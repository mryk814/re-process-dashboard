from __future__ import annotations

from collections import Counter
from pathlib import Path

from material_workbench.modeling.tabular.data import load_tabular_data
from material_workbench.modeling.tabular.profile import load_tabular_profile


ROOT = Path(__file__).parents[2]
PROFILE = ROOT / "backend/src/material_workbench/data/tabular-profile-mpea-room-tensile-v1.json"
SOURCE = ROOT / "data/source/external/mpea_ground_truth_18021833.csv"


def test_mpea_forward_task_does_not_use_post_process_microstructure() -> None:
    profile = load_tabular_profile(PROFILE)
    input_columns = {item.column for item in profile.inputs}

    assert not any(
        marker in column.casefold()
        for column in input_columns
        for marker in ("matrix", "precipitate")
    )
    assert profile.group_column == "File_Name"
    assert profile.id_column == "Material"


def test_mpea_curation_keeps_every_source_row_and_explains_disposition() -> None:
    data = load_tabular_data(SOURCE, PROFILE)

    assert data.row_count == 396
    assert len(data.observations) == 396
    statuses = Counter(row["run_context"]["curation"]["status"] for row in data.observations)
    assert statuses == {"accepted": 225, "warning": 17, "quarantined": 154}
    assert sum(statuses.values()) == 396
    assert all(
        row["eligibility_reasons"] or not row["outputs"]
        for row in data.observations
        if not row["eligible"]
    )
    assert len({row["id"] for row in data.observations}) == 396
    trace = data.observations[0]["run_context"]["curation"]["values"]
    assert trace["Homogenization temp (°C)"].keys() >= {
        "raw", "normalized", "parser", "conversion",
    }
    assert {issue["issue_type"] for issue in data.detected_quality} == {
        "curation_quarantine",
        "missing_target",
    }


def test_mpea_target_cohort_is_grouped_by_source_paper() -> None:
    data = load_tabular_data(SOURCE, PROFILE)
    eligible = [row for row in data.observations if row["eligible"]]

    assert len(eligible) == 104
    assert len({row["parent_key"] for row in eligible}) == 41
    assert {
        target: sum(target in row["eligible_targets"] for row in eligible)
        for target in ("TYS", "UTS", "EL")
    } == {"TYS": 99, "UTS": 90, "EL": 71}
    assert {
        target: len({
            row["parent_key"] for row in eligible if target in row["eligible_targets"]
        })
        for target in ("TYS", "UTS", "EL")
    } == {"TYS": 39, "UTS": 38, "EL": 33}
    assert all(row["parent_key"] != row["id"] for row in eligible)
