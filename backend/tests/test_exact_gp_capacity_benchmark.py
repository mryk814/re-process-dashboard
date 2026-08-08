from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np

from decision_workbench.modeling.training.approximate_gp_spike import (
    SPIKE_ID,
    fit_spike,
)


ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "backend" / "scripts" / "operations"
if str(OPERATIONS) not in sys.path:
    sys.path.insert(0, str(OPERATIONS))


def test_bounded_capacity_evidence_covers_the_declared_matrix() -> None:
    report = json.loads(
        (ROOT / "docs/benchmarks/exact-gp-capacity-v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert len(report["cases"]) == 60
    assert report["policy"]["actual_measurement_limit"] == {
        "design_points": 13,
        "unique_case_points": 9,
        "full_fit_points": 7,
        "preflight_points": 2,
    }
    assert report["policy"]["memory_measurement"]["metric"] == (
        "process_peak_working_set_bytes"
    )
    assert sum(
        item["metrics"]["measurement_status"].startswith("measured")
        for item in report["cases"]
    ) == 9
    assert any(
        item["role"] == "effective_rows=750"
        and item["measurement_status"] == "measured_preflight"
        for item in report["measurement_design"]
    )
    assert any(
        item["metrics"]["measurement_status"] == "projected_from_versioned_policy"
        and item["capacity_resolution"]["decision"] == "approximate_required"
        for item in report["cases"]
    )
    assert all(
        "peak_working_set_bytes" in item["metrics"]
        or "estimated_peak_memory_bytes" in item["metrics"]
        for item in report["cases"]
    )
    comparison = report["same_cohort_comparison"]
    assert comparison["adoption_decision"] == "no_adopt"
    assert comparison["cohort_digest"]
    assert comparison["fold_digest"]
    assert comparison["validation_plan_digest"]
    assert comparison["alternative_path"]["estimator_id"] == "ridge.v1"


def test_approximate_spike_writes_numeric_only_bounded_npz(tmp_path: Path) -> None:
    from exact_gp_capacity_benchmark import _fixture_training_set

    data = _fixture_training_set(effective_rows=24, feature_count=4, folds=3)
    artifact = tmp_path / "spike.npz"

    fit_spike(data, basis_count=16, seed=7, artifact_path=artifact)

    with np.load(artifact, allow_pickle=False) as loaded:
        assert str(loaded["estimator_id"]) == SPIKE_ID
        assert loaded["weights"].shape == (4, 16)
        assert loaded["coefficient"].shape == (16,)
        assert all(loaded[key].dtype.kind != "O" for key in loaded.files)
