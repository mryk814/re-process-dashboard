"""Fold-honest NB and ZIP count candidates exported to the safe posterior runtime."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from decision_workbench.contracts.inference_policy_contracts import InferenceIdentity
from decision_workbench.modeling.inference_policy import inference_policy
from decision_workbench.modeling.model_lifecycle import TargetQualityMetric
from decision_workbench.modeling.training.feature_dataset import TargetTrainingSet, prepared_feature_matrix
from decision_workbench.modeling.training.recipe import (
    BAYESIAN_INFERENCE_SEED_MODULUS,
    BAYESIAN_MAX_FEATURES,
    BAYESIAN_MAX_ROWS,
    NegativeBinomialRegressionEstimatorRecipe,
    ZeroInflatedPoissonRegressionEstimatorRecipe,
    bayesian_inference_resource_limits,
    effective_bayesian_final_inference_seed,
)

from .bayesian_linear import _InferenceSettings, _diagnostics_from_summary
from .types import TrainedPredictor, standard_training_metadata

RUNTIME_TYPE = "numpyro.dense_posterior.v1"
ARTIFACT_SUFFIX = ".npz"
ARTIFACT_FORMAT = "bounded-npz"
CountRecipe = NegativeBinomialRegressionEstimatorRecipe | ZeroInflatedPoissonRegressionEstimatorRecipe


class AdvancedCountTrainingError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code

    def bind_target(self, target: str) -> "AdvancedCountTrainingError":
        return type(self)(self.reason_code, f"{target}: {self}")


class AdvancedCountSamplingError(AdvancedCountTrainingError):
    pass


@dataclass(frozen=True)
class _Fit:
    coefficients: np.ndarray
    intercepts: np.ndarray
    dispersion: np.ndarray | None
    zero_gate_coefficients: np.ndarray | None
    zero_gate_intercepts: np.ndarray | None
    inference_identity: InferenceIdentity

    def parameters(self, values: np.ndarray, exposure: np.ndarray | None) -> tuple[np.ndarray, np.ndarray | None, np.ndarray | None]:
        eta = values @ self.coefficients.T + self.intercepts
        if exposure is not None:
            eta = eta + np.log(exposure)[:, None]
        mean = np.exp(np.clip(eta, -30, 30))
        gates = None
        if self.zero_gate_coefficients is not None and self.zero_gate_intercepts is not None:
            gates = 1.0 / (1.0 + np.exp(-(values @ self.zero_gate_coefficients.T + self.zero_gate_intercepts)))
        return mean, None if self.dispersion is None else self.dispersion[None, :], gates


def _dependencies():
    try:
        import jax.numpy as jnp
        from jax import random
        import numpyro
        import numpyro.distributions as dist
        from numpyro.diagnostics import summary
        from numpyro.infer import MCMC, NUTS, init_to_median
    except ImportError as exc:
        raise AdvancedCountTrainingError(
            "missing_dependency",
            "advanced count recipes require the numpyro optional dependency; no fallback is selected",
        ) from exc
    return jnp, random, numpyro, dist, summary, MCMC, NUTS, init_to_median


def _settings(recipe: CountRecipe) -> _InferenceSettings:
    return _InferenceSettings(
        chains=recipe.chains,
        warmup=recipe.warmup,
        draws=recipe.draws,
        max_r_hat=recipe.max_r_hat,
        min_ess=recipe.min_effective_sample_size,
        max_divergences=recipe.max_divergences,
    )


def _fit_seed(seed: int, fold: int = 0) -> int:
    return (int(seed) + fold * 10_007) % BAYESIAN_INFERENCE_SEED_MODULUS


def _sampler_model(recipe: CountRecipe, *, feature_count: int, jnp: Any, numpyro: Any, dist: Any):
    def model(features: Any, observed: Any, log_exposure: Any | None = None) -> None:
        coefficients = numpyro.sample(
            "coefficients",
            dist.Normal(0.0, recipe.coefficient_prior_scale).expand((feature_count,)).to_event(1),
        )
        intercept = numpyro.sample("intercept", dist.Normal(0.0, recipe.intercept_prior_scale))
        eta = jnp.asarray(features) @ coefficients + intercept
        if log_exposure is not None:
            eta = eta + jnp.asarray(log_exposure)
        mean = jnp.exp(jnp.clip(eta, -30, 30))
        if recipe.estimator_id == "negative-binomial-regression.v1":
            dispersion = numpyro.sample("dispersion", dist.LogNormal(0.0, 1.0))
            numpyro.sample("observed", dist.NegativeBinomial2(mean=mean, concentration=dispersion), obs=observed)
        else:
            gate_coefficients = numpyro.sample("gate_coefficients", dist.Normal(0.0, recipe.zero_gate_prior_scale).expand((feature_count,)).to_event(1))
            gate_intercept = numpyro.sample("gate_intercept", dist.Normal(0.0, recipe.zero_gate_prior_scale))
            gate = 1.0 / (1.0 + jnp.exp(-(jnp.asarray(features) @ gate_coefficients + gate_intercept)))
            numpyro.sample("observed", dist.ZeroInflatedPoisson(gate=gate, rate=mean), obs=observed)
    return model


def _fit(values: np.ndarray, target: np.ndarray, exposure: np.ndarray | None, recipe: CountRecipe, *, seed: int) -> _Fit:
    if values.ndim != 2 or values.shape[1] > BAYESIAN_MAX_FEATURES:
        raise ValueError(f"{recipe.estimator_id} supports at most {BAYESIAN_MAX_FEATURES} prepared features")
    if len(values) < 2 or len(values) > BAYESIAN_MAX_ROWS:
        raise ValueError(f"{recipe.estimator_id} requires 2..{BAYESIAN_MAX_ROWS} rows")
    if not np.isfinite(values).all() or not np.isfinite(target).all() or np.any(target < 0) or not np.array_equal(target, target.astype(int)):
        raise ValueError(f"{recipe.estimator_id} does not repair malformed count training data")
    if exposure is not None and (not np.isfinite(exposure).all() or np.any(exposure <= 0)):
        raise ValueError(f"{recipe.estimator_id} requires an explicit positive exposure offset")
    x_mean, x_scale = values.mean(axis=0), values.std(axis=0)
    x_scale[x_scale < 1e-12] = 1.0
    normalized = (values - x_mean) / x_scale
    settings = _settings(recipe)
    jnp, random, numpyro, dist, summarize, MCMC, NUTS, init_to_median = _dependencies()
    sampler = MCMC(
        NUTS(_sampler_model(recipe, feature_count=values.shape[1], jnp=jnp, numpyro=numpyro, dist=dist), target_accept_prob=recipe.target_accept_probability, max_tree_depth=recipe.max_tree_depth, dense_mass=recipe.dense_mass == "enabled", init_strategy=init_to_median(num_samples=10)),
        num_warmup=settings.warmup, num_samples=settings.draws, num_chains=settings.chains,
        chain_method=recipe.chain_method, progress_bar=False,
    )
    try:
        sampler.run(random.PRNGKey(_fit_seed(seed)), jnp.asarray(normalized), jnp.asarray(target.astype(int)), None if exposure is None else jnp.asarray(np.log(exposure)))
        grouped = sampler.get_samples(group_by_chain=True)
        names = ["coefficients", "intercept", "dispersion"] if recipe.estimator_id == "negative-binomial-regression.v1" else ["coefficients", "intercept", "gate_coefficients", "gate_intercept"]
        diagnostics = _diagnostics_from_summary(summarize({name: grouped[name] for name in names}, group_by_chain=True), int(np.asarray(sampler.get_extra_fields(group_by_chain=True).get("diverging", 0)).sum()), settings)
    except AdvancedCountTrainingError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AdvancedCountSamplingError("sampling_failed", f"{recipe.estimator_id} NUTS sampling failed: {exc}") from exc
    if diagnostics.status != "passed":
        raise AdvancedCountSamplingError("sampling_quality_failed", f"{recipe.estimator_id} diagnostics failed: " + "; ".join(diagnostics.findings))
    samples = sampler.get_samples(group_by_chain=False)
    normalized_coefficients = np.asarray(samples["coefficients"], dtype=float)
    coefficients = normalized_coefficients / x_scale
    intercepts = np.asarray(samples["intercept"], dtype=float) - coefficients @ x_mean
    dispersion = np.asarray(samples["dispersion"], dtype=float) if recipe.estimator_id == "negative-binomial-regression.v1" else None
    if not np.isfinite(coefficients).all() or not np.isfinite(intercepts).all() or (dispersion is not None and (not np.isfinite(dispersion).all() or np.any(dispersion <= 0))):
        raise AdvancedCountSamplingError("sampling_failed", f"{recipe.estimator_id} produced invalid posterior draws")
    gate_coefficients = np.asarray(samples["gate_coefficients"], dtype=float) / x_scale if recipe.estimator_id == "zero-inflated-poisson-regression.v1" else None
    gate_intercepts = np.asarray(samples["gate_intercept"], dtype=float) - gate_coefficients @ x_mean if gate_coefficients is not None else None
    if gate_coefficients is not None and (not np.isfinite(gate_coefficients).all() or not np.isfinite(gate_intercepts).all()):
        raise AdvancedCountSamplingError("sampling_failed", f"{recipe.estimator_id} produced invalid zero-gate draws")
    return _Fit(coefficients, intercepts, dispersion, gate_coefficients, gate_intercepts, InferenceIdentity.create(policy=inference_policy("nuts"), parameterization=recipe.parameterization, diagnostics=diagnostics, seed=_fit_seed(seed), chains=settings.chains, warmup=settings.warmup, draws=settings.draws, resource_limits=bayesian_inference_resource_limits(recipe), convergence_criteria={"max_r_hat": settings.max_r_hat, "min_ess": settings.min_ess, "max_divergences": settings.max_divergences}))


def negative_binomial_zero_mass(mean: np.ndarray, dispersion: np.ndarray) -> np.ndarray:
    """Return P(Y=0) for matching posterior NB2 mean/dispersion draws."""
    mean_values = np.asarray(mean, dtype=float)
    dispersion_values = np.asarray(dispersion, dtype=float)
    if np.any(mean_values < 0) or np.any(dispersion_values <= 0):
        raise ValueError("negative-binomial zero mass requires nonnegative means and positive dispersion")
    return np.power(dispersion_values / (dispersion_values + mean_values), dispersion_values)


def _predict(fit: _Fit, values: np.ndarray, exposure: np.ndarray | None, recipe: CountRecipe, *, seed: int) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray]:
    means, dispersions, gates = fit.parameters(values, exposure)
    rng = np.random.default_rng(seed)
    if recipe.estimator_id == "negative-binomial-regression.v1":
        assert dispersions is not None
        samples = rng.poisson(rng.gamma(shape=dispersions, scale=means / dispersions))
        zero_mass = negative_binomial_zero_mass(means, dispersions)
        return means.mean(axis=1), samples, dispersions.mean(axis=1), zero_mass.mean(axis=1)
    # The second posterior output is deliberately a fixed gate intercept for v1;
    # its probability remains distinct from the expected count.
    assert gates is not None
    gate = gates
    samples = rng.poisson(means)
    samples[rng.random(samples.shape) < gate] = 0
    return ((1.0 - gate) * means).mean(axis=1), samples, None, gate.mean(axis=1)


def _posterior_log_scores(
    observed: np.ndarray,
    fit: _Fit,
    values: np.ndarray,
    exposure: np.ndarray | None,
    recipe: CountRecipe,
) -> np.ndarray:
    """Log posterior-predictive mass per row, mixed across parameter draws."""
    means, dispersions, gates = fit.parameters(values, exposure)
    rows: list[float] = []
    for row, y in enumerate(observed.astype(int)):
        draw_means = np.maximum(means[row], 1e-15)
        if recipe.estimator_id == "negative-binomial-regression.v1":
            assert dispersions is not None
            draw_dispersion = np.maximum(dispersions[0], 1e-15)
            log_mass = (
                np.asarray([math.lgamma(y + float(item)) - math.lgamma(float(item)) for item in draw_dispersion])
                - math.lgamma(y + 1)
                + draw_dispersion * np.log(draw_dispersion / (draw_dispersion + draw_means))
                + y * np.log(draw_means / (draw_dispersion + draw_means))
            )
        else:
            assert gates is not None
            draw_gates = np.clip(gates[row], 1e-15, 1 - 1e-15)
            poisson_log = y * np.log(draw_means) - draw_means - math.lgamma(y + 1)
            if y == 0:
                log_mass = np.log(draw_gates + (1 - draw_gates) * np.exp(-draw_means))
            else:
                log_mass = np.log1p(-draw_gates) + poisson_log
        maximum = float(np.max(log_mass))
        rows.append(maximum + math.log(float(np.mean(np.exp(log_mass - maximum)))))
    return np.asarray(rows, dtype=float)


def negative_binomial_deviance(observed: np.ndarray, mean: np.ndarray, dispersion: np.ndarray) -> float:
    terms = []
    for y, mu, r in zip(observed, mean, dispersion, strict=True):
        first = 0.0 if y == 0 else y * math.log(y / mu)
        terms.append(2.0 * (first - (y + r) * math.log((y + r) / (mu + r))))
    return float(np.mean(terms))


def _artifact_arrays(fit: _Fit, recipe: CountRecipe) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {
        "w0": fit.coefficients[:, :, None],
        "b0": fit.intercepts[:, None],
    }
    if fit.dispersion is not None:
        arrays["dispersion"] = fit.dispersion
    elif recipe.estimator_id == "zero-inflated-poisson-regression.v1":
        arrays["w0"] = np.concatenate((arrays["w0"], np.zeros_like(arrays["w0"])), axis=2)
        assert fit.zero_gate_coefficients is not None and fit.zero_gate_intercepts is not None
        arrays["w0"][:, :, 1] = fit.zero_gate_coefficients
        arrays["b0"] = np.column_stack((arrays["b0"][:, 0], fit.zero_gate_intercepts))
    return arrays


def train(data: TargetTrainingSet, recipe: CountRecipe, artifact_path: Path) -> TrainedPredictor:
    if data.target_kind != "count":
        raise ValueError(f"{recipe.estimator_id} requires a count target")
    if not np.array_equal(data.y, data.y.astype(int)) or np.any(data.y < 0):
        raise ValueError(f"{recipe.estimator_id} requires nonnegative integer observations")
    expected = np.full(len(data.y), np.nan)
    lower = np.full(len(data.y), np.nan)
    upper = np.full(len(data.y), np.nan)
    zero_probability = np.full(len(data.y), np.nan)
    dispersions = np.full(len(data.y), np.nan)
    predictive_tail = np.full(len(data.y), np.nan)
    posterior_log_scores = np.full(len(data.y), np.nan)
    tail_threshold = float(np.quantile(data.y[data.quality_rows], 0.9))
    fold_diagnostics: list[dict[str, Any]] = []
    folds = (0,) if data.is_temporal_validation else range(data.folds)
    for fold in folds:
        train_rows = data.training_rows_for_fold(fold)
        if data.is_temporal_validation:
            train_rows = train_rows | data.temporal_calibration_rows
            evaluate = data.quality_rows
        else:
            evaluate = data.fold_ids == fold
        fitted = _fit(prepared_feature_matrix(data, fit_rows=train_rows, transform_rows=train_rows), data.y[train_rows], None if data.exposure is None else data.exposure[train_rows], recipe, seed=_fit_seed(recipe.seed, int(fold)))
        evaluation_values = prepared_feature_matrix(data, fit_rows=train_rows, transform_rows=evaluate)
        evaluation_exposure = None if data.exposure is None else data.exposure[evaluate]
        mean, samples, fold_dispersion, fold_zero = _predict(fitted, evaluation_values, evaluation_exposure, recipe, seed=_fit_seed(recipe.seed, int(fold) + 1_000))
        expected[evaluate] = mean
        lower[evaluate], upper[evaluate] = np.quantile(samples, (0.05, 0.95), axis=1).astype(int)
        zero_probability[evaluate] = fold_zero
        predictive_tail[evaluate] = np.mean(samples >= tail_threshold, axis=1)
        posterior_log_scores[evaluate] = _posterior_log_scores(data.y[evaluate], fitted, evaluation_values, evaluation_exposure, recipe)
        if fold_dispersion is not None:
            dispersions[evaluate] = fold_dispersion
        fold_diagnostics.append({"fold": int(fold), "training_rows": int(train_rows.sum()), "evaluation_rows": int(evaluate.sum()), "inference_identity": fitted.inference_identity.model_dump(mode="json")})
    quality_rows = data.quality_rows
    if not np.isfinite(expected[quality_rows]).all():
        raise AdvancedCountSamplingError("sampling_failed", f"{recipe.estimator_id} produced incomplete fold-safe predictions")
    final = _fit(prepared_feature_matrix(data), data.y, data.exposure, recipe, seed=effective_bayesian_final_inference_seed(recipe.seed))
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(artifact_path, **_artifact_arrays(final, recipe))
    observed = data.y[quality_rows]
    evaluated = expected[quality_rows]
    parameters = recipe.model_dump(mode="json", exclude={"estimator_id", "validation_plan", "validation_plans_by_target"}, exclude_none=True)
    quality = TargetQualityMetric(target=data.target, parent_conditions=len(set(data.validation_groups)), mae=float(np.mean(np.abs(observed - evaluated))), rmse=float(np.sqrt(np.mean((observed - evaluated) ** 2))), median_absolute_error=float(np.median(np.abs(observed - evaluated))), mean_log_predictive_density=float(np.mean(posterior_log_scores[quality_rows])), extreme_residual_mae=float(np.mean(np.abs(observed[observed >= np.quantile(observed, 0.9)] - evaluated[observed >= np.quantile(observed, 0.9)]))), interval_coverage_90=float(np.mean((observed >= lower[quality_rows]) & (observed <= upper[quality_rows]))), interval_coverage_method="posterior-predictive-interval", interval_coverage_observations=int(quality_rows.sum()), mean_interval_width=float(np.mean(upper[quality_rows] - lower[quality_rows])))
    family = "negative_binomial_log" if recipe.estimator_id == "negative-binomial-regression.v1" else "zero_inflated_poisson_log"
    predictor = {"id": f"{data.target.lower()}-{recipe.estimator_id.removesuffix('.v1')}", "target": data.target, "unit": data.unit, "target_kind": "count", "runtime_type": RUNTIME_TYPE, "architecture_id": "dense_mlp_v1", "artifact": artifact_path.as_posix(), "predictive_family": family, "feature_names": list(data.feature_names), "inference_identity": final.inference_identity.model_dump(mode="json"), "config": {"advanced_count_contract": "advanced-count-contract/v1", "activation": "tanh", "interval_semantics": "q05-q95 posterior-predictive count interval", "exposure": ({"mode": "explicit_offset/v1", "input_path": data.exposure_input_path} if data.exposure_input_path is not None else {"mode": "not_applicable_unexposed_count/v1"}), "training": standard_training_metadata(data, estimator_id=recipe.estimator_id, uncertainty="NUTS posterior predictive count distribution", parameters=parameters, effective_inference_seed=effective_bayesian_final_inference_seed(recipe.seed))}}
    exposure_diagnostics = {"status": "not_applicable"}
    if data.exposure is not None:
        exposed = data.exposure[quality_rows]
        median = float(np.median(exposed))
        exposure_diagnostics = {
            "low": {"observed_mean": float(observed[exposed <= median].mean()), "expected_mean": float(evaluated[exposed <= median].mean())},
            "high": {"observed_mean": float(observed[exposed > median].mean()), "expected_mean": float(evaluated[exposed > median].mean())},
        }
    poisson_terms = evaluated - observed
    positive_observed = observed > 0
    poisson_terms[positive_observed] += observed[positive_observed] * np.log(
        observed[positive_observed] / np.maximum(evaluated[positive_observed], 1e-15)
    )
    poisson_deviance = float(2 * np.mean(poisson_terms))
    nb_dispersion = dispersions[quality_rows] if np.isfinite(dispersions[quality_rows]).all() else np.full(len(observed), 1.0)
    nb_deviance = negative_binomial_deviance(observed, evaluated, nb_dispersion)
    if recipe.estimator_id == "negative-binomial-regression.v1":
        predicted_zero_rate = float(np.mean(zero_probability[quality_rows]))
    else:
        gates = zero_probability[quality_rows]
        count_means = evaluated / np.maximum(1 - gates, 1e-15)
        predicted_zero_rate = float(np.mean(gates + (1 - gates) * np.exp(-count_means)))
    return TrainedPredictor(predictor=predictor, artifact=artifact_path, quality=quality, diagnostics={"estimator_id": recipe.estimator_id, "folds": fold_diagnostics, "count_contract": {"target_eligibility": "nonnegative_integer", "observed_zero_rate": float(np.mean(data.y == 0)), "observed_mean": float(np.mean(data.y)), "observed_variance": float(np.var(data.y)), "exposure": predictor["config"]["exposure"]}, "count_quality": {"poisson_deviance": poisson_deviance, "negative_binomial_deviance": nb_deviance, "zero_observed_rate": float(np.mean(observed == 0)), "zero_predicted_rate": predicted_zero_rate, "tail_observed_rate": float(np.mean(observed >= tail_threshold)), "tail_predictive_rate": float(np.mean(predictive_tail[quality_rows])), "exposure_stratified": exposure_diagnostics}}, predict=lambda values: float(np.mean(_predict(final, values, None, recipe, seed=recipe.seed)[0])), evaluation_predictions=expected)
