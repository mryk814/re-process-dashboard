from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest

from material_workbench.app import _AppResources, create_app
from material_workbench.contracts.schemas import ModelPackageRefCreateInput
from material_workbench.data.dataset_registration import (
    file_sha256,
    register_dataset_records,
)
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.persistence.workspace_catalog_bootstrap import (
    REPLACED_MODEL_PACKAGE_IDS,
    WorkspaceCatalogBootstrapError,
    audit_project_bindings,
    migrate_replaced_model_package_projects,
)


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_ASSET_FILENAMES = {
    "material_workbench_tutorial_v2.xlsx",
    "material_workbench_process_v1.xlsx",
    "cutting_tool_flank_wear_synthetic_dataset.xlsx",
    "heat_treatment_tradeoff_samples.csv",
    "concrete_mix_samples.csv",
    "wear_curve_samples.csv",
    "battery_calce_cs2_cycles.csv",
    "secom_stress.csv",
    "mpea_ground_truth_18021833.csv",
    "welding_consumable_multistage_synthetic_dataset.xlsx",
}
EXPECTED_PROFILE_IDS = {
    "thin-sheet-tutorial-v1",
    "material-workbench-process-v1",
    "cutting-flank-wear-v1",
    "external-heat-treatment-v1",
    "external-concrete-v1",
    "external-wear-curve-v1",
    "calce-cs2-battery-capacity-v1",
    "uci-secom-yield-v1",
    "mpea-zenodo-18021833-v1",
    "mpea-zenodo-18021833-room-tensile-v1",
    "mpea-zenodo-18021833-hardness-v1",
    "welding-consumable-stage-b-v1",
    "welding-consumable-stage-c-observations-v1",
}
EXPECTED_MODEL_PACKAGES = {
    ("annealed-properties-v1", "annealed-gp-stable-ard-process-v2"),
    ("annealed-properties-v1", "annealed-gp-stable-ard-tutorial-v2"),
    ("annealed-properties-v1", "annealed-heteroscedastic-gp-process-v2"),
    ("annealed-properties-v1", "annealed-hierarchical-bayes-process-v2"),
    ("annealed-properties-v1", "annealed-lightgbm-standard-process-v2"),
    ("annealed-properties-v1", "annealed-lightgbm-standard-tutorial-v2"),
    ("battery-degradation-v1", "battery-degradation-lightgbm-calce-v1"),
    ("concrete-strength-v1", "concrete-strength-ridge-external-v1"),
    ("flank-wear-v1", "flank-wear-gp-2026-07"),
    ("heat-treatment-tradeoff-v1", "heat-treatment-ridge-external-v1"),
    ("hot-rolled-properties-v1", "hot-rolled-horseshoe-process-v2"),
    ("hot-rolled-properties-v1", "hot-rolled-tutorial-v2"),
    ("mpea-hardness-process-v1", "mpea-hardness-ridge-v1"),
    ("mpea-literature-tys-v1", "mpea-literature-tys-ridge-v1"),
    ("mpea-room-tensile-v1", "mpea-room-tensile-ridge-v1"),
    ("secom-yield-risk-v1", "secom-yield-lightgbm-calibrated-v1"),
    ("wear-curve-v1", "wear-curve-ridge-external-v1"),
    ("welding-consumable-stage-b-v1", "welding-consumable-stage-b-ridge-v1"),
    ("welding-stage-c-properties-v1", "welding-stage-c-ridge-v1"),
}


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
        assert projects["default"]["project_series_id"] is None
        assert projects["hot-rolling-default"]["project_series_id"] is None
        assert len(catalog.list_data_assets()) == len(EXPECTED_ASSET_FILENAMES)
        assert {
            asset.original_filename
            for asset in catalog.list_data_assets()
        } == EXPECTED_ASSET_FILENAMES
        assert len(catalog.list_profile_revisions()) == len(EXPECTED_PROFILE_IDS)
        assert {
            profile.profile_id
            for profile in catalog.list_profile_revisions()
        } == EXPECTED_PROFILE_IDS
        datasets = catalog.list_dataset_revisions()
        views = catalog.list_dataset_view_revisions()
        assert len(datasets) == len(EXPECTED_PROFILE_IDS)
        assert len(views) == len(datasets)
        assert {
            member.dataset_revision_id
            for view in views
            for member in view.members
        } == {dataset.id for dataset in datasets}
        assert all(view.kind == "single" and len(view.members) == 1 for view in views)
        assert len(catalog.list_model_package_refs()) == len(EXPECTED_MODEL_PACKAGES)
        assert {
            (package.task_id, package.package_id)
            for package in catalog.list_model_package_refs()
        } == EXPECTED_MODEL_PACKAGES

    audit_project_bindings(database)
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE projects SET project_series_id='missing-series' "
            "WHERE id='default'"
        )
    with pytest.raises(WorkspaceCatalogBootstrapError, match="Project Series"):
        audit_project_bindings(database)


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


def test_bootstrap_upgrades_a_project_pinned_to_the_previous_tutorial_package(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        current = client.get("/api/projects/default").json()
        catalog = client.app.state.workspace_catalog
        old_source = ROOT / "data/source/material_workbench_tutorial_v1.xlsx"
        old_dataset = register_dataset_records(
            catalog=catalog,
            source_path=old_source,
            source_sha256=file_sha256(old_source),
            profile_path=(
                ROOT
                / "backend/src/material_workbench/data/dataset-input-profile-tutorial.json"
            ),
            locator_kind="bundled",
            locator=old_source,
            name=old_source.stem,
        )
        old_package = ModelPackageLoader().load(
            ROOT / "models/packages/annealed-gp-stable-ard-tutorial-v1"
        )
        old_package_ref = catalog.upsert_model_package_ref(
            ModelPackageRefCreateInput(
                package_id=old_package.manifest.package_id,
                task_id=old_package.manifest.task_id,
                task_contract_digest="previous-task-contract",
                manifest_digest=old_package.manifest_sha256,
                locator=str(old_package.root),
                manifest_json=old_package.manifest.model_dump(mode="json"),
            )
        )

    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE projects SET dataset_view_revision_id=?,task_contract_digest=?,"
            "model_package_ref_id=?,model_package_manifest_digest=?,"
            "binding_provenance='explicit' WHERE id='default'",
            (
                old_dataset.dataset_view_revision_id,
                "previous-task-contract",
                old_package_ref.id,
                old_package.manifest_sha256,
            ),
        )

    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        refreshed = client.get("/api/projects/default").json()
        assert refreshed["dataset_view_revision_id"] == current["dataset_view_revision_id"]
        assert refreshed["task_contract_digest"] == current["task_contract_digest"]
        assert refreshed["model_package_ref_id"] == current["model_package_ref_id"]
        assert (
            refreshed["model_package_manifest_digest"]
            == current["model_package_manifest_digest"]
        )
        assert client.get("/api/projects/default/model-package").json()["id"] == (
            "annealed-gp-stable-ard-tutorial-v2"
        )
        assert client.get("/api/projects/default/lineage/AN-01").status_code == 200


def test_every_replaced_package_id_has_an_explicit_project_upgrade(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        catalog = client.app.state.workspace_catalog
        for previous_id in REPLACED_MODEL_PACKAGE_IDS:
            package = ModelPackageLoader().load(ROOT / "models/packages" / previous_id)
            catalog.upsert_model_package_ref(
                ModelPackageRefCreateInput(
                    package_id=previous_id,
                    task_id=package.manifest.task_id,
                    task_contract_digest="previous-task-contract",
                    manifest_digest=package.manifest_sha256,
                    locator=str(package.root),
                    manifest_json=package.manifest.model_dump(mode="json"),
                )
            )

    for previous_id, current_id in REPLACED_MODEL_PACKAGE_IDS.items():
        project_id = (
            "hot-rolling-default"
            if previous_id.startswith("hot-rolled-")
            else "default"
        )
        with sqlite3.connect(database) as conn:
            old_ref = conn.execute(
                "SELECT id,manifest_digest FROM model_package_refs WHERE package_id=?",
                (previous_id,),
            ).fetchone()
            assert old_ref is not None
            conn.execute(
                "UPDATE projects SET task_contract_digest='previous-task-contract',"
                "model_package_ref_id=?,model_package_manifest_digest=? WHERE id=?",
                (old_ref[0], old_ref[1], project_id),
            )

        assert migrate_replaced_model_package_projects(database) == 1
        with sqlite3.connect(database) as conn:
            upgraded = conn.execute(
                "SELECT m.package_id,p.task_contract_digest,m.task_contract_digest,"
                "p.model_package_manifest_digest,m.manifest_digest "
                "FROM projects p JOIN model_package_refs m ON m.id=p.model_package_ref_id "
                "WHERE p.id=?",
                (project_id,),
            ).fetchone()
        assert upgraded is not None
        assert upgraded[0] == current_id
        assert upgraded[1] == upgraded[2]
        assert upgraded[3] == upgraded[4]


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


def test_bootstrap_migrates_only_the_replaced_three_output_mpea_binding(
    tmp_path: Path,
    app_resources: _AppResources,
) -> None:
    database = tmp_path / "workbench.db"
    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        current = next(
            item for item in client.get("/api/projects").json()
            if item["task_id"] == "mpea-room-tensile-v1"
        )

    old_manifest = "a5c116ca97f84c8ba9f3731531387773d3c6d7448116bcf3a9a77ba7a0e052b0"
    legacy_id = "legacy-mpea-room-project"
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE projects SET id=? WHERE id=?", (legacy_id, current["id"]))
        conn.execute("UPDATE candidates SET project_id=? WHERE project_id=?", (legacy_id, current["id"]))
        conn.execute(
            "INSERT INTO model_package_refs(id,package_id,task_id,task_contract_digest,manifest_digest,locator,manifest_json,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                "replaced-mpea-room-package",
                "mpea-room-tensile-ridge-v1",
                "mpea-literature-tys-v1",
                "sha256:00b438d60f3903fc99fdcc1d4d7c93961438e04f5459d1207621b5b686df7ab0",
                old_manifest,
                "models/packages/mpea-room-tensile-ridge-v1",
                "{}",
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.execute(
            "UPDATE projects SET task_id='mpea-literature-tys-v1',task_contract_digest=?,"
            "model_package_ref_id='replaced-mpea-room-package',model_package_manifest_digest=? WHERE id=?",
            ("sha256:00b438d60f3903fc99fdcc1d4d7c93961438e04f5459d1207621b5b686df7ab0", old_manifest, legacy_id),
        )

    with TestClient(create_app(db_path=database, _resources=app_resources)) as client:
        migrated = client.get(f"/api/projects/{legacy_id}").json()
        assert migrated["task_id"] == "mpea-room-tensile-v1"
        assert migrated["model_package_manifest_digest"] != old_manifest
