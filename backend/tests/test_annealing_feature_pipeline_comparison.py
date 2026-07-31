from __future__ import annotations

import json
from pathlib import Path

from backend.scripts.experiments.compare_annealing_feature_pipelines import compare
from decision_workbench.modeling.numeric_canonicalization import (
    canonicalize_report_float,
)


ROOT = Path(__file__).resolve().parents[2]


def test_comparison_uses_identical_parent_folds_and_reports_ls_redundancy() -> None:
    report = compare(ROOT / "data/source/material_workbench_tutorial_v2.xlsx")

    assert report["comparison_policy"]["same_parent_folds_for_both_pipelines"] is True
    assert report["parents"]["comparison"] >= 5
    assert report["pipelines"]["v2"]["includes_raw_line_speed"] is True
    assert report["pipelines"]["v4"]["includes_raw_line_speed"] is False
    assert set(report["target_metrics"]) >= {"TS", "YS", "EL"}
    assert report["line_speed_heat_feature_correlations"]
    assert set(report["line_speed_coefficient_stability"]) >= {"TS", "YS", "EL"}
    assert "synthetic demo data" in report["interpretation_limit"]


def test_committed_process_comparison_report_is_current() -> None:
    source = ROOT / "data/source/material_workbench_process_v1.xlsx"
    expected = json.loads(
        (ROOT / "docs/reports/annealing-feature-pipeline-v4-comparison.json")
        .read_text(encoding="utf-8")
    )

    assert compare(source) == expected


def test_process_comparison_emits_canonical_numeric_evidence() -> None:
    report = compare(ROOT / "data/source/material_workbench_process_v1.xlsx")
    computed_values = [
        metric[name]
        for pipelines in report["target_metrics"].values()
        for metric in pipelines.values()
        for name in ("mae", "rmse")
    ]
    computed_values.extend(
        value
        for correlation in report["line_speed_heat_feature_correlations"]
        for name, value in correlation.items()
        if name in {"pearson_r", "absolute_r"}
    )
    for stability in report["line_speed_coefficient_stability"].values():
        computed_values.extend(stability["standardized_ls_coefficients"])
        computed_values.extend(
            stability[name] for name in ("sign_agreement", "mean", "std")
        )

    assert computed_values
    assert all(
        value == canonicalize_report_float(value)
        for value in computed_values
    )
