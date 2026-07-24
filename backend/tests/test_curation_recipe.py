from __future__ import annotations

from collections import Counter
from pathlib import Path

from material_workbench.modeling.tabular_regression import load_tabular_data


ROOT = Path(__file__).parents[2]
PROFILE = ROOT / "backend/src/material_workbench/data/tabular-profile-mpea-literature-tys-v1.json"
SOURCE = ROOT / "data/source/external/mpea_ground_truth_18021833.csv"


def test_mpea_curation_keeps_every_source_row_and_explains_disposition() -> None:
    data = load_tabular_data(SOURCE, PROFILE)

    assert data.row_count == 396
    assert len(data.observations) == 396
    statuses = Counter(row["run_context"]["curation"]["status"] for row in data.observations)
    assert statuses == {"accepted": 155, "warning": 218, "quarantined": 23}
    assert sum(statuses.values()) == 396
    assert all(
        row["eligibility_reasons"] or not row["outputs"]
        for row in data.observations
        if not row["eligible"]
    )
    assert len({row["id"] for row in data.observations}) == 396
    assert {issue["issue_type"] for issue in data.detected_quality} == {
        "curation_quarantine",
        "missing_target",
    }


def test_mpea_target_cohort_is_grouped_by_source_paper() -> None:
    data = load_tabular_data(SOURCE, PROFILE)
    eligible = [row for row in data.observations if row["eligible"]]

    assert len(eligible) == 169
    assert len({row["parent_key"] for row in eligible}) == 58
    assert all(row["eligible_targets"] == ["TYS"] for row in eligible)
    assert all(row["parent_key"] != row["id"] for row in eligible)
