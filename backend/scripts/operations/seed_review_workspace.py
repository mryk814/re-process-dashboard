from __future__ import annotations

import argparse
from collections.abc import Callable
from hashlib import sha256
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from fastapi.testclient import TestClient

from decision_workbench.app import create_app
from decision_workbench.bootstrap.resources import (
    AppResources,
    prepare_app_resources,
)
from decision_workbench.application.workspace_bundle import (
    commit_workspace_restore,
    create_workspace_backup,
    finalize_workspace_restore,
    prepare_workspace_restore,
    rollback_workspace_restore,
)
from decision_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalogUnavailableError,
    load_deterministic_transform_catalog,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reset a development workspace from a reproducible seed bundle."
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--data-library", required=True)
    return parser


def seed_review_workspace(
    database: Path,
    data_library: Path,
    *,
    resources: AppResources | None = None,
    validate_readiness: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, object]:
    database = database.expanduser().resolve()
    data_library = data_library.expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    resources = resources or prepare_app_resources()
    try:
        transform_catalog = load_deterministic_transform_catalog()
    except DeterministicTransformCatalogUnavailableError:
        transform_catalog = None

    with tempfile.TemporaryDirectory(
        prefix="material-workbench-review-seed-"
    ) as temporary:
        root = Path(temporary)
        seed_database = root / "seed.db"
        seed_library = root / "seed-data-library"
        bundle = root / "review-seed.mdwb"

        with TestClient(
            create_app(
                db_path=seed_database,
                data_library_path=seed_library,
                _resources=resources,
            )
        ) as client:
            readiness = client.get("/api/readiness")
            readiness.raise_for_status()
            if not readiness.json()["ready"]:
                raise RuntimeError("seed workspace did not become ready")
        _normalize_seed_timestamps(seed_database)

        created = create_workspace_backup(
            database=seed_database,
            data_library_root=seed_library,
            destination=bundle,
            app_version="development-review-seed/v1",
        )
        prepared = prepare_workspace_restore(
            database=database,
            data_library_root=data_library,
            source=bundle,
            task_registry=resources.task_registry,
            transform_catalog=transform_catalog,
        )
        committed = commit_workspace_restore(
            database=database,
            data_library_root=data_library,
            restore_token=prepared.restore_token,
        )

        try:
            with TestClient(
                create_app(
                    db_path=database,
                    data_library_path=data_library,
                    _resources=resources,
                )
            ) as client:
                readiness = client.get("/api/readiness")
                readiness.raise_for_status()
                readiness_payload = readiness.json()
                if not readiness_payload["ready"]:
                    raise RuntimeError(
                        "restored review workspace did not become ready"
                    )
                if validate_readiness is not None:
                    validate_readiness(readiness_payload)
        except Exception:
            rollback_workspace_restore(
                database=database,
                data_library_root=data_library,
                restore_token=prepared.restore_token,
            )
            raise

        finalized = finalize_workspace_restore(
            database=database,
            restore_token=prepared.restore_token,
        )
        return {
            "schema_version": "review-workspace-seed/v1",
            "status": "seeded",
            "database": str(database),
            "data_library": str(data_library),
            "bundle_size_bytes": created.size_bytes,
            "restore_status": committed.status,
            "finalize_status": finalized.status,
            "seed_content_digest": review_seed_content_digest(database),
            "readiness": readiness_payload,
        }


def review_seed_content_digest(database: Path) -> str:
    """Identify stable reader-visible seed content, including IDs and timestamps."""

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        projects = [
            {
                "id": row["id"],
                "name": row["name"],
                "task_id": row["task_id"],
                "target_values": json.loads(row["target_values"]),
                "scientific_identity": json.loads(row["scientific_identity_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in connection.execute(
                "SELECT id,name,task_id,target_values,scientific_identity_json,"
                "created_at,updated_at "
                "FROM projects ORDER BY id"
            )
        ]
        candidates = []
        for row in connection.execute(
            "SELECT id,project_id,name,payload,created_at,updated_at FROM candidates "
            "WHERE archived_at IS NULL ORDER BY project_id,name"
        ):
            payload = json.loads(row["payload"])
            candidates.append(
                {
                    "id": row["id"],
                    "project_id": row["project_id"],
                    "name": row["name"],
                    "inputs": payload.get("inputs"),
                    "editor_state": payload.get("editor_state"),
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            )
    finally:
        connection.close()
    canonical = json.dumps(
        {"projects": projects, "candidates": candidates},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_seed_timestamps(database: Path) -> None:
    """Make generated seed identities and timestamps stable for review."""

    fixed = "2026-01-01T00:00:00+00:00"
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA foreign_keys=OFF")
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        table_columns: dict[str, list[tuple[str, str]]] = {}
        for table in tables:
            escaped_table = table.replace('"', '""')
            table_columns[table] = [
                (str(row[1]), str(row[2]))
                for row in connection.execute(
                    f'PRAGMA table_info("{escaped_table}")'
                )
            ]
        candidates = connection.execute(
            "SELECT id,project_id,name FROM candidates ORDER BY project_id,name"
        ).fetchall()
        for old_id, project_id, name in candidates:
            new_id = str(
                uuid5(
                    NAMESPACE_URL,
                    "material-workbench-review-seed/v1/"
                    f"candidate/{project_id}/{name}",
                )
            )
            for table, columns in table_columns.items():
                escaped_table = table.replace('"', '""')
                for column, column_type in columns:
                    if table == "candidates" and column == "id":
                        continue
                    if "TEXT" not in column_type.upper():
                        continue
                    escaped_column = column.replace('"', '""')
                    connection.execute(
                        f'UPDATE "{escaped_table}" SET "{escaped_column}"=? '
                        f'WHERE "{escaped_column}"=?',
                        (new_id, old_id),
                    )
            connection.execute(
                "UPDATE candidates SET id=? WHERE id=?",
                (new_id, old_id),
            )
        for table in tables:
            escaped_table = table.replace('"', '""')
            columns = {column for column, _ in table_columns[table]}
            for column in ("created_at", "updated_at", "applied_at"):
                if column in columns:
                    connection.execute(
                        f'UPDATE "{escaped_table}" SET "{column}"=?',
                        (fixed,),
                    )
        connection.commit()
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(
                f"seed identity normalization broke references: {violations}"
            )
    finally:
        connection.close()


def main() -> int:
    arguments = _parser().parse_args()
    result = seed_review_workspace(
        Path(arguments.database),
        Path(arguments.data_library),
    )
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
