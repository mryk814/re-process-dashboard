from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from material_workbench.app import _AppResources, create_app


def test_startup_registers_runtime_resources_and_binds_projects(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        projects = {item["id"]: item for item in client.get("/api/projects").json()}
        catalog = client.app.state.workspace_catalog

        assert projects["default"]["binding_provenance"] == "assumed_current_at_upgrade"
        assert projects["hot-rolling-default"]["binding_provenance"] == "assumed_current_at_upgrade"
        assert projects["default"]["dataset_view_revision_id"] == projects["hot-rolling-default"]["dataset_view_revision_id"]
        assert projects["default"]["model_package_ref_id"] != projects["hot-rolling-default"]["model_package_ref_id"]
        assert projects["default"]["project_series_id"] != projects["hot-rolling-default"]["project_series_id"]
        assert len(catalog.list_data_assets()) == 8
        assert len(catalog.list_dataset_revisions()) == 8
        assert len(catalog.list_dataset_view_revisions()) == 8
        assert len(catalog.list_model_package_refs()) == 14


def test_bootstrap_is_idempotent_and_preserves_first_binding(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(create_app(db_path=database, _resources=app_resources)) as first_client:
        first = first_client.get("/api/projects/default").json()
        first_counts = (
            len(first_client.app.state.workspace_catalog.list_data_assets()),
            len(first_client.app.state.workspace_catalog.list_profile_revisions()),
            len(first_client.app.state.workspace_catalog.list_dataset_revisions()),
            len(first_client.app.state.workspace_catalog.list_model_package_refs()),
        )

    with TestClient(create_app(db_path=database, _resources=app_resources)) as second_client:
        second = second_client.get("/api/projects/default").json()
        second_counts = (
            len(second_client.app.state.workspace_catalog.list_data_assets()),
            len(second_client.app.state.workspace_catalog.list_profile_revisions()),
            len(second_client.app.state.workspace_catalog.list_dataset_revisions()),
            len(second_client.app.state.workspace_catalog.list_model_package_refs()),
        )

    assert second_counts == first_counts
    assert second["dataset_view_revision_id"] == first["dataset_view_revision_id"]
    assert second["model_package_manifest_digest"] == first["model_package_manifest_digest"]
    assert second["binding_migrated_at"] == first["binding_migrated_at"]


def test_bootstrap_refreshes_stale_explicit_bundled_tutorial_binding(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        current = client.get("/api/projects/default").json()

    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE projects SET task_contract_digest='stale',binding_provenance='explicit' "
            "WHERE id='default'"
        )

    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        refreshed = client.get("/api/projects/default").json()
        assert refreshed["dataset_view_revision_id"] == current["dataset_view_revision_id"]
        assert refreshed["task_contract_digest"] == current["task_contract_digest"]
        assert client.get("/api/projects/default/lineage/AN-01").status_code == 200


def test_bootstrap_archives_unreferenced_package_ref_after_locator_rebuild(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(create_app(db_path=database, _resources=app_resources)):
        pass

    with sqlite3.connect(database) as conn:
        current = conn.execute(
            "SELECT * FROM model_package_refs WHERE id=("
            "SELECT model_package_ref_id FROM projects WHERE id='default')"
        ).fetchone()
        assert current is not None
        conn.execute(
            "INSERT INTO model_package_refs("
            "id,package_id,task_id,task_contract_digest,manifest_digest,locator,"
            "manifest_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                "stale-package-ref",
                "stale-tutorial-package",
                current[2],
                current[3],
                "0" * 64,
                current[5],
                "{}",
                datetime.now(UTC).isoformat(),
            ),
        )

    with TestClient(create_app(db_path=database, _resources=app_resources)):
        pass
    with sqlite3.connect(database) as conn:
        archived_at = conn.execute(
            "SELECT archived_at FROM model_package_refs WHERE id='stale-package-ref'"
        ).fetchone()[0]
        assert archived_at is not None


def test_bootstrap_reuses_digest_equivalent_legacy_profile_json(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        before = len(client.app.state.workspace_catalog.list_profile_revisions())

    with sqlite3.connect(database) as conn:
        row = conn.execute(
            "SELECT id,effective_profile_json FROM dataset_profile_revisions "
            "WHERE profile_id='thin-sheet-tutorial-v1'"
        ).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        shared = payload["shared"]
        removed = False
        for key in ("policy_defaults", "optional_roles", "optional_technical_fields"):
            if not shared.get(key):
                removed = shared.pop(key, None) is not None or removed
        assert removed
        conn.execute(
            "UPDATE dataset_profile_revisions SET effective_profile_json=? WHERE id=?",
            (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), row[0]),
        )

    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        assert client.get("/api/health").json()["ok"] is True
        assert client.get("/api/projects/default/lineage?limit=1").status_code == 200
        assert len(client.app.state.workspace_catalog.list_profile_revisions()) == before
