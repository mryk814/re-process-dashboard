from __future__ import annotations

import json
from pathlib import Path

from backend.scripts.compare_annealing_feature_pipelines import compare


ROOT = Path(__file__).resolve().parents[2]


def test_comparison_uses_identical_parent_folds_and_reports_ls_redundancy() -> None:
    report = compare(ROOT / "data/source/material_workbench_tutorial_v1.xlsx")

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
