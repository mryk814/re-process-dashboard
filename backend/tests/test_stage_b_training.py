from __future__ import annotations

from pathlib import Path
import shutil

from openpyxl import load_workbook
from material_workbench.data.stage_b_training import (
    build_stage_b_training_data,
    load_stage_b_profile,
)
from material_workbench.modeling.model_packages import ModelPackageLoader


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
PROFILE = (
    ROOT
    / "backend/src/material_workbench/data/welding-stage-b-profile-v1.json"
)


def test_stage_b_uses_weld_metal_observations_not_relation_rows() -> None:
    profile = load_stage_b_profile(PROFILE)
    result = build_stage_b_training_data(SOURCE, profile)

    assert result.data.row_count == 300
    assert len(result.data.observations) == 300
    assert len({row["id"] for row in result.data.observations}) == 300
    assert len({row["parent_key"] for row in result.data.observations}) == 300
    assert all(row["eligible"] for row in result.data.observations)
    assert all(len(row["composition"]) == 31 for row in result.data.observations)
    assert all(len(row["features"]) == 4 for row in result.data.observations)
    assert all(len(row["categorical"]) == 2 for row in result.data.observations)
    assert all(len(row["outputs"]) == 16 for row in result.data.observations)


def test_stage_b_cohorts_and_folds_are_target_specific_and_group_safe() -> None:
    profile = load_stage_b_profile(PROFILE)
    result = build_stage_b_training_data(SOURCE, profile)

    assert set(result.cohort_digests) == set(profile.weld_output_columns)
    assert set(result.fold_digests) == set(profile.weld_output_columns)
    assert result.missing_by_target == {
        target: 0 for target in profile.weld_output_columns
    }
    assert all(value.startswith("sha256:") for value in result.cohort_digests.values())
    assert all(value.startswith("sha256:") for value in result.fold_digests.values())
    assert result.profile_digest == profile.profile_digest
    assert result.transform_digest == profile.transform_digest


def test_stage_b_missing_targets_keep_inputs_and_create_target_cohorts(
    tmp_path: Path,
) -> None:
    changed = tmp_path / "stage-b-missing-targets.xlsx"
    shutil.copyfile(SOURCE, changed)
    workbook = load_workbook(changed)
    sheet = workbook["溶着金属成分"]
    headers = {
        cell.value: cell.column for cell in sheet[1]
    }
    sheet.cell(2, headers["C[%]"]).value = None
    sheet.cell(3, headers["O[%]"]).value = None
    workbook.save(changed)
    workbook.close()

    profile = load_stage_b_profile(PROFILE)
    result = build_stage_b_training_data(changed, profile)

    assert result.data.row_count == 300
    assert all(row["eligible"] for row in result.data.observations)
    assert result.missing_by_target["C"] == 1
    assert result.missing_by_target["O"] == 1
    assert result.missing_by_target["Mn"] == 0
    assert result.cohort_digests["C"] != result.cohort_digests["Mn"]
    assert result.fold_digests["C"] != result.fold_digests["Mn"]


def test_stage_b_package_records_profile_cohort_fold_and_smoke_contracts() -> None:
    package = ModelPackageLoader().load(
        ROOT / "models/packages/welding-consumable-stage-b-ridge-v1"
    )
    assert package.manifest.task_id == "welding-consumable-stage-b-v1"
    assert len(package.manifest.predictors) == 16
    stats = package.artifact_path(
        "reference/training_stats.json"
    ).read_text(encoding="utf-8")
    for marker in (
        "profile_digest",
        "transform_digest",
        "cohort_digests",
        "fold_digests",
        "missing_by_target",
    ):
        assert marker in stats


def test_stage_b_task_predicts_and_compares_actual_measurement(client) -> None:
    tasks = client.get("/api/task-definitions").json()
    task = next(
        item for item in tasks
        if item["definition"]["task_definition"]["id"]
        == "welding-consumable-stage-b-v1"
    )
    assert task["definition"]["availability"]["status"] == "available"
    projects = client.get("/api/projects").json()
    project = next(
        item for item in projects if item["id"] == "welding-stage-b-default"
    )
    package = client.get(f"/api/projects/{project['id']}/model-package")
    assert package.status_code == 200, package.text
    assert package.json()["id"] == "welding-consumable-stage-b-ridge-v1"
    candidates = client.get(
        f"/api/projects/{project['id']}/candidates"
    ).json()
    candidate = candidates[0]
    preview = client.post(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/preview",
        params={"expected_revision": candidate["revision"]},
    )
    assert preview.status_code == 200, preview.text
    prediction = preview.json()["predictions"]["C"]
    assert prediction["unit"] == "mass% deposited metal"
    assert preview.json()["model_meta"]["training_data"]["profile_digest"].startswith(
        "sha256:"
    )

    actual = client.post(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/actuals",
        params={"expected_revision": candidate["revision"]},
        json={
            "property": "C",
            "mean": prediction["value"],
            "std": 0.001,
            "replicates": 1,
            "unit": "mass% deposited metal",
            "experiment_no": "STAGE-B-TEST",
            "note": "Stage B unit integration",
        },
    )
    assert actual.status_code == 201
    comparison = client.get(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/prediction-vs-actual"
    )
    assert comparison.status_code == 200
    assert comparison.json()["comparisons"][0]["actual"]["property"] == "C"


def test_stage_b_is_available_in_developer_training_inspector(client) -> None:
    profiles = client.get("/api/developer/observation-training-profiles")
    assert profiles.status_code == 200
    profile = next(
        item for item in profiles.json()
        if item["profile_id"] == "welding-consumable-stage-b-v1"
    )
    assert profile["families"][0]["source_rows"] == 300
    page = client.get(
        "/api/developer/observation-training-data",
        params={
            "profile_id": "welding-consumable-stage-b-v1",
            "family": "stage_b",
            "target": "C",
            "limit": 10,
        },
    )
    assert page.status_code == 200
    assert page.json()["source_rows"] == 300
    assert page.json()["usable_rows"] == 300
