from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

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
from decision_workbench.modeling.training.estimators.advanced_count import (
    negative_binomial_deviance,
    negative_binomial_zero_mass,
)
from decision_workbench.modeling.training.estimators import advanced_count
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
    spec = PredictorSpec.model_validate({"id": family, "target": "events", "unit": "events", "target_kind": "count", "runtime_type": "numpyro.dense_posterior.v1", "architecture_id": "dense_mlp_v1", "artifact": "posterior.npz", "predictive_family": family, "feature_names": ["x"], "config": {"advanced_count_contract": "advanced-count-contract/v1", "exposure": {"mode": "explicit_offset/v1", "input_path": "process.exposure"}}})
    summary = NumpyroDensePosteriorAdapter().load(_Artifacts(artifact), spec).predict({"x": 0.0, "process.exposure": 2.0}, sampling_request=SamplingRequest.create(operation="package_verification", policy_id="advanced-count-test/v1", seed=792, requested_sample_count=3))
    assert summary.point_statistic == "rate"
    assert summary.point_estimate == pytest.approx(2.0 if family == "negative_binomial_log" else 1.0)
    assert summary.prediction_interval is not None
    assert summary.distribution["mean_semantics"] == "expected_count"
    assert expected_key in summary.distribution


def test_new_advanced_artifact_rejects_missing_exposure_metadata(tmp_path: Path) -> None:
    artifact = tmp_path / "posterior.npz"
    np.savez(artifact, w0=np.asarray([[[0.0]], [[0.0]]]), b0=np.zeros((2, 1)), dispersion=np.asarray([2.0, 2.0]))
    spec = PredictorSpec.model_validate({"id": "nb", "target": "events", "unit": "events", "target_kind": "count", "runtime_type": "numpyro.dense_posterior.v1", "architecture_id": "dense_mlp_v1", "artifact": "posterior.npz", "predictive_family": "negative_binomial_log", "feature_names": ["x"], "config": {"advanced_count_contract": "advanced-count-contract/v1"}})
    predictor = NumpyroDensePosteriorAdapter().load(_Artifacts(artifact), spec)
    with pytest.raises(ValueError, match="explicit exposure semantics"):
        predictor.predict({"x": 0.0}, sampling_request=SamplingRequest.create(operation="package_verification", policy_id="advanced-count-contract/v1", seed=792, requested_sample_count=2))


def test_negative_binomial_deviance_is_zero_at_saturated_mean() -> None:
    observed = np.asarray([0.0, 1.0, 5.0, 12.0])
    assert negative_binomial_deviance(observed, observed, np.full(4, 3.0)) == pytest.approx(0.0)


def test_negative_binomial_zero_mass_uses_posterior_nb_probability() -> None:
    assert negative_binomial_zero_mass(np.asarray([1.0]), np.asarray([2.0]))[0] == pytest.approx(4.0 / 9.0)


def test_advanced_count_settings_map_every_shared_inference_field() -> None:
    recipe = estimator_recipe("negative-binomial-regression.v1")
    settings = advanced_count._settings(recipe)
    assert settings.chains == recipe.chains
    assert settings.warmup == recipe.warmup
    assert settings.draws == recipe.draws
    assert settings.max_r_hat == recipe.max_r_hat
    assert settings.min_ess == recipe.min_effective_sample_size
    assert settings.max_divergences == recipe.max_divergences


def test_posterior_log_score_uses_nb_and_zip_draw_masses() -> None:
    identity = SimpleNamespace()
    values = np.asarray([[0.0]])
    nb_fit = advanced_count._Fit(
        np.zeros((2, 1)),
        np.zeros(2),
        np.full(2, 2.0),
        None,
        None,
        identity,
    )
    nb_score = advanced_count._posterior_log_scores(
        np.asarray([0.0]),
        nb_fit,
        values,
        None,
        estimator_recipe("negative-binomial-regression.v1"),
    )
    assert np.exp(nb_score[0]) == pytest.approx(4.0 / 9.0)

    zip_fit = advanced_count._Fit(
        np.zeros((2, 1)),
        np.zeros(2),
        None,
        np.zeros((2, 1)),
        np.zeros(2),
        identity,
    )
    zip_score = advanced_count._posterior_log_scores(
        np.asarray([0.0]),
        zip_fit,
        values,
        None,
        estimator_recipe("zero-inflated-poisson-regression.v1"),
    )
    assert np.exp(zip_score[0]) == pytest.approx(0.5 + 0.5 * math.exp(-1.0))


def test_zip_prediction_keeps_structural_gate_and_total_zero_mixture_distinct() -> None:
    fit = advanced_count._Fit(
        np.zeros((2, 1)),
        np.log(np.asarray([0.1, 10.0])),
        None,
        np.zeros((2, 1)),
        np.asarray([math.log(9.0), -math.log(9.0)]),
        SimpleNamespace(),
    )
    expected, _, _, structural_gate, total_zero = advanced_count._predict(
        fit,
        np.asarray([[0.0]]),
        None,
        estimator_recipe("zero-inflated-poisson-regression.v1"),
        seed=792,
    )
    correct_total = np.mean([0.9 + 0.1 * math.exp(-0.1), 0.1 + 0.9 * math.exp(-10.0)])
    assert expected[0] == pytest.approx(4.505)
    assert structural_gate is not None
    assert structural_gate[0] == pytest.approx(0.5)
    assert total_zero[0] == pytest.approx(correct_total)
    assert total_zero[0] == pytest.approx(0.545262294)


def test_zip_quality_aggregates_total_zero_mass_across_oof_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = compile_target_training_set(
        _canonical((0, 1, 0, 2), (1, 1, 1, 1)),
        target="events",
        unit="events",
        target_kind="count",
        folds=2,
        seed=792,
    )
    identity = SimpleNamespace(model_dump=lambda **_: {"diagnostics": {"status": "passed"}})

    def heterogeneous_zip_fit(values, target, exposures, recipe, *, seed):
        return advanced_count._Fit(
            np.zeros((2, values.shape[1])),
            np.log(np.asarray([0.1, 10.0])),
            None,
            np.zeros((2, values.shape[1])),
            np.asarray([math.log(9.0), -math.log(9.0)]),
            identity,
        )

    monkeypatch.setattr(advanced_count, "_fit", heterogeneous_zip_fit)
    trained = advanced_count.train(
        data,
        estimator_recipe("zero-inflated-poisson-regression.v1"),
        tmp_path / "heterogeneous-zip.npz",
    )
    quality = trained.diagnostics["count_quality"]
    assert quality["structural_zero_gate_rate"] == pytest.approx(0.5)
    assert quality["zero_predicted_rate"] == pytest.approx(0.545262294)


@pytest.mark.parametrize(
    ("fixture", "estimator_id", "strategy", "with_exposure"),
    [
        ("true_poisson", "negative-binomial-regression.v1", "grouped_kfold", False),
        ("overdispersed_nb", "negative-binomial-regression.v1", "grouped_kfold", False),
        ("structural_zip", "zero-inflated-poisson-regression.v1", "grouped_kfold", False),
        ("zero_heavy_non_zip", "negative-binomial-regression.v1", "grouped_kfold", False),
        ("varying_exposure", "negative-binomial-regression.v1", "grouped_kfold", True),
        ("temporal_count", "negative-binomial-regression.v1", "temporal_holdout", True),
    ],
)
def test_injected_synthetic_matrix_runs_compile_train_artifact_runtime_quality(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fixture: str,
    estimator_id: str,
    strategy: str,
    with_exposure: bool,
) -> None:
    rng = np.random.default_rng(792)
    x = np.linspace(-1.5, 1.5, 30)
    exposure = np.linspace(0.5, 2.0, 30) if with_exposure else np.ones(30)
    rate = exposure * np.exp(0.35 * x + 0.7)
    if fixture == "overdispersed_nb":
        values = rng.negative_binomial(2.0, 2.0 / (2.0 + rate))
    else:
        values = rng.poisson(rate)
    if fixture == "structural_zip":
        values[x < 0] = 0
    if fixture == "zero_heavy_non_zip":
        values = rng.negative_binomial(0.7, 0.7 / (0.7 + rate))
    canonical = _canonical(tuple(float(item) for item in values), tuple(float(item) for item in exposure))
    for index, row in enumerate(canonical["rows"]):
        row["features"]["x"] = float(x[index])
    plan = (
        ValidationPlan(strategy="grouped_kfold", folds=3, group_key="parent_key", seed=792)
        if strategy == "grouped_kfold"
        else ValidationPlan(strategy="temporal_holdout", holdout_fraction=0.2, time_key="x", minimum_train_size=10, seed=792)
    )
    data = compile_target_training_set(
        canonical,
        target="events",
        unit="events",
        target_kind="count",
        exposure_input_path="process.exposure" if with_exposure else None,
        validation_plan=plan,
    )
    identity = SimpleNamespace(model_dump=lambda **_: {"diagnostics": {"status": "passed"}})

    def injected_fit(values, target, exposures, recipe, *, seed):
        draws = 16
        coefficients = np.full((draws, values.shape[1]), 0.35)
        intercepts = np.full(draws, 0.7)
        if recipe.estimator_id == "negative-binomial-regression.v1":
            return advanced_count._Fit(coefficients, intercepts, np.full(draws, 2.0), None, None, identity)
        return advanced_count._Fit(coefficients, intercepts, None, np.full_like(coefficients, -1.0), np.zeros(draws), identity)

    monkeypatch.setattr(advanced_count, "_fit", injected_fit)
    trained = advanced_count.train(data, estimator_recipe(estimator_id), tmp_path / f"{fixture}.npz")
    predictor_payload = dict(trained.predictor)
    predictor_payload.pop("inference_identity")
    spec = PredictorSpec.model_validate(predictor_payload)
    runtime = NumpyroDensePosteriorAdapter().load(_Artifacts(trained.artifact), spec)
    request = SamplingRequest.create(operation="package_verification", policy_id="synthetic-count-matrix/v1", seed=792, requested_sample_count=16)
    candidate = {"x": 0.25}
    if with_exposure:
        candidate["process.exposure"] = 1.25
    summary = runtime.predict(candidate, sampling_request=request)
    assert summary.point_estimate >= 0
    assert trained.quality.mean_log_predictive_density is not None
    assert isinstance(trained.diagnostics["count_quality"]["tail_predictive_rate"], float)
    exposure_quality = trained.diagnostics["count_quality"]["exposure_stratified"]
    assert ("low" in exposure_quality) == with_exposure


@pytest.mark.skipif(pytest.importorskip("importlib.util").find_spec("numpyro") is None, reason="backend-science installs runtime-numpyro")
@pytest.mark.parametrize(
    ("estimator_id", "observed", "family"),
    [
        (
            "negative-binomial-regression.v1",
            (0, 1, 1, 0, 2, 1, 4, 2, 5, 3, 8, 4, 7, 6, 11, 8, 14, 9, 16, 13, 20, 16, 25, 21),
            "negative_binomial_log",
        ),
        (
            "zero-inflated-poisson-regression.v1",
            (0, 0, 1, 0, 0, 2, 0, 1, 3, 0, 4, 0, 5, 3, 0, 7, 4, 0, 9, 6, 0, 11, 8, 0),
            "zero_inflated_poisson_log",
        ),
    ],
)
def test_real_numpyro_nb_and_zip_fit_export_and_runtime(
    tmp_path: Path,
    estimator_id: str,
    observed: tuple[int, ...],
    family: str,
) -> None:
    values = np.linspace(-1.5, 1.5, len(observed), dtype=float)[:, None]
    recipe = estimator_recipe(estimator_id)
    fitted = advanced_count._fit(
        values,
        np.asarray(observed, dtype=float),
        None,
        recipe,
        seed=792,
    )
    assert fitted.inference_identity.diagnostics.status == "passed"
    assert fitted.inference_identity.chains == 2
    assert fitted.inference_identity.warmup == 256
    assert fitted.inference_identity.draws == 256

    artifact = tmp_path / f"{estimator_id}.npz"
    np.savez(artifact, **advanced_count._artifact_arrays(fitted, recipe))
    spec = PredictorSpec.model_validate(
        {
            "id": estimator_id,
            "target": "events",
            "unit": "events",
            "target_kind": "count",
            "runtime_type": "numpyro.dense_posterior.v1",
            "architecture_id": "dense_mlp_v1",
            "artifact": artifact.name,
            "predictive_family": family,
            "feature_names": ["x"],
            "config": {
                "advanced_count_contract": "advanced-count-contract/v1",
                "exposure": {"mode": "not_applicable_unexposed_count/v1"},
            },
        }
    )
    summary = NumpyroDensePosteriorAdapter().load(_Artifacts(artifact), spec).predict(
        {"x": 0.25},
        sampling_request=SamplingRequest.create(
            operation="package_verification",
            policy_id="real-count-numpyro-smoke/v1",
            seed=792,
            requested_sample_count=64,
        ),
    )
    assert summary.point_estimate >= 0
    assert summary.prediction_interval is not None
    assert summary.distribution["mean_semantics"] == "expected_count"
