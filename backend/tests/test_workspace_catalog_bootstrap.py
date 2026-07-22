from __future__ import annotations

from pathlib import Path

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
        assert len(catalog.list_data_assets()) == 2
        assert len(catalog.list_dataset_revisions()) == 2
        assert len(catalog.list_dataset_view_revisions()) == 2
        assert len(catalog.list_model_package_refs()) == 3


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
