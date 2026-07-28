from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from material_workbench.app import create_app
from material_workbench.developer_experience.workspace_preflight import (
    CurrentWorkspacePreflightRegistry,
    inspect_workspace_compatibility,
)
from material_workbench.persistence.sqlite_connection import initialize_sqlite


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _manifest_only_active_config(tmp_path: Path) -> Path:
    source_config = Path("models/active-packages.json")
    payload = json.loads(source_config.read_text(encoding="utf-8"))
    source_root = source_config.parent
    for selection in payload["tasks"].values():
        relative = Path(selection["active"])
        destination = tmp_path / relative / "manifest.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((source_root / relative / "manifest.json").read_bytes())
    config = tmp_path / "active-packages.json"
    config.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return config


def test_current_workspace_preflight_registry_matches_production_identities(
    tmp_path: Path,
    app_resources,
) -> None:
    registry = CurrentWorkspacePreflightRegistry(
        active_packages_path=_manifest_only_active_config(tmp_path)
    )

    assert registry.available_task_ids == app_resources.task_registry.available_task_ids
    for task_id in registry.available_task_ids:
        lightweight = registry.entry_for(task_id).model_package
        production = app_resources.task_registry.entry_for(task_id).model_package
        assert lightweight.manifest == production.manifest
        assert lightweight.manifest_sha256 == production.manifest_sha256


def test_current_workspace_preflight_registry_skips_wrong_task_package(
    tmp_path: Path,
) -> None:
    config = _manifest_only_active_config(tmp_path)
    payload = json.loads(config.read_text(encoding="utf-8"))
    annealed = tmp_path / payload["tasks"]["annealed-properties-v1"]["active"] / "manifest.json"
    hot_rolling = tmp_path / payload["tasks"]["hot-rolled-properties-v1"]["active"] / "manifest.json"
    annealed.write_bytes(hot_rolling.read_bytes())

    registry = CurrentWorkspacePreflightRegistry(active_packages_path=config)

    assert "annealed-properties-v1" not in registry.available_task_ids
    assert "hot-rolled-properties-v1" in registry.available_task_ids


def test_workspace_preflight_accepts_missing_database_without_creating_it(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "missing.db"

    report = inspect_workspace_compatibility(
        database,
        app_resources.task_registry,
    )

    assert report.status == "ok"
    assert report.database_exists is False
    assert not database.exists()


def test_workspace_preflight_allows_supported_legacy_schema_migration(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "legacy.db"
    initialize_sqlite(database)

    report = inspect_workspace_compatibility(
        database,
        app_resources.task_registry,
    )

    assert report.status == "ok"
    assert report.code == 0
    assert report.database_exists is True


def test_workspace_preflight_detects_catalog_conflict_without_writing(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ) as client:
        assert client.get("/api/readiness").json()["ready"] is True

    with sqlite3.connect(database) as connection:
        package = connection.execute(
            "SELECT id FROM model_package_refs "
            "WHERE archived_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        assert package is not None
        connection.execute(
            "UPDATE model_package_refs SET task_contract_digest=? WHERE id=?",
            ("sha256:" + "0" * 64, package[0]),
        )

    before = _digest(database)
    report = inspect_workspace_compatibility(
        database,
        app_resources.task_registry,
    )
    after = _digest(database)

    assert report.status == "error"
    assert report.code == 1
    assert before == after
    finding = next(item for item in report.findings if item.stage == "catalog")
    assert finding.resource_id
    assert finding.registered_digest == "sha256:" + "0" * 64
    assert finding.current_digest
    assert "別workspace" in finding.recovery_hint


def test_workspace_preflight_does_not_misclassify_invalid_package_as_catalog_conflict(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass
    lightweight = CurrentWorkspacePreflightRegistry(
        active_packages_path=_manifest_only_active_config(tmp_path / "packages")
    )
    package = lightweight.entry_for(lightweight.available_task_ids[0]).model_package
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE model_package_refs SET manifest_json=? "
            "WHERE package_id=? AND manifest_digest=?",
            ("{}", package.manifest.package_id, package.manifest_sha256),
        )

    report = inspect_workspace_compatibility(database, lightweight)

    assert not any(
        item.stage == "catalog"
        and item.resource_id == package.manifest.package_id
        for item in report.findings
    )


def test_workspace_preflight_reports_broken_project_binding(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "UPDATE projects SET model_package_ref_id='missing-package-ref' "
            "WHERE id='default'"
        )

    report = inspect_workspace_compatibility(
        database,
        app_resources.task_registry,
    )

    finding = next(
        item
        for item in report.findings
        if item.stage == "project_binding" and item.resource_id == "default"
    )
    assert "missing-package-ref" in finding.cause
    assert finding.impact
    assert finding.recovery_hint


def test_workspace_preflight_rejects_malformed_project_identity(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE projects SET scientific_identity_json='{' WHERE id='default'"
        )

    report = inspect_workspace_compatibility(
        database,
        app_resources.task_registry,
    )

    assert report.status == "error"
    assert any(
        item.resource_id == "default"
        and "契約" in item.cause
        for item in report.findings
    )


def test_workspace_preflight_rejects_partial_single_task_identity(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE projects SET scientific_identity_json=? WHERE id='default'",
            (
                '{"identity_kind":"single_task","task_id":"annealed-properties-v1",'
                '"binding_provenance":"explicit",'
                '"task_contract_digest":"sha256:'
                + "0" * 64
                + '"}',
            ),
        )

    report = inspect_workspace_compatibility(
        database,
        app_resources.task_registry,
    )

    assert report.status == "error"
    assert any(
        item.resource_id == "default"
        and "契約" in item.cause
        for item in report.findings
    )


def test_workspace_preflight_rejects_identity_task_mirror_mismatch(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT scientific_identity_json FROM projects WHERE id='default'"
        ).fetchone()
        assert row is not None
        identity = json.loads(row[0])
        identity["task_id"] = "wrong-task"
        connection.execute(
            "UPDATE projects SET scientific_identity_json=? WHERE id='default'",
            (json.dumps(identity),),
        )

    report = inspect_workspace_compatibility(
        database,
        app_resources.task_registry,
    )

    assert report.status == "error"
    assert any(
        item.resource_id == "default"
        and "task_id" in item.cause
        for item in report.findings
    )


def test_workspace_preflight_rejects_missing_current_chain_catalog_table(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(
        create_app(db_path=database, _resources=app_resources)
    ):
        pass
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("DROP TABLE chain_revisions")

    report = inspect_workspace_compatibility(
        database,
        app_resources.task_registry,
    )

    assert report.status == "error"
    assert any(
        item.stage == "chain_binding"
        and item.resource_id == "chain-catalog-v1"
        for item in report.findings
    )
