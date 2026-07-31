from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.scripts.acceptance.reference_data_loop_acceptance import (
    ACTUAL_ROW_KEY,
    HELD_OUT_CELL_ID,
    PROFILE,
    SOURCE,
    TASK_ID,
    run_reference_data_loop,
)
from backend.scripts.generators.prepare_calce_battery_dataset import (
    require_writable_destination,
)
from material_workbench.app import create_app
from material_workbench.application.training_snapshot_adapter import (
    BATTERY_MATERIALIZATION_ADAPTER_ID,
    BATTERY_MATERIALIZATION_ADAPTER_VERSION,
    BATTERY_SOURCE_ADAPTER_ID,
    BATTERY_SOURCE_ADAPTER_VERSION,
    BATTERY_SOURCE_ROW_KEY,
    battery_row_key,
    battery_source_records,
)
from material_workbench.modeling.model_lifecycle import dataset_profile_digest
from material_workbench.contracts.data_lifecycle_contracts import (
    SourceConnector,
    SourceConnectorCreateInput,
)
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.packages.contracts import (
    ProvenanceSpec,
    SourceLifecycleProvenance,
)
from material_workbench.modeling.tabular_model_builder import (
    build_tabular_package_from_data,
)
from material_workbench.modeling.tabular.data import load_tabular_data
from material_workbench.persistence.data_lifecycle_repository import (
    DataLifecycleRepository,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.application.project_runtime import (
    ProjectRuntimeResolutionError,
)


def _source_digest() -> str:
    return hashlib.sha256(SOURCE.read_bytes()).hexdigest()


def test_battery_source_adapter_makes_the_composite_row_identity_explicit() -> None:
    records = battery_source_records(SOURCE)
    keys = [record[BATTERY_SOURCE_ROW_KEY] for record in records]

    assert len(records) == 3_131
    assert len(set(keys)) == len(keys)
    assert len({record["cell_id"] for record in records}) == 4
    assert ACTUAL_ROW_KEY in keys

    changed_ordinal = dict(records[0])
    changed_ordinal["cycle_index"] = "999999"
    assert battery_row_key(changed_ordinal) == keys[0]


def test_battery_derivation_cannot_write_beneath_source_root() -> None:
    with pytest.raises(ValueError, match="read-only source of truth"):
        require_writable_destination(SOURCE)


def test_source_adapter_fields_preserve_legacy_connector_digest() -> None:
    legacy_configuration = {
        "schema_version": "source-connector/v1",
        "name": "legacy connector",
        "connector_type": "object_storage_json_v1",
        "source_locator": "repository://legacy.json",
        "selection": {
            "schema_version": "object-selection/v1",
            "format": "json_array",
            "primary_key": "id",
            "included_fields": [],
        },
        "trigger_policy": "manual_only",
        "schedule": None,
    }
    connector = SourceConnector.model_validate(
        {
            **legacy_configuration,
            "id": "legacy",
            "configuration_digest": semantic_digest(
                legacy_configuration
            ),
            "created_at": "2026-07-27T00:00:00Z",
        }
    )

    assert connector.selection.source_adapter_id is None
    assert connector.calculated_configuration_digest == (
        semantic_digest(legacy_configuration)
    )
    assert SourceConnectorCreateInput.model_validate(
        legacy_configuration
    ).calculated_configuration_digest == (
        semantic_digest(legacy_configuration)
    )


def test_package_provenance_rejects_a_different_materialized_asset() -> None:
    digest = "sha256:" + "a" * 64
    lifecycle = SourceLifecycleProvenance(
        connector_id="connector",
        connector_configuration_digest=digest,
        source_adapter_id=BATTERY_SOURCE_ADAPTER_ID,
        source_adapter_version=BATTERY_SOURCE_ADAPTER_VERSION,
        raw_snapshot_id="raw",
        raw_snapshot_digest=digest,
        recipe_id="recipe",
        recipe_digest=digest,
        curation_run_id="curation",
        curation_digest=digest,
        profile_revision_id="profile",
        profile_digest=digest,
        canonical_dataset_revision_id="canonical",
        canonical_dataset_digest=digest,
        training_snapshot_id="training",
        training_snapshot_digest=digest,
        training_selection_policy_digest=digest,
        materialization_adapter_id=BATTERY_MATERIALIZATION_ADAPTER_ID,
        materialization_adapter_version=(
            BATTERY_MATERIALIZATION_ADAPTER_VERSION
        ),
        materialized_training_sha256="b" * 64,
        row_count=1,
    )

    with pytest.raises(ValidationError, match="training_data_id"):
        ProvenanceSpec(
            training_data_id="sha256:" + "c" * 64,
            feature_dataset_id=digest,
            training_code_revision="test",
            dataset_profile_id=digest,
            source_lifecycle=lifecycle,
        )


def test_package_builder_rejects_a_lifecycle_row_count_mismatch(
    tmp_path: Path,
) -> None:
    data = load_tabular_data(SOURCE, PROFILE)
    digest = "sha256:" + "a" * 64
    lifecycle = SourceLifecycleProvenance(
        connector_id="connector",
        connector_configuration_digest=digest,
        source_adapter_id=BATTERY_SOURCE_ADAPTER_ID,
        source_adapter_version=BATTERY_SOURCE_ADAPTER_VERSION,
        raw_snapshot_id="raw",
        raw_snapshot_digest=digest,
        recipe_id="recipe",
        recipe_digest=digest,
        curation_run_id="curation",
        curation_digest=digest,
        profile_revision_id="profile",
        profile_digest=dataset_profile_digest(PROFILE),
        canonical_dataset_revision_id="canonical",
        canonical_dataset_digest=digest,
        training_snapshot_id="training",
        training_snapshot_digest=digest,
        training_selection_policy_digest=digest,
        materialization_adapter_id=BATTERY_MATERIALIZATION_ADAPTER_ID,
        materialization_adapter_version=(
            BATTERY_MATERIALIZATION_ADAPTER_VERSION
        ),
        materialized_training_sha256=data.source_sha256,
        row_count=data.row_count + 1,
    )

    with pytest.raises(ValueError, match="does not match loaded tabular data"):
        build_tabular_package_from_data(
            data,
            PROFILE,
            tmp_path / "package",
            source_lifecycle=lifecycle,
        )


def test_battery_reference_loop_is_traceable_and_resume_safe(
    tmp_path: Path,
    app_resources,
) -> None:
    workspace = tmp_path / "reference-loop"
    before = _source_digest()

    first = run_reference_data_loop(workspace, resources=app_resources)
    second = run_reference_data_loop(workspace, resources=app_resources)

    assert second == first
    assert _source_digest() == before
    lifecycle = first["source_lifecycle"]
    assert lifecycle["row_count"] == 2_302
    assert lifecycle["materialization_adapter_id"] == (
        BATTERY_MATERIALIZATION_ADAPTER_ID
    )
    assert lifecycle["materialization_adapter_version"] == (
        BATTERY_MATERIALIZATION_ADAPTER_VERSION
    )
    assert lifecycle["source_adapter_id"] == BATTERY_SOURCE_ADAPTER_ID
    assert lifecycle["source_adapter_version"] == (
        BATTERY_SOURCE_ADAPTER_VERSION
    )
    assert (
        lifecycle["materialized_training_sha256"]
        == first["materialized_dataset"]["source_sha256"]
    )
    assert (
        first["model_package"]["manifest_digest"]
        == first["project"]["model_package_manifest_digest"]
    )
    assert first["candidate"]["source_row_key"] == ACTUAL_ROW_KEY
    assert first["candidate"]["source_kind"] == "decision_activity"
    assert first["actual"]["snapshot_id"] == first["comparison"]["snapshot_id"]
    assert first["actual"]["value"] != first["comparison"]["predicted"]
    identity = first["comparison"]["model_identity"]
    assert identity["package"]["manifest_sha256"] == (
        first["model_package"]["manifest_digest"]
    )
    assert identity["feature_pipeline"]["digest"].startswith("sha256:")
    assert identity["training_data"]["training_data_id"] == (
        f"sha256:{first['materialized_dataset']['source_sha256']}"
    )
    assert identity["training_data"]["feature_dataset_id"].startswith(
        "sha256:"
    )
    assert identity["training_data"]["training_code_revision"]

    repository = DataLifecycleRepository(workspace / "workbench.db")
    detail = repository.detail(lifecycle["connector_id"])
    assert len(detail.raw_snapshots) == 1
    assert len(detail.curation_runs) == 1
    assert len(detail.canonical_revisions) == 1
    assert len(detail.training_snapshots) == 1
    assert detail.training_snapshots[0].id == lifecycle["training_snapshot_id"]
    assert detail.raw_snapshots[0].row_count == 3_131
    assert detail.curation_runs[0].quality.quarantined == 0
    assert detail.canonical_revisions[0].approved_row_count == 3_131
    training_snapshot = repository.get_training_snapshot(
        lifecycle["training_snapshot_id"]
    )
    assert (
        training_snapshot.selection_policy_digest
        == lifecycle["training_selection_policy_digest"]
    )
    assert all(
        not key.startswith(f"{HELD_OUT_CELL_ID}|")
        for key in training_snapshot.included_row_keys
    )

    store = Store(workspace / "workbench.db")
    project_id = first["project"]["id"]
    candidate_id = first["candidate"]["id"]
    runs = store.list_decision_activity_runs(
        project_id,
        candidate_id=first["candidate"]["base_candidate_id"],
    )
    assert len(
        runs
    ) == 1
    assert runs[0]["id"] == first["activity"]["id"]
    assert len(store.list_snapshots(candidate_id)) == 1
    assert len(store.list_actuals(candidate_id)) == 1

    catalog = WorkspaceCatalog(workspace / "workbench.db")
    view = catalog.get_dataset_view_revision(
        first["project"]["dataset_view_revision_id"]
    )
    assert view is not None
    assert (
        view.members[0].provenance_json["source_lifecycle"]
        == lifecycle
    )
    package = catalog.get_model_package_ref(
        first["project"]["model_package_ref_id"]
    )
    assert package is not None
    assert package.manifest_json["provenance"]["source_lifecycle"] == lifecycle

    dataset_revision_id = view.members[0].dataset_revision_id
    forged_view = catalog.ensure_single_dataset_view(
        dataset_revision_id,
        name="forged lifecycle view",
        view_id="forged-lifecycle-view",
    )
    with TestClient(
        create_app(
            db_path=workspace / "workbench.db",
            data_library_path=workspace / "data-library",
            _resources=app_resources,
        )
    ) as client:
        rejected = client.post(
            "/api/projects",
            json={
                "name": "forged lifecycle project",
                "task_id": TASK_ID,
                "dataset_view_revision_id": forged_view.id,
                "model_package_ref_id": package.id,
            },
        )
        assert rejected.status_code == 422
        assert "学習元としていません" in rejected.text

    database = workspace / "workbench.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE actual_measurements SET mean=mean+1 WHERE id=?",
            (first["actual"]["id"],),
        )
    with pytest.raises(RuntimeError, match="Actual checkpoint differs"):
        run_reference_data_loop(workspace, resources=app_resources)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE actual_measurements SET mean=? WHERE id=?",
            (first["actual"]["value"], first["actual"]["id"]),
        )
        connection.execute(
            "UPDATE dataset_view_members SET provenance_json=? "
            "WHERE dataset_view_revision_id=?",
            (json.dumps({}), view.id),
        )

    with TestClient(
        create_app(
            db_path=database,
            data_library_path=workspace / "data-library",
            _resources=app_resources,
        )
    ) as client:
        project = client.app.state.store.get_project(project_id)
        assert project is not None
        with pytest.raises(
            ProjectRuntimeResolutionError,
            match="Source Lifecycle provenance",
        ):
            client.app.state.project_runtime_resolver.resolve(project)
