"""Bayesian shrinkage trainers for the safe posterior-linear runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from scipy.special import logsumexp
from scipy.stats import norm

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
    BayesianRidgeEstimatorRecipe,
    HorseshoeLinearEstimatorRecipe,
)

from .types import TrainedPredictor, standard_training_metadata


RUNTIME_TYPE = "builtin.posterior_linear.v1"
ARTIFACT_SUFFIX = ".npz"
ARTIFACT_FORMAT = "bounded-npz"
_MAX_FEATURES = 64
_MAX_ROWS = 5_000
_MAX_TREE_DEPTH = 13
_DENSE_MASS = True
_MAX_SEED = 2_147_483_647
_Z90 = 1.6448536269514722

BayesianShrinkageRecipe = (
    BayesianRidgeEstimatorRecipe | HorseshoeLinearEstimatorRecipe
)


class BayesianTrainingError(RuntimeError):
    """A typed Bayesian build failure with no implicit estimator fallback."""

    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        findings: tuple[str, ...] = (),
        diagnostics: InferenceDiagnostics | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.findings = findings
        self.diagnostics = diagnostics
        self.target: str | None = None

    def bind_target(self, target: str) -> BayesianTrainingError:
        self.target = target
        return self

    def quality_finding(
        self,
        *,
        estimator_id: str,
        target: str,
    ) -> BayesianQualityFinding:
        status: Literal["unavailable", "failed"] = (
            "unavailable"
            if self.reason_code == "unavailable_missing_dependency"
            else "failed"
        )
        return BayesianQualityFinding(
            estimator_id=estimator_id,
            target=target,
            status=status,
            reason_code=self.reason_code,
            message=str(self),
            findings=tuple(self.findings) or (str(self),),
            diagnostics=self.diagnostics,
        )


class BayesianQualityFinding(BaseModel):
    """The typed, caller-facing record for a Bayesian build that cannot export."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["bayesian-quality-finding/v1"] = (
        "bayesian-quality-finding/v1"
    )
    estimator_id: Annotated[str, Field(min_length=1)]
    target: Annotated[str, Field(min_length=1)]
    status: Literal["unavailable", "failed"]
    reason_code: Literal[
        "unavailable_missing_dependency",
        "sampling_failed",
        "sampling_quality_failed",
    ]
    message: Annotated[str, Field(min_length=1)]
    findings: tuple[str, ...] = Field(min_length=1)
    diagnostics: InferenceDiagnostics | None = None


class BayesianTrainingUnavailableError(BayesianTrainingError):
    """The reviewed optional dependency is unavailable."""

    def __init__(self, dependency: str) -> None:
        finding = f"optional dependency {dependency} is unavailable"
        super().__init__(
            "unavailable_missing_dependency",
            f"Bayesian shrinkage requires the {dependency} optional dependency; "
            "no fallback estimator was selected",
            findings=(finding,),
            diagnostics=InferenceDiagnostics(
                status="not_applicable",
                findings=(finding,),
            ),
        )


class BayesianSamplingFailureError(BayesianTrainingError):
    """The requested sampler could not produce a posterior draw set."""

    def __init__(self, message: str, *, cause: Exception | None = None) -> None:
        super().__init__(
            "sampling_failed",
            message,
            findings=(message,),
            diagnostics=InferenceDiagnostics(
                status="failed",
                findings=(message,),
            ),
        )
        if cause is not None:
            self.__cause__ = cause


class BayesianDiagnosticsQualityError(BayesianTrainingError):
    """Posterior diagnostics are below the fixed recipe quality gate."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: InferenceDiagnostics,
    ) -> None:
        super().__init__(
            "sampling_quality_failed",
            message,
            findings=diagnostics.findings,
            diagnostics=diagnostics,
        )


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
    standardized_coefficients: np.ndarray
    intercepts: np.ndarray
    observation_scales: np.ndarray
    local_scales: np.ndarray | None
    inference_identity: InferenceIdentity

    @property
    def draws(self) -> int:
        return len(self.intercepts)

    def locations(self, values: np.ndarray) -> np.ndarray:
        return values @ self.coefficients.T + self.intercepts


def _settings(recipe: BayesianShrinkageRecipe) -> _InferenceSettings:
    return _InferenceSettings(
        chains=int(recipe.chains),
        warmup=int(recipe.warmup),
        draws=int(recipe.draws),
        max_r_hat=float(recipe.max_r_hat),
        min_ess=float(recipe.min_effective_sample_size),
        max_divergences=int(recipe.max_divergences),
    )


def _dependencies() -> tuple[Any, ...]:
    try:
        import jax.numpy as jnp
        from jax import random
        import numpyro
        import numpyro.distributions as dist
        from numpyro.diagnostics import summary
        from numpyro.infer import MCMC, NUTS
        from numpyro.infer.initialization import init_to_median
    except ImportError as exc:
        raise BayesianTrainingUnavailableError("numpyro") from exc
    return jnp, random, numpyro, dist, summary, MCMC, NUTS, init_to_median


def _fit_seed(seed: int, offset: int) -> int:
    return int((int(seed) + int(offset)) % _MAX_SEED)


def _diagnostics_from_summary(
    chain_summary: dict[str, dict[str, Any]],
    divergence_count: int,
    settings: _InferenceSettings,
) -> InferenceDiagnostics:
    r_hats: list[np.ndarray] = []
    effective_sizes: list[np.ndarray] = []
    for values in chain_summary.values():
        if values.get("r_hat") is not None:
            r_hats.append(np.asarray(values["r_hat"], dtype=float).reshape(-1))
        if values.get("n_eff") is not None:
            effective_sizes.append(
                np.asarray(values["n_eff"], dtype=float).reshape(-1)
            )
    flattened_r_hats = (
        np.concatenate(r_hats) if r_hats else np.asarray([], dtype=float)
    )
    flattened_effective_sizes = (
        np.concatenate(effective_sizes)
        if effective_sizes
        else np.asarray([], dtype=float)
    )
    if (
        not len(flattened_r_hats)
        or not len(flattened_effective_sizes)
        or not np.isfinite(flattened_r_hats).all()
        or not np.isfinite(flattened_effective_sizes).all()
    ):
        return InferenceDiagnostics(
            status="failed",
            divergence_count=divergence_count,
            findings=("NUTS R-hat or effective sample size is non-finite",),
        )
    max_r_hat = float(np.max(flattened_r_hats))
    min_ess = float(np.min(flattened_effective_sizes))
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
    return InferenceDiagnostics(
        status="failed" if findings else "passed",
        max_r_hat=max_r_hat,
        min_effective_sample_size=min_ess,
        divergence_count=divergence_count,
        findings=tuple(findings),
    )


def _sampler_model(
    recipe: BayesianShrinkageRecipe,
    *,
    feature_count: int,
    jnp: Any,
    numpyro: Any,
    dist: Any,
) -> Any:
    if recipe.estimator_id == "bayesian-ridge.v1":

        def model(features: Any, observed: Any | None = None) -> None:
            coefficients = numpyro.sample(
                "coefficients",
                dist.Normal(0.0, recipe.coefficient_prior_scale)
                .expand((feature_count,))
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
            location = intercept + jnp.asarray(features) @ coefficients
            numpyro.sample(
                "observed",
                dist.Normal(location, observation_scale),
                obs=observed,
            )

        return model

    assert recipe.estimator_id == "horseshoe-linear.v1"

    def model(features: Any, observed: Any | None = None) -> None:
        global_scale = numpyro.sample("global_scale", dist.HalfNormal(1.0))
        local_scale = numpyro.sample(
            "local_scale",
            dist.HalfCauchy(1.0).expand((feature_count,)).to_event(1),
        )
        # This v1 is a fixed Student-t capped horseshoe variant, not the
        # canonical regularized horseshoe: the canonical prior samples a
        # normal raw coefficient and an inverse-gamma slab auxiliary.  We use
        # a Student-t raw coefficient and a deterministic E[c^2] cap instead,
        # so the persisted recipe name must keep that distinction explicit.
        slab_variance = numpyro.deterministic(
            "slab_variance",
            recipe.slab_scale**2
            * recipe.slab_degrees_of_freedom
            / (recipe.slab_degrees_of_freedom - 2.0),
        )
        shrinkage = jnp.sqrt(
            slab_variance
            * local_scale**2
            / (slab_variance + global_scale**2 * local_scale**2)
        )
        coefficient_scale = numpyro.deterministic(
            "coefficient_scale",
            global_scale * shrinkage,
        )
        coefficient_raw = numpyro.sample(
            "coefficient_raw",
            dist.StudentT(
                recipe.slab_degrees_of_freedom,
                0.0,
                1.0,
            )
            .expand((feature_count,))
            .to_event(1),
        )
        coefficients = numpyro.deterministic(
            "coefficients",
            coefficient_scale * coefficient_raw,
        )
        intercept = numpyro.sample(
            "intercept",
            dist.Normal(0.0, recipe.intercept_prior_scale),
        )
        observation_scale = numpyro.sample(
            "observation_scale",
            dist.HalfNormal(1.0),
        )
        location = intercept + jnp.asarray(features) @ coefficients
        numpyro.sample(
            "observed",
            dist.Normal(location, observation_scale),
            obs=observed,
        )

    return model


def _fit(
    values: np.ndarray,
    target: np.ndarray,
    recipe: BayesianShrinkageRecipe,
    *,
    seed: int,
) -> _Fit:
    if values.ndim != 2 or values.shape[1] > _MAX_FEATURES:
        raise ValueError(
            f"{recipe.estimator_id} supports at most {_MAX_FEATURES} prepared features"
        )
    if len(values) < 2:
        raise ValueError(f"{recipe.estimator_id} needs at least two training rows")
    if not np.isfinite(values).all() or not np.isfinite(target).all():
        raise ValueError(
            f"{recipe.estimator_id} does not repair non-finite or malformed training data"
        )
    x_mean = values.mean(axis=0)
    x_scale = values.std(axis=0)
    x_scale[x_scale < 1e-12] = 1.0
    y_mean = float(np.mean(target))
    y_scale = max(float(np.std(target)), 1e-8)
    normalized_x = (values - x_mean) / x_scale
    normalized_y = (target - y_mean) / y_scale
    settings = _settings(recipe)
    (
        jnp,
        random,
        numpyro,
        dist,
        summarize,
        MCMC,
        NUTS,
        init_to_median,
    ) = _dependencies()
    model = _sampler_model(
        recipe,
        feature_count=values.shape[1],
        jnp=jnp,
        numpyro=numpyro,
        dist=dist,
    )
    sampler = MCMC(
        NUTS(
            model,
            target_accept_prob=recipe.target_accept_probability,
            max_tree_depth=_MAX_TREE_DEPTH,
            dense_mass=_DENSE_MASS,
            init_strategy=init_to_median(num_samples=10),
        ),
        num_warmup=settings.warmup,
        num_samples=settings.draws,
        num_chains=settings.chains,
        chain_method="sequential",
        progress_bar=False,
    )
    try:
        sampler.run(
            random.PRNGKey(_fit_seed(seed, 0)),
            jnp.asarray(normalized_x),
            jnp.asarray(normalized_y),
        )
    except Exception as exc:  # noqa: BLE001 - sampler failures are typed here
        raise BayesianSamplingFailureError(
            f"{recipe.estimator_id} NUTS sampling failed: {exc}",
            cause=exc,
        ) from exc

    grouped = sampler.get_samples(group_by_chain=True)
    summary_names = ["coefficients", "intercept", "observation_scale"]
    if recipe.estimator_id == "horseshoe-linear.v1":
        summary_names.extend(("global_scale", "local_scale"))
    try:
        chain_summary = summarize(
            {name: grouped[name] for name in summary_names},
            group_by_chain=True,
        )
        divergence_values = sampler.get_extra_fields(
            group_by_chain=True,
        ).get("diverging")
        divergence_count = int(
            np.asarray(
                divergence_values if divergence_values is not None else 0,
                dtype=int,
            ).sum()
        )
        diagnostics = _diagnostics_from_summary(
            chain_summary,
            divergence_count,
            settings,
        )
    except Exception as exc:  # noqa: BLE001 - malformed diagnostics are findings
        raise BayesianSamplingFailureError(
            f"{recipe.estimator_id} NUTS diagnostics could not be computed: {exc}",
            cause=exc,
        ) from exc
    if diagnostics.status != "passed":
        raise BayesianDiagnosticsQualityError(
            f"{recipe.estimator_id} NUTS diagnostics failed: "
            + "; ".join(diagnostics.findings),
            diagnostics=diagnostics,
        )

    inference_identity = InferenceIdentity.create(
        policy=inference_policy("nuts"),
        parameterization=recipe.parameterization,
        diagnostics=diagnostics,
        seed=_fit_seed(seed, 0),
        chains=settings.chains,
        warmup=settings.warmup,
        draws=settings.draws,
        resource_limits={
            "max_rows": _MAX_ROWS,
            "max_features": _MAX_FEATURES,
            "chain_method": "sequential",
            "max_tree_depth": _MAX_TREE_DEPTH,
            "dense_mass": "enabled" if _DENSE_MASS else "disabled",
            "init_strategy": "prior-median-10/v1",
        },
        convergence_criteria={
            "max_r_hat": settings.max_r_hat,
            "min_ess": settings.min_ess,
            "max_divergences": settings.max_divergences,
        },
    )
    samples = sampler.get_samples(group_by_chain=False)
    normalized_coefficients = np.asarray(
        samples["coefficients"],
        dtype=float,
    )
    coefficients = normalized_coefficients * y_scale / x_scale
    normalized_intercepts = np.asarray(samples["intercept"], dtype=float)
    intercepts = y_mean + y_scale * normalized_intercepts - coefficients @ x_mean
    observation_scales = (
        np.asarray(samples["observation_scale"], dtype=float) * y_scale
    )
    local_scales = None
    if recipe.estimator_id == "horseshoe-linear.v1":
        local_scales = np.asarray(samples["local_scale"], dtype=float)
    if (
        coefficients.ndim != 2
        or coefficients.shape[1] != values.shape[1]
        or intercepts.shape != (len(coefficients),)
        or observation_scales.shape != (len(coefficients),)
        or np.any(observation_scales <= 0)
        or (
            local_scales is not None
            and (
                local_scales.shape != coefficients.shape
                or np.any(local_scales <= 0)
            )
        )
        or not np.isfinite(coefficients).all()
        or not np.isfinite(intercepts).all()
        or not np.isfinite(observation_scales).all()
    ):
        raise BayesianSamplingFailureError(
            f"{recipe.estimator_id} produced an invalid posterior draw shape"
        )
    return _Fit(
        coefficients=coefficients,
        standardized_coefficients=normalized_coefficients,
        intercepts=intercepts,
        observation_scales=observation_scales,
        local_scales=local_scales,
        inference_identity=inference_identity,
    )


def _evaluate(
    fit: _Fit,
    values: np.ndarray,
    observed: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    locations = fit.locations(values)
    predictive_std = np.sqrt(
        np.var(locations, axis=1) + np.mean(fit.observation_scales**2)
    )
    point = locations.mean(axis=1)
    lower = point - _Z90 * predictive_std
    upper = point + _Z90 * predictive_std
    log_density = logsumexp(
        norm.logpdf(
            observed[:, None],
            loc=locations,
            scale=fit.observation_scales,
        ),
        axis=1,
    ) - np.log(fit.draws)
    return point, lower, upper, log_density


def _honest_predictions(
    data: TargetTrainingSet,
    recipe: BayesianShrinkageRecipe,
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
        try:
            fitted = _fit(
                train_x,
                data.y[train_rows],
                recipe,
                seed=_fit_seed(recipe.seed, int(fold)),
            )
        except BayesianTrainingError as exc:
            raise exc.bind_target(data.target) from exc
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
        ) = _evaluate(fitted, evaluate_x, data.y[evaluate])
        fold_diagnostics.append({
            "fold": int(fold),
            "training_rows": int(train_rows.sum()),
            "evaluation_rows": int(evaluate.sum()),
            "inference_identity": fitted.inference_identity.model_dump(mode="json"),
        })
    if not (
        np.isfinite(points).all()
        and np.isfinite(lowers).all()
        and np.isfinite(uppers).all()
        and np.isfinite(log_densities).all()
    ):
        raise BayesianSamplingFailureError(
            f"{recipe.estimator_id} produced incomplete cross-fitted predictions"
        )
    return points, lowers, uppers, log_densities, tuple(fold_diagnostics)


def _coefficient_summary(
    fit: _Fit,
    feature_names: tuple[str, ...],
    *,
    rope_half_width: float,
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for index, name in enumerate(feature_names):
        values = fit.standardized_coefficients[:, index]
        rows[name] = {
            "mean": float(np.mean(values)),
            "q05": float(np.quantile(values, 0.05)),
            "q50": float(np.quantile(values, 0.50)),
            "q95": float(np.quantile(values, 0.95)),
            "sign_probability_positive": float(np.mean(values > 0.0)),
            "rope_outside_probability": float(
                np.mean(np.abs(values) > rope_half_width)
            ),
        }
    return {
        "semantic": (
            "posterior coefficient evidence on standardized predictor and response "
            "scales; sign and ROPE probabilities are not intervention claims or "
            "rankings"
        ),
        "coefficient_scale": "standardized_predictor_and_response",
        "rope_half_width": rope_half_width,
        "rope_semantics": (
            "unitless ROPE on standardized coefficients; positive unit conversion "
            "does not change the evidence"
        ),
        "features": rows,
    }


def _correlation_caution(
    values: np.ndarray,
    feature_names: tuple[str, ...],
) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    if values.shape[1] >= 2:
        correlation = np.corrcoef(values, rowvar=False)
        for left in range(values.shape[1]):
            for right in range(left + 1, values.shape[1]):
                absolute = abs(float(correlation[left, right]))
                if np.isfinite(absolute) and absolute >= 0.8:
                    pairs.append({
                        "left": feature_names[left],
                        "right": feature_names[right],
                        "absolute_correlation": absolute,
                    })
    return {
        "policy": (
            "correlated inputs can exchange posterior coefficient mass; interpret "
            "sign and ROPE evidence jointly with the correlation structure"
        ),
        "threshold_absolute_correlation": 0.8,
        "high_correlation_pairs": pairs[:100],
    }


def train(
    data: TargetTrainingSet,
    recipe: BayesianShrinkageRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if data.target_kind != "continuous":
        raise ValueError(
            "Bayesian shrinkage linear regression supports continuous targets only"
        )
    if len(data.y) > _MAX_ROWS:
        raise ValueError(f"{recipe.estimator_id} supports at most {_MAX_ROWS} rows")
    if len(data.feature_names) > _MAX_FEATURES:
        raise ValueError(
            f"{recipe.estimator_id} supports at most {_MAX_FEATURES} prepared features"
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
    try:
        final = _fit(
            prepared_feature_matrix(data),
            data.y,
            recipe,
            seed=_fit_seed(recipe.seed, 1_000_000),
        )
    except BayesianTrainingError as exc:
        raise exc.bind_target(data.target) from exc
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    arrays: dict[str, np.ndarray] = {
        "beta_draws": final.coefficients,
        "intercept_draws": final.intercepts,
        "noise_scale_draws": final.observation_scales,
    }
    if final.local_scales is not None:
        arrays["local_scale_draws"] = final.local_scales
    np.savez(artifact_path, **arrays)
    quality = TargetQualityMetric(
        target=data.target,
        parent_conditions=len(set(data.validation_groups)),
        mae=float(np.mean(absolute_residuals)),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        median_absolute_error=float(np.median(absolute_residuals)),
        mean_log_predictive_density=float(
            np.mean(evaluation_log_densities[quality_rows])
        ),
        extreme_residual_mae=float(np.mean(absolute_residuals[extreme_rows])),
        interval_coverage_90=float(
            np.mean(
                (data.y[quality_rows] >= evaluation_lowers[quality_rows])
                & (data.y[quality_rows] <= evaluation_uppers[quality_rows])
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
    prepared = prepared_feature_matrix(data)
    predictor = {
        "id": f"{data.target.lower()}-{recipe.estimator_id.removesuffix('.v1')}",
        "target": data.target,
        "unit": data.unit,
        "target_kind": data.target_kind,
        "runtime_type": RUNTIME_TYPE,
        "architecture_id": "posterior_linear_v1",
        "artifact": artifact_path.as_posix(),
        "predictive_family": "normal",
        "feature_names": list(data.feature_names),
        "inference_identity": final.inference_identity.model_dump(mode="json"),
        "config": {
            "method": recipe.estimator_id,
            "output_representation": "moment_matched_normal",
            "prediction_uncertainty": (
                "posterior-predictive normal approximation combining coefficient "
                "draw variation and observation-scale draws"
            ),
            "coefficient_evidence": (
                "sign probability and ROPE-outside probability describe fitted "
                "coefficient uncertainty; they are not prediction intervals"
            ),
            "feature_policy": "all prepared features are retained; no deletion",
            "training": standard_training_metadata(
                data,
                estimator_id=recipe.estimator_id,
                uncertainty=(
                    "NUTS posterior draws for linear coefficients and observation "
                    "scale in the safe posterior-linear runtime"
                ),
                parameters=parameters,
            ),
        },
    }
    diagnostics: dict[str, Any] = {
        "estimator_id": recipe.estimator_id,
        "folds": data.folds,
        "cohort_digest": data.cohort_digest,
        "fold_digest": data.fold_digest,
        "evaluation": "outer-fold-refit-with-fold-local-nuts",
        "fold_inference": fold_diagnostics,
        "final_inference_identity": final.inference_identity.model_dump(mode="json"),
        "final_inference_identity_digest": final.inference_identity.identity_digest,
        "coefficient_summary": _coefficient_summary(
            final,
            data.feature_names,
            rope_half_width=float(recipe.rope_half_width),
        ),
        "correlation_caution": _correlation_caution(
            prepared,
            data.feature_names,
        ),
        "prediction_uncertainty_semantics": (
            "posterior-predictive uncertainty for a new observation; it is distinct "
            "from coefficient sign and ROPE evidence"
        ),
        "scale_summary": {
            "mean": float(np.mean(final.observation_scales)),
            "q05": float(np.quantile(final.observation_scales, 0.05)),
            "q95": float(np.quantile(final.observation_scales, 0.95)),
        },
        "quality_findings": [],
        "adoption_status": "experimental",
        "interpretation": (
            "This package records an experimental shrinkage candidate for explicit "
            "same-cohort comparison; it does not select a winner or activate itself."
        ),
        "extreme_residual_subset": {
            "definition": "largest absolute OOF residual decile",
            "observations": extreme_count,
        },
        "limitations": (
            "NUTS diagnostics are required to pass the fixed R-hat, ESS, and "
            "divergence thresholds before an artifact is written.",
            "Correlated inputs can make marginal coefficient evidence unstable "
            "even when predictions remain useful.",
            "No raw Python model object or sampler state is stored in the Package.",
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
