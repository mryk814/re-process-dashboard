from __future__ import annotations

from pathlib import Path

from material_workbench.data.stage_b_training import (
    build_stage_b_training_data,
    load_stage_b_profile,
)


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
