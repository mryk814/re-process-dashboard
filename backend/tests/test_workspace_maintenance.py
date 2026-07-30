from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import create_app
from material_workbench.developer_experience.workspace_maintenance import (
    deactivate_package_registration,
    inspect_package_registrations,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import (
    CatalogConflictError,
    CatalogReferenceError,
    WorkspaceCatalog,
)
from material_workbench.application.workspace_catalog_bootstrap import (
    bootstrap_workspace_catalog,
)


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def test_maintenance_inspect_is_read_only(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass

    before = _digest(database)
    registrations = inspect_package_registrations(database)
    after = _digest(database)

    assert registrations
    assert before == after
    assert any(item["referenced_by"] for item in registrations)
    assert all(item["task_contract_digest"] for item in registrations)


def test_maintenance_refuses_referenced_package(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass
    registration = next(
        item
        for item in inspect_package_registrations(database)
        if item["referenced_by"]
    )

    with pytest.raises(CatalogReferenceError, match="保存済み証拠から参照"):
        deactivate_package_registration(
            database,
            reference_id=registration["id"],
            reason="test must be refused",
        )

    current = WorkspaceCatalog(database).get_model_package_ref(
        registration["id"],
        include_archived=True,
    )
    assert current is not None
    assert current.archived_at is None


def test_explicit_unreferenced_deactivation_recovers_catalog_conflict(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    bootstrap_workspace_catalog(database, app_resources.task_registry)
    catalog = WorkspaceCatalog(database)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT id FROM model_package_refs ORDER BY id LIMIT 1"
        ).fetchone()
        assert row is not None
        reference_id = row[0]
        connection.execute(
            "UPDATE model_package_refs SET task_contract_digest=? WHERE id=?",
            ("sha256:" + "0" * 64, reference_id),
        )

    with pytest.raises(CatalogConflictError, match="別内容で登録済み"):
        with TestClient(
            create_app(db_path=database, _resources=app_resources)
        ):
            pass

    result = deactivate_package_registration(
        database,
        reference_id=reference_id,
        reason="現行Task contractへ明示的に再登録するため",
    )
    assert result["status"] == "deactivated"
    assert result["audit_event"]["operation"] == "deactivate"
    assert result["audit_event"]["detail"]["task_contract_digest"] == (
        "sha256:" + "0" * 64
    )

    root = Path(__file__).resolve().parents[2]
    preflight = subprocess.run(
        ["node", "scripts/dev-launcher.mjs", "--preflight-only"],
        cwd=root,
        env={**os.environ, "WORKBENCH_DB_PATH": str(database)},
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert preflight.returncode == 0
    report = json.loads(preflight.stdout.splitlines()[-1])
    assert report["status"] == "ok"
    assert any(item["severity"] == "warning" for item in report["findings"])

    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ) as recovered:
        assert recovered.get("/api/readiness").json()["ready"] is True

    catalog = WorkspaceCatalog(database)
    restored = catalog.get_model_package_ref(
        reference_id,
        include_archived=True,
    )
    assert restored is not None
    assert restored.archived_at is None
    events = catalog.list_workspace_maintenance_events()
    assert [event["operation"] for event in events] == [
        "deactivate",
        "reactivate-current-contract",
    ]
    assert events[-1]["detail"]["deactivation_event_id"] == events[0]["id"]


@pytest.mark.parametrize(
    ("evidence_kind", "expected_label"),
    [
        ("snapshot", "Evidence: snapshots.payload"),
        ("chain_memo", "Evidence: chain_stage_memo.package_manifest_digest"),
    ],
)
def test_maintenance_refuses_package_referenced_by_saved_evidence(
    tmp_path: Path,
    app_resources,
    evidence_kind: str,
    expected_label: str,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    bootstrap_workspace_catalog(database, app_resources.task_registry)
    catalog = WorkspaceCatalog(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        package = connection.execute(
            "SELECT * FROM model_package_refs ORDER BY id LIMIT 1"
        ).fetchone()
        assert package is not None
        if evidence_kind == "snapshot":
            connection.execute(
                "INSERT INTO snapshots(id,candidate_id,payload,created_at) "
                "VALUES (?,?,?,?)",
                (
                    "snapshot-evidence",
                    "historical-candidate",
                    json.dumps(
                        {"model_package_manifest_digest": package["manifest_digest"]}
                    ),
                    "2026-01-01",
                ),
            )
        else:
            connection.execute(
                "INSERT INTO chain_stage_memo("
                "memo_key,stage_id,input_digest,contract_digest,"
                "package_manifest_digest,canonical_input_json,result_json,created_at"
                ") VALUES (?,?,?,?,?,?,?,?)",
                (
                    "memo-evidence",
                    "stage-evidence",
                    "input-digest",
                    "contract-digest",
                    package["manifest_digest"],
                    "{}",
                    "{}",
                    "2026-01-01",
                ),
            )
        reference_id = package["id"]

    with pytest.raises(CatalogReferenceError, match=expected_label):
        catalog.deactivate_model_package_ref_for_maintenance(
            reference_id,
            reason="must be rejected",
        )


def test_deactivation_approval_is_consumed_after_reactivation(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    bootstrap_workspace_catalog(database, app_resources.task_registry)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT id FROM model_package_refs ORDER BY id LIMIT 1"
        ).fetchone()
        assert row is not None
        reference_id = row[0]
        connection.execute(
            "UPDATE model_package_refs SET task_contract_digest=? WHERE id=?",
            ("sha256:" + "0" * 64, reference_id),
        )

    deactivate_package_registration(
        database,
        reference_id=reference_id,
        reason="one-time recovery",
    )
    bootstrap_workspace_catalog(database, app_resources.task_registry)
    WorkspaceCatalog(database).set_model_package_ref_availability(
        reference_id,
        archived=True,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE model_package_refs SET task_contract_digest=? WHERE id=?",
            ("sha256:" + "f" * 64, reference_id),
        )

    with pytest.raises(CatalogConflictError, match="別内容で登録済み"):
        bootstrap_workspace_catalog(database, app_resources.task_registry)


def test_maintenance_refuses_reference_kept_only_in_project_identity(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass
    catalog = WorkspaceCatalog(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        project = connection.execute(
            "SELECT id,model_package_ref_id FROM projects "
            "WHERE model_package_ref_id IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
        assert project is not None
        reference_id = project["model_package_ref_id"]
        connection.execute(
            "UPDATE projects SET model_package_ref_id=NULL,"
            "model_package_manifest_digest='detached-manifest' WHERE id=?",
            (project["id"],),
        )

    with pytest.raises(
        CatalogReferenceError,
        match="Evidence: projects.scientific_identity_json",
    ):
        catalog.deactivate_model_package_ref_for_maintenance(
            reference_id,
            reason="identity reference must win",
        )
