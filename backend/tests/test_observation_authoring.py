from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from decision_workbench.application.data_lifecycle import DataLifecycleService
from decision_workbench.application.dataset_registration import register_managed_dataset
from decision_workbench.contracts.data_lifecycle_contracts import (
    CurationRecipeCreateInput,
    CurationRunCreateInput,
    DatasetApprovalInput,
    ObjectSelection,
    SourceConnectorCreateInput,
    SourceFetchRequest,
    TrainingSnapshotCreateInput,
)
from decision_workbench.data.observation_authoring import (
    ObservationAuthoringRequest,
    ObservationInputBinding,
    ObservationTargetBinding,
    author_observation_profile,
)
from decision_workbench.data.observation_profile import (
    ObservationProfileError,
    build_observation_training_dataset,
    load_observation_profile,
)
from decision_workbench.modeling.model_package_verify import verify_model_package
from decision_workbench.modeling.observation_model_builder import build
from decision_workbench.persistence.store import Store
from decision_workbench.task_composition.builtin.welding import observation_declaration
from decision_workbench.tasks.task_registry import load_task_contracts


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (
    ROOT
    / "backend"
    / "tests"
    / "fixtures"
    / "observation_authoring"
    / "weld-tensile-repeats.csv"
)
TASK_ID = "welding-graph-tensile-ts-v1"


def _request() -> ObservationAuthoringRequest:
    task = load_task_contracts()[TASK_ID].task_definition
    return ObservationAuthoringRequest(
        task_id=TASK_ID,
        observation_grain="同一溶接条件から採取した個別引張試験片",
        observation_id_column="specimen_id",
        group_column="condition_id",
        inputs=tuple(
            ObservationInputBinding(
                path=field.path,
                column=field.path,
                source_unit=field.unit,
            )
            for group in task.input_groups
            for field in group.fields
        ),
        targets=(ObservationTargetBinding(
            key="TS",
            column="TS",
            source_unit="MPa",
        ),),
        technical_metadata_columns=("operator",),
        validation_folds=5,
        ridge_alpha=0.75,
    )


def test_repeated_measurement_authoring_reaches_dataset_snapshot_and_verified_package(
    tmp_path: Path,
) -> None:
    profile_store = tmp_path / "profiles"
    authored = author_observation_profile(
        SOURCE,
        _request(),
        store_path=profile_store,
    )
    retried = author_observation_profile(
        SOURCE,
        _request(),
        store_path=profile_store,
    )
    assert retried.profile_id == authored.profile_id
    assert retried.profile_digest == authored.profile_digest
    assert len(tuple(profile_store.glob("*.json"))) == 1
    profile_path = Path(authored.profile_locator)
    profile = load_observation_profile(profile_path)
    training = build_observation_training_dataset(SOURCE, profile)
    assert authored.observations == 12
    assert authored.groups == 6
    assert training.views["authored-observations"].summary.split_groups == 6
    assert profile.authoring is not None
    assert profile.authoring.validation_plan.folds == 5
    assert profile.authoring.feature_recipe.id == "observation-identity-v1"
    assert profile.authoring.estimator.alpha == 0.75

    database = tmp_path / "workbench.db"
    registered = register_managed_dataset(
        database=database,
        source=SOURCE,
        library_root=tmp_path / "library",
        profile_path=profile_path,
        name="External repeated tensile observations",
    )
    assert registered.dataset_revision_id
    assert registered.task_ids == (TASK_ID,)

    with SOURCE.open(encoding="utf-8", newline="") as stream:
        lifecycle_rows = list(csv.DictReader(stream))
    Store(database)
    lifecycle = DataLifecycleService(database)
    connector = lifecycle.create_connector(SourceConnectorCreateInput(
        name="External observation authoring",
        connector_type="object_storage_json_v1",
        source_locator="s3://fixture/external-observation-authoring.json",
        selection=ObjectSelection(format="json_array", primary_key="specimen_id"),
    ))
    recipe = lifecycle.create_recipe(CurationRecipeCreateInput(
        recipe_id="external-observation-authoring",
        version=1,
        name="Observation authoring non-missing targets",
        steps=(
            {
                "kind": "coerce_number_v1",
                "fields": [
                    item.column for item in _request().inputs
                ] + ["TS"],
            },
            {
                "kind": "required_fields_v1",
                "fields": ["specimen_id", "condition_id", *[
                    item.column for item in _request().inputs
                ]],
            },
            {"kind": "target_eligibility_v1", "fields": ["TS"]},
        ),
    ))
    raw, _ = lifecycle.fetch(
        connector.id,
        SourceFetchRequest(
            object_content=json.dumps(lifecycle_rows),
            object_version=authored.source_sha256,
        ),
    )
    run = lifecycle.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id=registered.profile_revision_id,
            profile_digest=authored.profile_digest,
        ),
    )
    revision = lifecycle.approve(
        run.id,
        DatasetApprovalInput(actor="author", reason="Profile validation completed"),
    )
    snapshot = lifecycle.create_training_snapshot(
        revision.id,
        TrainingSnapshotCreateInput(
            actor="author",
            purpose="Observation authoring acceptance",
            targets=({"target_key": "TS", "field": "TS"},),
            split={"group_field": "condition_id", "folds": 5},
        ),
    )
    assert snapshot.row_count == 12
    assert len(snapshot.target_cohorts[0].split_assignments) == 6

    package = tmp_path / "package"
    build(
        SOURCE,
        package,
        declaration=observation_declaration(TASK_ID),
        package_id="external-weld-tensile-ridge-v1",
        package_version="1.0.0",
        profile=profile,
    )
    report = verify_model_package(
        package,
        task_id=TASK_ID,
        source=SOURCE,
        profile=profile_path,
    )
    assert report.package_id == "external-weld-tensile-ridge-v1"
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    predictor = manifest["predictors"][0]["config"]
    assert predictor["validation_plan"] == {"strategy": "grouped-k-fold", "folds": 5}
    assert predictor["feature_recipe"]["id"] == "observation-identity-v1"
    assert predictor["ridge_alpha"] == 0.75


def test_observation_authoring_rejects_relational_workbook_shape(
    tmp_path: Path,
) -> None:
    workbook = Workbook()
    workbook.active.title = "observations"
    workbook.create_sheet("relation")
    source = tmp_path / "relational.xlsx"
    workbook.save(source)
    workbook.close()

    with pytest.raises(ObservationProfileError, match="exactly one visible worksheet"):
        author_observation_profile(
            source,
            _request(),
            store_path=tmp_path / "profiles",
        )


def test_observation_authoring_rejects_columns_with_multiple_scientific_roles() -> None:
    request = _request().model_dump(mode="json")
    request["inputs"][0]["column"] = "TS"

    with pytest.raises(
        ValueError,
        match="source columns must have exactly one authoring role",
    ):
        ObservationAuthoringRequest.model_validate(request)


def test_observation_authoring_requires_grouped_folds_for_each_target(
    tmp_path: Path,
) -> None:
    source = tmp_path / "insufficient-target-groups.csv"
    with SOURCE.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
        fieldnames = rows[0].keys()
    retained_groups = {"WR-101", "WR-102", "WR-103", "WR-104"}
    for row in rows:
        if row["condition_id"] not in retained_groups:
            row["TS"] = ""
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    with pytest.raises(
        ValueError,
        match="TS grouped validation requires at least 5 eligible groups; found 4",
    ):
        author_observation_profile(
            source,
            _request(),
            store_path=tmp_path / "profiles",
        )
