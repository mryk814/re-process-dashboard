"""Fold-honest standard ordered-logit training with safe posterior export."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from decision_workbench.contracts.inference_policy_contracts import (
    InferenceIdentity,
)
from decision_workbench.modeling.inference_policy import inference_policy
from decision_workbench.modeling.model_lifecycle import TargetQualityMetric
from decision_workbench.modeling.training.feature_dataset import (
    TargetTrainingSet,
    prepared_feature_matrix,
)
from decision_workbench.modeling.training.recipe import (
    BAYESIAN_INFERENCE_SEED_MODULUS,
    BAYESIAN_MAX_FEATURES,
    BAYESIAN_MAX_ROWS,
    OrderedLogitEstimatorRecipe,
    bayesian_inference_resource_limits,
    effective_bayesian_final_inference_seed,
)

from .bayesian_linear import _diagnostics_from_summary, _InferenceSettings
from .types import TrainedPredictor, standard_training_metadata

RUNTIME_TYPE = "numpyro.dense_posterior.v1"
ARTIFACT_SUFFIX = ".npz"
ARTIFACT_FORMAT = "bounded-npz"


class OrderedLogitTrainingError(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.target: str | None = None

    def bind_target(self, target: str) -> "OrderedLogitTrainingError":
        self.target = target
        return self


class OrderedLogitTrainingUnavailableError(OrderedLogitTrainingError):
    def __init__(self, dependency: str) -> None:
        super().__init__(
            "unavailable_missing_dependency",
            f"ordered-logit.v1 requires the {dependency} optional dependency; "
            "no continuous, nominal, or other estimator fallback was selected",
        )


class OrderedLogitSamplingError(OrderedLogitTrainingError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(reason_code, message)


@dataclass(frozen=True)
class _Fit:
    coefficients: np.ndarray
    biases: np.ndarray
    thresholds: np.ndarray
    inference_identity: InferenceIdentity

    @property
    def draws(self) -> int:
        return len(self.biases)

    def probabilities(self, values: np.ndarray) -> np.ndarray:
        eta = values @ self.coefficients.T + self.biases
        cumulative = _sigmoid(self.thresholds[None, :, :] - eta[:, :, None])
        probabilities = np.concatenate(
            (
                cumulative[:, :, :1],
                np.diff(cumulative, axis=2),
                1.0 - cumulative[:, :, -1:],
            ),
            axis=2,
        )
        probabilities = np.clip(probabilities, 0.0, 1.0)
        return probabilities / probabilities.sum(axis=2, keepdims=True)


def _sigmoid(value: np.ndarray) -> np.ndarray:
    clipped = np.clip(value, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _dependencies() -> tuple[Any, ...]:
    try:
        import jax.numpy as jnp
        import numpyro
        import numpyro.distributions as dist
        from jax import random
        from numpyro.diagnostics import summary
        from numpyro.infer import MCMC, NUTS
        from numpyro.infer.initialization import init_to_median
    except ImportError as exc:
        raise OrderedLogitTrainingUnavailableError("numpyro") from exc
    return jnp, random, numpyro, dist, summary, MCMC, NUTS, init_to_median


def _settings(recipe: OrderedLogitEstimatorRecipe) -> _InferenceSettings:
    return _InferenceSettings(
        chains=recipe.chains,
        warmup=recipe.warmup,
        draws=recipe.draws,
        max_r_hat=recipe.max_r_hat,
        min_ess=recipe.min_effective_sample_size,
        max_divergences=recipe.max_divergences,
    )


def _fit_seed(seed: int, offset: int = 0) -> int:
    return int((seed + offset) % BAYESIAN_INFERENCE_SEED_MODULUS)


def _sampler_model(
    recipe: OrderedLogitEstimatorRecipe,
    *,
    feature_count: int,
    category_count: int,
    jnp: Any,
    numpyro: Any,
    dist: Any,
) -> Any:
    def model(features: Any, observed: Any | None = None) -> None:
        coefficients = numpyro.sample(
            "coefficients",
            dist.Normal(0.0, recipe.coefficient_prior_scale)
            .expand((feature_count,))
            .to_event(1),
        )
        first = numpyro.sample(
            "first_threshold",
            dist.Normal(0.0, recipe.first_threshold_prior_scale),
        )
        if category_count > 2:
            log_increments = numpyro.sample(
                "log_threshold_increments",
                dist.Normal(0.0, 0.5).expand((category_count - 2,)).to_event(1),
            )
            thresholds = jnp.concatenate(
                (jnp.asarray([first]), first + jnp.cumsum(jnp.exp(log_increments)))
            )
        else:
            thresholds = jnp.asarray([first])
        numpyro.deterministic("thresholds", thresholds)
        eta = jnp.asarray(features) @ coefficients
        cumulative = 1.0 / (
            1.0 + jnp.exp(-jnp.clip(thresholds - eta[:, None], -50, 50))
        )
        probabilities = jnp.concatenate(
            (
                cumulative[:, :1],
                jnp.diff(cumulative, axis=1),
                1.0 - cumulative[:, -1:],
            ),
            axis=1,
        )
        numpyro.sample("observed", dist.Categorical(probs=probabilities), obs=observed)

    return model


def _fit(
    values: np.ndarray,
    target: np.ndarray,
    recipe: OrderedLogitEstimatorRecipe,
    *,
    category_count: int,
    seed: int,
) -> _Fit:
    if values.ndim != 2 or values.shape[1] > BAYESIAN_MAX_FEATURES:
        raise ValueError(
            "ordered-logit.v1 supports at most "
            f"{BAYESIAN_MAX_FEATURES} prepared features"
        )
    if len(values) < 2 or len(values) > BAYESIAN_MAX_ROWS:
        raise ValueError(f"ordered-logit.v1 requires 2..{BAYESIAN_MAX_ROWS} rows")
    if not np.isfinite(values).all() or not np.isfinite(target).all():
        raise ValueError("ordered-logit.v1 does not repair malformed training data")
    observed = target.astype(int)
    if not np.array_equal(target, observed) or set(np.unique(observed)) != set(
        range(category_count)
    ):
        raise ValueError("ordered-logit.v1 requires every Task-owned category")
    x_mean = values.mean(axis=0)
    x_scale = values.std(axis=0)
    x_scale[x_scale < 1e-12] = 1.0
    normalized = (values - x_mean) / x_scale
    settings = _settings(recipe)
    jnp, random, numpyro, dist, summarize, MCMC, NUTS, init_to_median = _dependencies()
    model = _sampler_model(
        recipe,
        feature_count=values.shape[1],
        category_count=category_count,
        jnp=jnp,
        numpyro=numpyro,
        dist=dist,
    )
    sampler = MCMC(
        NUTS(
            model,
            target_accept_prob=recipe.target_accept_probability,
            max_tree_depth=recipe.max_tree_depth,
            dense_mass=recipe.dense_mass == "enabled",
            init_strategy=init_to_median(num_samples=10),
        ),
        num_warmup=settings.warmup,
        num_samples=settings.draws,
        num_chains=settings.chains,
        chain_method=recipe.chain_method,
        progress_bar=False,
    )
    try:
        sampler.run(
            random.PRNGKey(_fit_seed(seed)),
            jnp.asarray(normalized),
            jnp.asarray(observed),
        )
        grouped = sampler.get_samples(group_by_chain=True)
        names = ["coefficients", "first_threshold"]
        if category_count > 2:
            names.append("log_threshold_increments")
        chain_summary = summarize(
            {name: grouped[name] for name in names}, group_by_chain=True
        )
        divergences = sampler.get_extra_fields(group_by_chain=True).get("diverging")
        diagnostics = _diagnostics_from_summary(
            chain_summary,
            int(np.asarray(divergences if divergences is not None else 0).sum()),
            settings,
        )
    except OrderedLogitTrainingError:
        raise
    except Exception as exc:  # noqa: BLE001 - converted to typed build failure
        raise OrderedLogitSamplingError(
            "sampling_failed", f"ordered-logit.v1 NUTS sampling failed: {exc}"
        ) from exc
    if diagnostics.status != "passed":
        raise OrderedLogitSamplingError(
            "sampling_quality_failed",
            "ordered-logit.v1 diagnostics failed: " + "; ".join(diagnostics.findings),
        )
    samples = sampler.get_samples(group_by_chain=False)
    normalized_coefficients = np.asarray(samples["coefficients"], dtype=float)
    coefficients = normalized_coefficients / x_scale
    biases = -(coefficients @ x_mean)
    thresholds = np.asarray(samples["thresholds"], dtype=float)
    if (
        coefficients.ndim != 2
        or thresholds.shape != (len(coefficients), category_count - 1)
        or biases.shape != (len(coefficients),)
        or not np.isfinite(coefficients).all()
        or not np.isfinite(biases).all()
        or not np.isfinite(thresholds).all()
        or not np.all(np.diff(thresholds, axis=1) > 0)
    ):
        raise OrderedLogitSamplingError(
            "sampling_failed", "ordered-logit.v1 produced invalid posterior draws"
        )
    identity = InferenceIdentity.create(
        policy=inference_policy("nuts"),
        parameterization=recipe.parameterization,
        diagnostics=diagnostics,
        seed=_fit_seed(seed),
        chains=settings.chains,
        warmup=settings.warmup,
        draws=settings.draws,
        resource_limits=bayesian_inference_resource_limits(recipe),
        convergence_criteria={
            "max_r_hat": settings.max_r_hat,
            "min_ess": settings.min_ess,
            "max_divergences": settings.max_divergences,
        },
    )
    return _Fit(coefficients, biases, thresholds, identity)


def _mixture_probabilities(fit: _Fit, values: np.ndarray) -> np.ndarray:
    return fit.probabilities(values).mean(axis=1)


def _category_quantile(probabilities: np.ndarray, quantile: float) -> np.ndarray:
    return np.argmax(np.cumsum(probabilities, axis=1) >= quantile, axis=1).astype(float)


def _honest_predictions(
    data: TargetTrainingSet,
    recipe: OrderedLogitEstimatorRecipe,
) -> tuple[np.ndarray, tuple[dict[str, Any], ...]]:
    category_count = len(data.ordinal_categories)
    probabilities = np.full((len(data.y), category_count), np.nan, dtype=float)
    fold_diagnostics: list[dict[str, Any]] = []
    folds = (0,) if data.is_temporal_validation else range(data.folds)
    for fold in folds:
        train_rows = data.training_rows_for_fold(fold)
        if data.is_temporal_validation:
            train_rows = train_rows | data.temporal_calibration_rows
            evaluate = data.quality_rows
        else:
            evaluate = data.fold_ids == fold
        try:
            fitted = _fit(
                prepared_feature_matrix(
                    data, fit_rows=train_rows, transform_rows=train_rows
                ),
                data.y[train_rows],
                recipe,
                category_count=category_count,
                seed=_fit_seed(recipe.seed, int(fold)),
            )
        except OrderedLogitTrainingError as exc:
            raise exc.bind_target(data.target) from exc
        probabilities[evaluate] = _mixture_probabilities(
            fitted,
            prepared_feature_matrix(data, fit_rows=train_rows, transform_rows=evaluate),
        )
        fold_diagnostics.append(
            {
                "fold": int(fold),
                "training_rows": int(train_rows.sum()),
                "evaluation_rows": int(evaluate.sum()),
                "category_order_digest": data.ordinal_category_order_digest,
                "inference_identity": fitted.inference_identity.model_dump(mode="json"),
            }
        )
    if not np.isfinite(probabilities).all():
        raise OrderedLogitSamplingError(
            "sampling_failed",
            "ordered-logit.v1 produced incomplete cross-fitted probabilities",
        )
    return probabilities, tuple(fold_diagnostics)


def train(
    data: TargetTrainingSet,
    recipe: OrderedLogitEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if data.target_kind != "ordinal" or len(data.ordinal_categories) < 2:
        raise ValueError("ordered-logit.v1 requires Task-owned ordinal semantics")
    probabilities, fold_diagnostics = _honest_predictions(data, recipe)
    quality_rows = data.quality_rows
    observed = data.y.astype(int)
    ranks = np.arange(len(data.ordinal_categories), dtype=float)
    points = probabilities @ ranks
    residuals = data.y[quality_rows] - points[quality_rows]
    lower = _category_quantile(probabilities, 0.05)
    upper = _category_quantile(probabilities, 0.95)
    selected_probability = probabilities[np.arange(len(observed)), observed]
    cumulative = np.cumsum(probabilities[:, :-1], axis=1)
    observed_cumulative = (
        observed[:, None] <= np.arange(len(data.ordinal_categories) - 1)[None, :]
    )
    rps_rows = np.sum((cumulative - observed_cumulative) ** 2, axis=1)
    predicted_category = np.argmax(probabilities, axis=1)
    final = _fit(
        prepared_feature_matrix(data),
        data.y,
        recipe,
        category_count=len(data.ordinal_categories),
        seed=effective_bayesian_final_inference_seed(recipe.seed),
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        artifact_path,
        w0=final.coefficients[:, :, None],
        b0=final.biases[:, None],
        ordinal_thresholds=final.thresholds,
    )
    quality = TargetQualityMetric(
        target=data.target,
        parent_conditions=len(set(data.validation_groups)),
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        median_absolute_error=float(np.median(np.abs(residuals))),
        mean_log_predictive_density=float(
            np.mean(np.log(np.clip(selected_probability[quality_rows], 1e-15, 1.0)))
        ),
        extreme_residual_mae=float(np.mean(np.abs(residuals))),
        interval_coverage_90=float(
            np.mean(
                (data.y[quality_rows] >= lower[quality_rows])
                & (data.y[quality_rows] <= upper[quality_rows])
            )
        ),
        interval_coverage_method="posterior-predictive-interval",
        interval_coverage_observations=int(quality_rows.sum()),
        mean_interval_width=float(np.mean(upper[quality_rows] - lower[quality_rows])),
    )
    parameters = recipe.model_dump(
        mode="json",
        exclude={"estimator_id", "validation_plan", "validation_plans_by_target"},
        exclude_none=True,
    )
    representative_thresholds = np.median(final.thresholds, axis=0)
    predictor = {
        "id": f"{data.target.lower()}-ordered-logit",
        "target": data.target,
        "unit": data.unit,
        "target_kind": "ordinal",
        "runtime_type": RUNTIME_TYPE,
        "architecture_id": "dense_mlp_v1",
        "artifact": artifact_path.as_posix(),
        "predictive_family": "ordinal_logit",
        "feature_names": list(data.feature_names),
        "inference_identity": final.inference_identity.model_dump(mode="json"),
        "config": {
            "activation": "tanh",
            "categories": list(data.ordinal_categories),
            "category_order_digest": data.ordinal_category_order_digest,
            "thresholds": representative_thresholds.tolist(),
            "threshold_draws": "artifact:ordinal_thresholds",
            "interval_semantics": (
                "q05-q95 posterior-predictive ordered-category interval"
            ),
            "training": standard_training_metadata(
                data,
                estimator_id=recipe.estimator_id,
                uncertainty=(
                    "NUTS posterior draws for coefficients and monotone "
                    "ordinal thresholds"
                ),
                parameters=parameters,
                effective_inference_seed=effective_bayesian_final_inference_seed(
                    recipe.seed
                ),
            ),
        },
    }
    category_calibration = {
        category: {
            "observed_rate": float(np.mean(observed[quality_rows] == index)),
            "predicted_probability": float(np.mean(probabilities[quality_rows, index])),
        }
        for index, category in enumerate(data.ordinal_categories)
    }
    cumulative_calibration = [
        {
            "through_category": data.ordinal_categories[index],
            "observed_rate": float(np.mean(observed[quality_rows] <= index)),
            "predicted_probability": float(np.mean(cumulative[quality_rows, index])),
        }
        for index in range(len(data.ordinal_categories) - 1)
    ]
    quality_observed = observed[quality_rows]
    quality_predicted = predicted_category[quality_rows]
    extreme_recall = {}
    for index in (0, len(data.ordinal_categories) - 1):
        category_rows = quality_observed == index
        extreme_recall[data.ordinal_categories[index]] = (
            float(np.mean(quality_predicted[category_rows] == index))
            if category_rows.any()
            else None
        )
    diagnostics = {
        "estimator_id": recipe.estimator_id,
        "adoption_status": "experimental",
        "production_claim": False,
        "cohort_digest": data.cohort_digest,
        "fold_digest": data.fold_digest,
        "category_order": list(data.ordinal_categories),
        "category_order_digest": data.ordinal_category_order_digest,
        "fold_category_diagnostics": data.validation_diagnostics.get(
            "ordinal_category_diagnostics"
        ),
        "fold_inference": fold_diagnostics,
        "final_inference_identity_digest": final.inference_identity.identity_digest,
        "ordinal_quality": {
            "ordinal_mae": quality.mae,
            "ranked_probability_score": float(np.mean(rps_rows[quality_rows])),
            "ordinal_log_loss": float(
                -np.mean(
                    np.log(np.clip(selected_probability[quality_rows], 1e-15, 1.0))
                )
            ),
            "category_calibration": category_calibration,
            "cumulative_calibration": cumulative_calibration,
            "extreme_category_recall": extreme_recall,
        },
        "limitations": [
            "experimental candidate; no production adoption claim",
            "category order is Task-owned and is never inferred from labels "
            "or frequency",
            "coefficient effects are associational, not intervention claims",
        ],
    }
    return TrainedPredictor(
        predictor=predictor,
        artifact=artifact_path,
        quality=quality,
        diagnostics=diagnostics,
        predict=lambda values: float(
            _mixture_probabilities(
                final, np.asarray(values, dtype=float).reshape(1, -1)
            )[0]
            @ ranks
        ),
        evaluation_predictions=points,
    )
