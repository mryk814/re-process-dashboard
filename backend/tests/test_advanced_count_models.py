from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from decision_workbench.adapters.numpyro_posterior import NumpyroDensePosteriorAdapter
from decision_workbench.contracts.sampling_identity_contracts import SamplingRequest
from decision_workbench.contracts.task_contracts import OutputDefinition
from decision_workbench.modeling.packages.contracts import PredictorSpec
from decision_workbench.modeling.training.feature_dataset import compile_target_training_set
from decision_workbench.modeling.training.count_comparison import (
    CountCandidateEvidence,
    compare_same_cohort_counts,
)
from decision_workbench.modeling.training.readiness import (
    resolve_estimator_contract_readiness,
    standard_estimator_catalog,
)
from decision_workbench.modeling.training.recipe import estimator_recipe
from decision_workbench.modeling.training.validation_plan import ValidationPlan


class _Artifacts:
    def __init__(self, path: Path) -> None:
        self.path = path

    def artifact_path(self, relative_path: str) -> Path:
        assert Path(relative_path).name == self.path.name
        return self.path


def _canonical(values: tuple[float, ...], exposures: tuple[float, ...]) -> dict[str, object]:
    return {
        "task_id": "count-contract-test",
        "feature_pipeline": {"features": [{"name": "x"}], "missing_policy": {"imputation_values": {}}},
        "rows": [
            {
                "observation_id": f"obs-{index}",
                "condition_context_id": f"context-{index}",
                "parent_key": f"group-{index}",
                "features": {"x": float(index)},
                "canonical_inputs": {"process.exposure": exposure},
                "outputs": {"events": value},
            }
            for index, (value, exposure) in enumerate(zip(values, exposures, strict=True))
        ],
    }


def _count_output(*, explicit_exposure: bool) -> OutputDefinition:
    count: dict[str, object] = {"count_unit": "events"}
    if explicit_exposure:
        count.update({"exposure_label": "time", "exposure_input_path": "process.exposure", "exposure_unit": "h"})
    return OutputDefinition.model_validate({"key": "events", "label": "Events", "unit": "events", "target_kind": "count", "count": count, "goal_direction": "at_most", "plausibility_range": {"min": 0, "max": 1000}, "preferred_display_range": {"min": 0, "max": 100}})


def test_count_compiler_never_rounds_and_sums_explicit_exposure() -> None:
    data = compile_target_training_set(_canonical((0, 2, 4, 1), (1, 2, 4, 2)), target="events", unit="events", target_kind="count", exposure_input_path="process.exposure", folds=2, seed=792)
    assert np.array_equal(data.y, np.asarray([0.0, 2.0, 4.0, 1.0]))
    assert np.array_equal(data.exposure, np.asarray([1.0, 2.0, 4.0, 2.0]))
    assert data.exposure_input_path == "process.exposure"
    with pytest.raises(ValueError, match="nonnegative integers"):
        compile_target_training_set(_canonical((0, 1.5, 2, 3), (1, 1, 1, 1)), target="events", unit="events", target_kind="count", folds=2, seed=792)


def test_advanced_count_recipes_are_experimental_and_require_observed_diagnostics() -> None:
    ids = {entry.estimator_id: entry for entry in standard_estimator_catalog().entries}
    for estimator_id in ("negative-binomial-regression.v1", "zero-inflated-poisson-regression.v1"):
        assert estimator_recipe(estimator_id).estimator_id == estimator_id
        assert ids[estimator_id].adoption_status == "experimental"
        assert "zero" in " ".join(ids[estimator_id].quality_metrics)
    plan = ValidationPlan(strategy="grouped_kfold", folds=2, group_key="parent_key")
    explicit = resolve_estimator_contract_readiness(estimator_id="negative-binomial-regression.v1", output=_count_output(explicit_exposure=True), validation_plan=plan, feature_recipe=None, canonical_feature_count=1, row_count=20, independent_group_count=10, observed_target_min=0, observed_targets_are_integers=True, observed_zero_rate=0.4, observed_target_mean=2.0, observed_target_variance=5.0, available_dependencies=frozenset({"numpyro"}))
    assert explicit.status == "ready"
    missing = resolve_estimator_contract_readiness(estimator_id="zero-inflated-poisson-regression.v1", output=_count_output(explicit_exposure=False), validation_plan=plan, feature_recipe=None, canonical_feature_count=1, row_count=20, independent_group_count=10, observed_target_min=0, observed_targets_are_integers=True, available_dependencies=frozenset({"numpyro"}))
    assert missing.status == "out_of_scope"
    assert "diagnostics" in missing.reasons[0]
    zip_without_rationale = resolve_estimator_contract_readiness(estimator_id="zero-inflated-poisson-regression.v1", output=_count_output(explicit_exposure=False), validation_plan=plan, feature_recipe=None, canonical_feature_count=1, row_count=20, independent_group_count=10, observed_target_min=0, observed_targets_are_integers=True, observed_zero_rate=0.9, observed_target_mean=2.0, observed_target_variance=8.0, available_dependencies=frozenset({"numpyro"}))
    assert zip_without_rationale.status == "out_of_scope"
    assert "structural-zero" in zip_without_rationale.reasons[0]


def test_same_cohort_comparison_refuses_automatic_selection_and_identity_drift() -> None:
    poisson = CountCandidateEvidence("poisson.v1", "sha256:cohort", "sha256:fold", "sha256:exposure", {"log_score": -2.0})
    zip_candidate = CountCandidateEvidence("zero-inflated-poisson-regression.v1", "sha256:cohort", "sha256:fold", "sha256:exposure", {"log_score": -1.5}, structural_zero_evidence="process state prevents events")
    protocol = compare_same_cohort_counts((poisson, zip_candidate))
    assert protocol.adoption_decision == "experimental"
    assert protocol.automatic_selection is False
    with pytest.raises(ValueError, match="share cohort"):
        compare_same_cohort_counts((poisson, CountCandidateEvidence("negative-binomial-regression.v1", "sha256:other", "sha256:fold", "sha256:exposure", {"log_score": -1.7})))


@pytest.mark.parametrize(
    ("family", "arrays", "expected_key"),
    [
        ("negative_binomial_log", {"w0": np.asarray([[[0.0]], [[0.0]], [[0.0]]]), "b0": np.asarray([[0.0], [0.0], [0.0]]), "dispersion": np.asarray([2.0, 2.0, 2.0])}, "overdispersion"),
        ("zero_inflated_poisson_log", {"w0": np.asarray([[[0.0, 0.0]], [[0.0, 0.0]], [[0.0, 0.0]]]), "b0": np.asarray([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0]])}, "zero_probability"),
    ],
)
def test_safe_runtime_distinguishes_expected_count_from_nb_and_zip_parameters(tmp_path: Path, family: str, arrays: dict[str, np.ndarray], expected_key: str) -> None:
    artifact = tmp_path / "posterior.npz"
    np.savez(artifact, **arrays)
    spec = PredictorSpec.model_validate({"id": family, "target": "events", "unit": "events", "target_kind": "count", "runtime_type": "numpyro.dense_posterior.v1", "architecture_id": "dense_mlp_v1", "artifact": "posterior.npz", "predictive_family": family, "feature_names": ["x"], "config": {"exposure": {"mode": "explicit_offset/v1", "input_path": "process.exposure"}}})
    summary = NumpyroDensePosteriorAdapter().load(_Artifacts(artifact), spec).predict({"x": 0.0, "process.exposure": 2.0}, sampling_request=SamplingRequest.create(operation="package_verification", policy_id="advanced-count-test/v1", seed=792, requested_sample_count=3))
    assert summary.point_statistic == "rate"
    assert summary.point_estimate == pytest.approx(2.0 if family == "negative_binomial_log" else 1.0)
    assert summary.prediction_interval is not None
    assert summary.distribution["mean_semantics"] == "expected_count"
    assert expected_key in summary.distribution
