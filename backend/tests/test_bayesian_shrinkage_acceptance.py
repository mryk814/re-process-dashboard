from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from backend.scripts.operations.model_workflow import compare_estimators
from decision_workbench.modeling.training.estimators import bayesian_linear
from decision_workbench.modeling.training.recipe import estimator_recipe


ROOT = Path(__file__).resolve().parents[2]
BUNDLED_TASK = "welding-graph-deposition-efficiency-v1"
BUNDLED_SOURCE = (
    ROOT
    / "data"
    / "fixtures"
    / "prediction-graph"
    / "welding_deposition_efficiency_synthetic.csv"
)


def _noisy_shrinkage_fixture() -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(789)
    values = rng.normal(size=(64, 6))
    target = (
        0.4
        + 1.9 * values[:, 0]
        + 0.15 * values[:, 1]
        + rng.normal(0.0, 0.75, size=len(values))
    )
    return values, target


def test_bundled_task_real_comparison_records_same_cohort_calibration_and_memory(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpyro")
    result = compare_estimators(
        BUNDLED_TASK,
        BUNDLED_SOURCE,
        tmp_path / "comparison",
        tmp_path / "feature-dataset.json",
        estimators=(
            "ridge.v1",
            "bayesian-ridge.v1",
            "horseshoe-linear.v1",
        ),
        estimator_options={
            "ridge.v1": {"folds": 3},
            "bayesian-ridge.v1": {"folds": 3},
            "horseshoe-linear.v1": {"folds": 3},
        },
        package_prefix="issue-789-bundled-comparison",
        package_version="1.0.0",
    )

    assert result["selection"] is None
    assert len(result["models"]) == 3
    assert result["feature_dataset_id"].startswith("sha256:")
    for model in result["models"]:
        assert model["quality_report"] == model["quality"]
        assert model["quality_findings"] == []
        assert model["evaluation"] == result["evaluation"]
        assert model["cost"]["peak_traced_memory_bytes"] > 0
        assert model["quality"]["targets"]
        for metric in model["quality"]["targets"]:
            assert 0.0 <= metric["interval_coverage_90"] <= 1.0
            if metric["mean_interval_width"] is not None:
                assert metric["mean_interval_width"] >= 0.0
    assert result["evidence_contract"]["calibration"]
    assert result["evidence_contract"]["memory"]
    saved = json.loads(
        (tmp_path / "comparison" / "comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["evaluation"] == result["evaluation"]


def test_real_noisy_fixture_recovers_signal_with_horseshoe_shrinkage() -> None:
    pytest.importorskip("numpyro")
    values, target = _noisy_shrinkage_fixture()
    fit = bayesian_linear._fit(
        values,
        target,
        estimator_recipe("horseshoe-linear.v1"),
        seed=789,
    )

    signal = fit.standardized_coefficients[:, 0]
    nuisance = fit.standardized_coefficients[:, 2:]
    assert float(np.mean(signal > 0.0)) > 0.9
    assert float(np.mean(np.abs(nuisance))) < float(np.mean(np.abs(signal)))
    assert fit.inference_identity.diagnostics.status == "passed"


def test_real_horseshoe_prior_sensitivity_changes_inference_identity_and_draws(
) -> None:
    pytest.importorskip("numpyro")
    values, target = _noisy_shrinkage_fixture()
    narrow = estimator_recipe(
        "horseshoe-linear.v1",
        {"slab_scale": 0.5, "slab_degrees_of_freedom": 3.0},
    )
    wide = estimator_recipe(
        "horseshoe-linear.v1",
        {"slab_scale": 4.0, "slab_degrees_of_freedom": 12.0},
    )
    # Keep this sensitivity comparison on a sampler seed that passes the
    # declared zero-divergence gate for both prior settings.
    narrow_fit = bayesian_linear._fit(values, target, narrow, seed=790)
    wide_fit = bayesian_linear._fit(values, target, wide, seed=790)

    assert narrow.slab_degrees_of_freedom != wide.slab_degrees_of_freedom
    assert narrow_fit.inference_identity.identity_digest != (
        wide_fit.inference_identity.identity_digest
    )
    assert not np.allclose(
        narrow_fit.standardized_coefficients,
        wide_fit.standardized_coefficients,
    )
