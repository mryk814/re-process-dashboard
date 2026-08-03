from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path

import pytest

from decision_workbench.application import training_snapshot_adapter
from decision_workbench.application.data_lifecycle import DataLifecycleService
from decision_workbench.application.training_snapshot_adapter import (
    TABULAR_MATERIALIZATION_ADAPTER_ID,
    TrainingSnapshotMaterializationAvailable,
    TrainingSnapshotMaterializationRequest,
    TrainingSnapshotMaterializationUnavailable,
    training_snapshot_materializer_registry,
)
from decision_workbench.contracts.data_library_contracts import (
    ProfileRevisionCreateInput,
)
from decision_workbench.contracts.data_lifecycle_contracts import (
    CurationRecipeCreateInput,
    CurationRunCreateInput,
    DatasetApprovalInput,
    ObjectSelection,
    SourceConnectorCreateInput,
    SourceFetchRequest,
    TrainingSnapshotCreateInput,
)
from decision_workbench.modeling.model_lifecycle import dataset_profile_digest
from decision_workbench.modeling.tabular.profile import TabularDatasetProfile
from decision_workbench.persistence.data_lifecycle_repository import (
    DataLifecycleRepository,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog


def _tabular_profile() -> TabularDatasetProfile:
    return TabularDatasetProfile.model_validate(
        {
            "schema_version": "tabular-dataset-profile/v1",
            "profile_id": "second-tabular-fixture-v1",
            "name": "Second tabular materialization fixture",
            "task_id": "second-tabular-task-v1",
            "package_id": "second-tabular-package-v1",
            "id_column": "id",
            "group_column": "group",
            "model_family": "ridge",
            "ridge_alpha": 1.0,
            "inputs": [
                {
                    "path": "process.x",
                    "column": "x",
                    "kind": "number",
                    "transform": "linear",
                }
            ],
            "outputs": [
                {
                    "key": "target",
                    "column": "target",
                    "unit": "unit",
                }
            ],
        }
    )


def _lifecycle_fixture(
    tmp_path: Path,
    *,
    split_group_field: str = "group",
) -> tuple[
    DataLifecycleRepository,
    WorkspaceCatalog,
    str,
    str,
    object,
]:
    database = tmp_path / "workbench.db"
    Store(database)
    catalog = WorkspaceCatalog(database)
    profile = _tabular_profile()
    profile_revision = catalog.upsert_profile_revision(
        ProfileRevisionCreateInput(
            profile_id=profile.profile_id,
            revision=1,
            name=profile.name,
            profile_digest=dataset_profile_digest(profile),
            canonical_contract_digest="sha256:second-tabular-contract",
            effective_profile_json=profile.model_dump(mode="json"),
        )
    )
    service = DataLifecycleService(database)
    connector = service.create_connector(
        SourceConnectorCreateInput(
            name="Second tabular fixture",
            connector_type="object_storage_json_v1",
            source_locator="repository://second-tabular.json",
            selection=ObjectSelection(
                format="json_array",
                primary_key="id",
                source_adapter_id="second-tabular-json-records",
                source_adapter_version="1.0.0",
            ),
        )
    )
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content=json.dumps(
                [
                    {"id": "A", "group": "g1", "x": 1, "target": 10},
                    {"id": "B", "group": "g2", "x": 2, "target": 20},
                    {"id": "C", "group": "g3", "x": 3, "target": 30},
                    {"id": "D", "group": "g4", "x": 4, "target": 40},
                ]
            ),
            object_version="fixture-v1",
        ),
    )
    recipe = service.create_recipe(
        CurationRecipeCreateInput(
            recipe_id="second-tabular-curation",
            version=1,
            name="Second tabular curation",
            steps=(
                {
                    "kind": "trim_strings_v1",
                    "fields": ["id", "group"],
                },
                {
                    "kind": "coerce_number_v1",
                    "fields": ["x", "target"],
                },
                {
                    "kind": "required_fields_v1",
                    "fields": ["id", "group", "x"],
                },
                {
                    "kind": "target_eligibility_v1",
                    "fields": ["target"],
                },
            ),
        )
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id=profile_revision.id,
            profile_digest=profile_revision.profile_digest,
        ),
    )
    revision = service.approve(
        run.id,
        DatasetApprovalInput(actor="reviewer", reason="fixture approved"),
    )
    snapshot = service.create_training_snapshot(
        revision.id,
        TrainingSnapshotCreateInput(
            actor="modeler",
            purpose="second tabular materializer contract",
            targets=({"target_key": "target", "field": "target"},),
            split={"group_field": split_group_field, "folds": 2},
            selection_policy={
                "policy_id": "no-held-out-groups",
                "revision": 1,
                "exclusions": (
                    {
                        "kind": "field_equals_any_v1",
                        "field": "group",
                        "values": ("held-out",),
                    },
                ),
            },
        ),
    )
    return (
        DataLifecycleRepository(database),
        catalog,
        profile_revision.id,
        profile_revision.profile_digest,
        snapshot,
    )


def test_second_tabular_profile_materializes_exact_snapshot_selection(
    tmp_path: Path,
) -> None:
    repository, catalog, profile_revision_id, profile_digest, snapshot = (
        _lifecycle_fixture(tmp_path)
    )
    destination = tmp_path / "materialized" / "training.csv"
    result = training_snapshot_materializer_registry(
        repository,
        catalog,
    ).materialize(
        TrainingSnapshotMaterializationRequest(
            task_id="second-tabular-task-v1",
            profile_revision_id=profile_revision_id,
            training_snapshot_id=snapshot.id,
            destination=destination,
        )
    )

    assert isinstance(result, TrainingSnapshotMaterializationAvailable)
    artifact = result.builder_input
    assert artifact.task_id == "second-tabular-task-v1"
    assert artifact.profile_revision_id == profile_revision_id
    assert artifact.profile_digest == profile_digest
    assert artifact.target_cohorts == snapshot.target_cohorts
    assert artifact.split == snapshot.split
    assert artifact.provenance.training_snapshot_digest == snapshot.snapshot_digest
    assert artifact.provenance.materialization_adapter_id == (
        TABULAR_MATERIALIZATION_ADAPTER_ID
    )
    with destination.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["id"] for row in rows] == list(snapshot.included_row_keys)
    assert [row["group"] for row in rows] == ["g1", "g2", "g3", "g4"]

    repeated = training_snapshot_materializer_registry(
        repository,
        catalog,
    ).materialize(
        TrainingSnapshotMaterializationRequest(
            task_id="second-tabular-task-v1",
            profile_revision_id=profile_revision_id,
            training_snapshot_id=snapshot.id,
            destination=destination,
        )
    )
    assert isinstance(repeated, TrainingSnapshotMaterializationAvailable)
    assert repeated.builder_input.source_sha256 == artifact.source_sha256


def test_materializer_rejects_task_and_profile_digest_mismatch(
    tmp_path: Path,
) -> None:
    repository, catalog, profile_revision_id, _, snapshot = _lifecycle_fixture(
        tmp_path
    )
    registry = training_snapshot_materializer_registry(repository, catalog)
    task_mismatch = registry.materialize(
        TrainingSnapshotMaterializationRequest(
            task_id="different-task",
            profile_revision_id=profile_revision_id,
            training_snapshot_id=snapshot.id,
            destination=tmp_path / "wrong-task.csv",
        )
    )
    assert task_mismatch == TrainingSnapshotMaterializationUnavailable(
        reason_code="profile_task_mismatch",
        reason="Profile Revisionは指定したPrediction Taskに対応していません",
    )

    with sqlite3.connect(catalog.path) as conn:
        conn.execute(
            "UPDATE dataset_profile_revisions SET profile_digest=? WHERE id=?",
            ("sha256:" + "0" * 64, profile_revision_id),
        )
    with pytest.raises(
        ValueError,
        match="Profile Revision digest does not match",
    ):
        registry.materialize(
            TrainingSnapshotMaterializationRequest(
                task_id="second-tabular-task-v1",
                profile_revision_id=profile_revision_id,
                training_snapshot_id=snapshot.id,
                destination=tmp_path / "wrong-digest.csv",
            )
        )


def test_materializer_rejects_snapshot_split_for_another_builder_group(
    tmp_path: Path,
) -> None:
    repository, catalog, profile_revision_id, _, snapshot = _lifecycle_fixture(
        tmp_path,
        split_group_field="id",
    )

    with pytest.raises(
        ValueError,
        match="split group field does not match",
    ):
        training_snapshot_materializer_registry(
            repository,
            catalog,
        ).materialize(
            TrainingSnapshotMaterializationRequest(
                task_id="second-tabular-task-v1",
                profile_revision_id=profile_revision_id,
                training_snapshot_id=snapshot.id,
                destination=tmp_path / "wrong-split.csv",
            )
        )


def test_materializer_never_writes_beneath_source_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, catalog, profile_revision_id, _, snapshot = _lifecycle_fixture(
        tmp_path
    )
    source_truth = tmp_path / "read-only-source"
    monkeypatch.setattr(
        training_snapshot_adapter,
        "READ_ONLY_SOURCE_ROOT",
        source_truth,
    )
    destination = source_truth / "derived.csv"

    with pytest.raises(ValueError, match="read-only source of truth"):
        training_snapshot_materializer_registry(
            repository,
            catalog,
        ).materialize(
            TrainingSnapshotMaterializationRequest(
                task_id="second-tabular-task-v1",
                profile_revision_id=profile_revision_id,
                training_snapshot_id=snapshot.id,
                destination=destination,
            )
        )
    assert not destination.exists()


def test_unsupported_profile_family_is_reasoned_unavailable(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    catalog = WorkspaceCatalog(database)
    profile = catalog.upsert_profile_revision(
        ProfileRevisionCreateInput(
            profile_id="unsupported-series-profile",
            revision=1,
            name="Unsupported series fixture",
            profile_digest="sha256:" + "1" * 64,
            canonical_contract_digest="sha256:unsupported",
            effective_profile_json={
                "schema_version": "series-profile/v1",
                "profile_id": "unsupported-series-profile",
                "task_id": "series-task",
            },
        )
    )
    result = training_snapshot_materializer_registry(
        DataLifecycleRepository(database),
        catalog,
    ).materialize(
        TrainingSnapshotMaterializationRequest(
            task_id="series-task",
            profile_revision_id=profile.id,
            training_snapshot_id="must-not-fallback-to-another-snapshot",
            destination=tmp_path / "must-not-exist.csv",
        )
    )

    assert result == TrainingSnapshotMaterializationUnavailable(
        reason_code="profile_family_unsupported",
        reason=(
            "このProfile familyのTraining Snapshot materializerは"
            "allow-listされていません"
        ),
    )
    assert not (tmp_path / "must-not-exist.csv").exists()
