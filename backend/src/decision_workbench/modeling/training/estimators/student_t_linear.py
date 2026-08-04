from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.special import logsumexp
from scipy.stats import t as student_t

from decision_workbench.contracts.inference_policy_contracts import (
    InferenceDiagnostics,
    InferenceIdentity,
)
from decision_workbench.modeling.inference_policy import inference_policy
from decision_workbench.modeling.model_lifecycle import TargetQualityMetric
from decision_workbench.modeling.training.feature_dataset import (
    TargetTrainingSet,
    prepared_feature_matrix,
)
from decision_workbench.modeling.training.recipe import (
    StudentTLinearRegressionEstimatorRecipe,
)

from .types import TrainedPredictor, standard_training_metadata


RUNTIME_TYPE = "numpyro.dense_posterior.v1"
ARTIFACT_SUFFIX = ".npz"
ARTIFACT_FORMAT = "bounded-npz"
_DF_LOWER = 2.1
_DF_UPPER = 30.0
_MAX_FEATURES = 64
_MAX_ROWS = 5_000


@dataclass(frozen=True)
class _InferenceSettings:
    chains: int
    warmup: int
    draws: int
    max_r_hat: float
    min_ess: float
    max_divergences: int


@dataclass(frozen=True)
class _Fit:
    coefficients: np.ndarray
    intercepts: np.ndarray
    observation_scales: np.ndarray
    degrees_of_freedom: np.ndarray
    inference_identity: InferenceIdentity

    @property
    def draws(self) -> int:
        return len(self.intercepts)

    def locations(self, values: np.ndarray) -> np.ndarray:
        return values @ self.coefficients.T + self.intercepts


def _settings(
    recipe: StudentTLinearRegressionEstimatorRecipe,
) -> _InferenceSettings:
    if recipe.inference_preset == "quick-evidence":
        return _InferenceSettings(
            chains=2,
            warmup=96,
            draws=96,
            max_r_hat=1.15,
            min_ess=20.0,
            max_divergences=0,
        )
    return _InferenceSettings(
        chains=2,
        warmup=256,
        draws=256,
        max_r_hat=1.05,
        min_ess=50.0,
        max_divergences=0,
    )


def _dependencies() -> tuple[Any, ...]:
    try:
        import jax.numpy as jnp
        from jax import random
        import numpyro
        import numpyro.distributions as dist
        from numpyro.diagnostics import summary
        from numpyro.infer import MCMC, NUTS
    except ImportError as exc:
        raise RuntimeError(
            "student-t-linear-regression.v1 requires the runtime-numpyro "
            "optional dependency; no fallback estimator was selected"
        ) from exc
    return jnp, random, numpyro, dist, summary, MCMC, NUTS


def _fit(
    values: np.ndarray,
    target: np.ndarray,
    recipe: StudentTLinearRegressionEstimatorRecipe,
    *,
    seed: int,
) -> _Fit:
    if values.shape[1] > _MAX_FEATURES:
        raise ValueError(
            f"Student-t linear recipe supports at most {_MAX_FEATURES} prepared features"
        )
    if not np.isfinite(values).all() or not np.isfinite(target).all():
        raise ValueError(
            "Student-t likelihood does not repair non-finite or malformed training data"
        )
    x_mean = values.mean(axis=0)
    x_scale = values.std(axis=0)
    x_scale[x_scale < 1e-12] = 1.0
    y_mean = float(np.mean(target))
    y_scale = max(float(np.std(target)), 1e-8)
    normalized_x = (values - x_mean) / x_scale
    normalized_y = (target - y_mean) / y_scale
    settings = _settings(recipe)
    jnp, random, numpyro, dist, summarize, MCMC, NUTS = _dependencies()

    def model(features: Any, observed: Any | None = None) -> None:
        coefficients = numpyro.sample(
            "coefficients",
            dist.Normal(0.0, recipe.coefficient_prior_scale)
            .expand((values.shape[1],))
            .to_event(1),
        )
        intercept = numpyro.sample(
            "intercept",
            dist.Normal(0.0, recipe.intercept_prior_scale),
        )
        observation_scale = numpyro.sample(
            "observation_scale",
            dist.HalfNormal(1.0),
        )
        df_unit = numpyro.sample("df_unit", dist.Beta(2.0, 5.0))
        degrees_of_freedom = numpyro.deterministic(
            "degrees_of_freedom",
            _DF_LOWER + (_DF_UPPER - _DF_LOWER) * df_unit,
        )
        location = intercept + jnp.asarray(features) @ coefficients
        numpyro.sample(
            "observed",
            dist.StudentT(
                degrees_of_freedom,
                location,
                observation_scale,
            ),
            obs=observed,
        )

    sampler = MCMC(
        NUTS(model, target_accept_prob=recipe.target_accept_probability),
        num_warmup=settings.warmup,
        num_samples=settings.draws,
        num_chains=settings.chains,
        chain_method="sequential",
        progress_bar=False,
    )
    sampler.run(
        random.PRNGKey(seed),
        jnp.asarray(normalized_x),
        jnp.asarray(normalized_y),
    )
    grouped = sampler.get_samples(group_by_chain=True)
    chain_summary = summarize(
        {
            name: grouped[name]
            for name in (
                "coefficients",
                "intercept",
                "observation_scale",
                "df_unit",
            )
        },
        group_by_chain=True,
    )
    r_hats = np.concatenate([
        np.asarray(item["r_hat"], dtype=float).reshape(-1)
        for item in chain_summary.values()
    ])
    effective_sizes = np.concatenate([
        np.asarray(item["n_eff"], dtype=float).reshape(-1)
        for item in chain_summary.values()
    ])
    divergence_count = int(
        np.asarray(
            sampler.get_extra_fields(group_by_chain=True)["diverging"]
        ).sum()
    )
    if not np.isfinite(r_hats).all() or not np.isfinite(effective_sizes).all():
        raise ValueError("Student-t NUTS diagnostics are non-finite")
    max_r_hat = float(np.max(r_hats))
    min_ess = float(np.min(effective_sizes))
    findings: list[str] = []
    if max_r_hat > settings.max_r_hat:
        findings.append(
            f"max R-hat {max_r_hat:.4g} exceeds {settings.max_r_hat:g}"
        )
    if min_ess < settings.min_ess:
        findings.append(
            f"minimum ESS {min_ess:.4g} is below {settings.min_ess:g}"
        )
    if divergence_count > settings.max_divergences:
        findings.append(
            f"divergences {divergence_count} exceed {settings.max_divergences}"
        )
    diagnostics = InferenceDiagnostics(
        status="failed" if findings else "passed",
        max_r_hat=max_r_hat,
        min_effective_sample_size=min_ess,
        divergence_count=divergence_count,
        findings=tuple(findings),
    )
    if findings:
        raise ValueError(
            "Student-t NUTS diagnostics failed: " + "; ".join(findings)
        )
    inference_identity = InferenceIdentity.create(
        policy=inference_policy("nuts"),
        parameterization=(
            "standardized-linear-location/"
            "bounded-beta-2-5-df-2p1-30/v1"
        ),
        diagnostics=diagnostics,
        seed=seed,
        chains=settings.chains,
        warmup=settings.warmup,
        draws=settings.draws,
        resource_limits={
            "max_rows": _MAX_ROWS,
            "max_features": _MAX_FEATURES,
            "chain_method": "sequential",
        },
        convergence_criteria={
            "max_r_hat": settings.max_r_hat,
            "min_ess": settings.min_ess,
            "max_divergences": settings.max_divergences,
        },
    )
    samples = sampler.get_samples(group_by_chain=False)
    normalized_coefficients = np.asarray(samples["coefficients"], dtype=float)
    coefficients = normalized_coefficients * y_scale / x_scale
    normalized_intercepts = np.asarray(samples["intercept"], dtype=float)
    intercepts = (
        y_mean
        + y_scale * normalized_intercepts
        - coefficients @ x_mean
    )
    observation_scales = (
        np.asarray(samples["observation_scale"], dtype=float) * y_scale
    )
    degrees_of_freedom = (
        _DF_LOWER
        + (_DF_UPPER - _DF_LOWER)
        * np.asarray(samples["df_unit"], dtype=float)
    )
    return _Fit(
        coefficients=coefficients,
        intercepts=intercepts,
        observation_scales=observation_scales,
        degrees_of_freedom=degrees_of_freedom,
        inference_identity=inference_identity,
    )


def _evaluate(
    fit: _Fit,
    values: np.ndarray,
    observed: np.ndarray,
    *,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    locations = fit.locations(values)
    rng = np.random.default_rng(seed)
    predictive_samples = (
        locations
        + fit.observation_scales
        * rng.standard_t(
            fit.degrees_of_freedom,
            size=locations.shape,
        )
    )
    point = locations.mean(axis=1)
    lower, upper = np.quantile(
        predictive_samples,
        (0.05, 0.95),
        axis=1,
    )
    log_density = logsumexp(
        student_t.logpdf(
            observed[:, None],
            fit.degrees_of_freedom,
            loc=locations,
            scale=fit.observation_scales,
        ),
        axis=1,
    ) - np.log(fit.draws)
    return point, lower, upper, log_density


def _honest_predictions(
    data: TargetTrainingSet,
    recipe: StudentTLinearRegressionEstimatorRecipe,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, tuple[dict[str, Any], ...]]:
    points = np.full(len(data.y), np.nan, dtype=float)
    lowers = np.full(len(data.y), np.nan, dtype=float)
    uppers = np.full(len(data.y), np.nan, dtype=float)
    log_densities = np.full(len(data.y), np.nan, dtype=float)
    fold_diagnostics: list[dict[str, Any]] = []
    folds = (0,) if data.is_temporal_validation else range(data.folds)
    for fold in folds:
        train_rows = data.training_rows_for_fold(fold)
        if data.is_temporal_validation:
            train_rows = train_rows | data.temporal_calibration_rows
            evaluate = data.quality_rows
        else:
            evaluate = data.fold_ids == fold
        train_x = prepared_feature_matrix(
            data,
            fit_rows=train_rows,
            transform_rows=train_rows,
        )
        fitted = _fit(
            train_x,
            data.y[train_rows],
            recipe,
            seed=recipe.seed + int(fold),
        )
        evaluate_x = prepared_feature_matrix(
            data,
            fit_rows=train_rows,
            transform_rows=evaluate,
        )
        (
            points[evaluate],
            lowers[evaluate],
            uppers[evaluate],
            log_densities[evaluate],
        ) = _evaluate(
            fitted,
            evaluate_x,
            data.y[evaluate],
            seed=recipe.seed + 10_000 + int(fold),
        )
        fold_diagnostics.append({
            "fold": int(fold),
            "training_rows": int(train_rows.sum()),
            "evaluation_rows": int(evaluate.sum()),
            "inference_identity_digest": (
                fitted.inference_identity.identity_digest
            ),
            "max_r_hat": fitted.inference_identity.diagnostics.max_r_hat,
            "min_effective_sample_size": (
                fitted.inference_identity.diagnostics.min_effective_sample_size
            ),
            "divergence_count": (
                fitted.inference_identity.diagnostics.divergence_count
            ),
        })
    return (
        points,
        lowers,
        uppers,
        log_densities,
        tuple(fold_diagnostics),
    )


def train(
    data: TargetTrainingSet,
    recipe: StudentTLinearRegressionEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if data.target_kind != "continuous":
        raise ValueError("Student-t linear regression supports continuous targets only")
    if len(data.y) > _MAX_ROWS:
        raise ValueError(
            f"Student-t linear recipe supports at most {_MAX_ROWS} rows"
        )
    (
        evaluation_points,
        evaluation_lowers,
        evaluation_uppers,
        evaluation_log_densities,
        fold_diagnostics,
    ) = _honest_predictions(data, recipe)
    quality_rows = data.quality_rows
    residuals = data.y[quality_rows] - evaluation_points[quality_rows]
    absolute_residuals = np.abs(residuals)
    extreme_count = max(1, int(np.ceil(len(residuals) * 0.1)))
    extreme_rows = np.argsort(absolute_residuals)[-extreme_count:]
    final = _fit(
        prepared_feature_matrix(data),
        data.y,
        recipe,
        seed=recipe.seed + 1_000_000,
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        artifact_path,
        w0=final.coefficients[:, :, None],
        b0=final.intercepts[:, None],
        obs_scale=final.observation_scales,
        df=final.degrees_of_freedom,
    )
    quality = TargetQualityMetric(
        target=data.target,
        parent_conditions=len(set(data.validation_groups)),
        mae=float(np.mean(absolute_residuals)),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        median_absolute_error=float(np.median(absolute_residuals)),
        mean_log_predictive_density=float(
            np.mean(evaluation_log_densities[quality_rows])
        ),
        extreme_residual_mae=float(
            np.mean(absolute_residuals[extreme_rows])
        ),
        interval_coverage_90=float(
            np.mean(
                (
                    data.y[quality_rows]
                    >= evaluation_lowers[quality_rows]
                )
                & (
                    data.y[quality_rows]
                    <= evaluation_uppers[quality_rows]
                )
            )
        ),
        interval_coverage_method="posterior-predictive-interval",
        interval_coverage_observations=int(quality_rows.sum()),
        mean_interval_width=float(
            np.mean(
                evaluation_uppers[quality_rows]
                - evaluation_lowers[quality_rows]
            )
        ),
    )
    parameters = recipe.model_dump(
        mode="json",
        exclude={
            "estimator_id",
            "validation_plan",
            "validation_plans_by_target",
        },
        exclude_none=True,
    )
    predictor = {
        "id": f"{data.target.lower()}-student-t-linear",
        "target": data.target,
        "unit": data.unit,
        "target_kind": data.target_kind,
        "runtime_type": RUNTIME_TYPE,
        "architecture_id": "dense_mlp_v1",
        "artifact": artifact_path.as_posix(),
        "predictive_family": "student_t",
        "feature_names": list(data.feature_names),
        "inference_identity": final.inference_identity.model_dump(mode="json"),
        "config": {
            "activation": "tanh",
            "location_statistic": "mean",
            "df_policy": {
                "id": recipe.df_policy,
                "lower_exclusive": 2.0,
                "effective_lower": _DF_LOWER,
                "upper": _DF_UPPER,
            },
            "data_quality_boundary": (
                "unit mismatch, impossible values, parsing errors, duplicate "
                "identity conflicts, and input mistakes remain pre-model rejects"
            ),
            "interval_semantics": (
                "q05-q95 posterior predictive interval for a new observation"
            ),
            "training": standard_training_metadata(
                data,
                estimator_id=recipe.estimator_id,
                uncertainty=(
                    "NUTS posterior draws for linear coefficients, Student-t "
                    "observation scale, and bounded degrees of freedom"
                ),
                parameters=parameters,
            ),
        },
    }
    diagnostics = {
        "estimator_id": recipe.estimator_id,
        "folds": data.folds,
        "cohort_digest": data.cohort_digest,
        "fold_digest": data.fold_digest,
        "evaluation": "outer-fold-refit-with-fold-local-nuts",
        "evaluation_predictive_sampling_seed": recipe.seed + 10_000,
        "fold_inference": fold_diagnostics,
        "final_inference_identity_digest": (
            final.inference_identity.identity_digest
        ),
        "df_summary": {
            "mean": float(np.mean(final.degrees_of_freedom)),
            "q05": float(np.quantile(final.degrees_of_freedom, 0.05)),
            "q95": float(np.quantile(final.degrees_of_freedom, 0.95)),
            "policy": recipe.df_policy,
        },
        "scale_summary": {
            "mean": float(np.mean(final.observation_scales)),
            "q05": float(np.quantile(final.observation_scales, 0.05)),
            "q95": float(np.quantile(final.observation_scales, 0.95)),
        },
        "extreme_residual_subset": {
            "definition": "largest absolute OOF residual decile",
            "observations": extreme_count,
        },
        "limitations": (
            "heavy-tail likelihood does not repair malformed or impossible data",
            "linear location function does not model nonlinear mean structure",
            "marginal target posteriors do not provide cross-target joint samples",
            "bounded df policy is conditional on the fixed 2.1 to 30 range",
        ),
    }
    return TrainedPredictor(
        predictor=predictor,
        artifact=artifact_path,
        quality=quality,
        diagnostics=diagnostics,
        predict=lambda values: float(
            np.mean(final.locations(values.reshape(1, -1))[0])
        ),
        evaluation_predictions=evaluation_points,
    )
