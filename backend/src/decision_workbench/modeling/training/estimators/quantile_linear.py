from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.optimize import linprog

from decision_workbench.modeling.model_lifecycle import TargetQualityMetric
from decision_workbench.modeling.training.feature_dataset import (
    TargetTrainingSet,
    prepared_feature_matrix,
)
from decision_workbench.modeling.training.recipe import (
    QuantileLinearRegressionEstimatorRecipe,
)

from .types import TrainedPredictor, standard_training_metadata

RUNTIME_TYPE = "builtin.quantile_linear.v1"
ARTIFACT_SUFFIX = ".npz"
ARTIFACT_FORMAT = "bounded-npz"


def _fit_quantile(
    values: np.ndarray,
    target: np.ndarray,
    *,
    level: float,
    penalty: float,
) -> tuple[np.ndarray, float]:
    mean = values.mean(axis=0)
    scale = values.std(axis=0)
    scale[scale < 1e-12] = 1.0
    normalized = (values - mean) / scale
    rows, features = normalized.shape
    # beta+ / beta- implement an L1 penalty. The intercept is unbounded and
    # unpenalized; u/v are the positive and negative pinball residuals.
    objective = np.r_[
        np.full(2 * features, penalty * rows),
        0.0,
        np.full(rows, level),
        np.full(rows, 1.0 - level),
    ]
    equality = np.column_stack(
        [
            normalized,
            -normalized,
            np.ones(rows),
            np.eye(rows),
            -np.eye(rows),
        ]
    )
    result = linprog(
        objective,
        A_eq=equality,
        b_eq=target,
        bounds=[
            *((0.0, None),) * (2 * features),
            (None, None),
            *((0.0, None),) * (2 * rows),
        ],
        method="highs",
    )
    if not result.success:
        raise ValueError(f"quantile fit failed at q={level:g}: {result.message}")
    normalized_weights = result.x[:features] - result.x[features : 2 * features]
    weights = normalized_weights / scale
    intercept = float(result.x[2 * features] - weights @ mean)
    return weights, intercept


def _fit(
    values: np.ndarray,
    target: np.ndarray,
    recipe: QuantileLinearRegressionEstimatorRecipe,
) -> tuple[np.ndarray, np.ndarray]:
    fitted = [
        _fit_quantile(
            values,
            target,
            level=level,
            penalty=recipe.penalty,
        )
        for level in recipe.quantile_levels
    ]
    return (
        np.vstack([item[0] for item in fitted]),
        np.asarray([item[1] for item in fitted]),
    )


def _honest_predictions(
    data: TargetTrainingSet,
    recipe: QuantileLinearRegressionEstimatorRecipe,
) -> np.ndarray:
    predictions = np.full(
        (len(data.y), len(recipe.quantile_levels)),
        np.nan,
        dtype=float,
    )
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
        weights, intercepts = _fit(
            train_x,
            data.y[train_rows],
            recipe,
        )
        evaluate_x = prepared_feature_matrix(
            data,
            fit_rows=train_rows,
            transform_rows=evaluate,
        )
        predictions[evaluate] = evaluate_x @ weights.T + intercepts
    return predictions


def _pinball(
    observed: np.ndarray,
    predicted: np.ndarray,
    level: float,
) -> float:
    error = observed - predicted
    return float(np.mean(np.maximum(level * error, (level - 1.0) * error)))


def train(
    data: TargetTrainingSet,
    recipe: QuantileLinearRegressionEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if data.target_kind != "continuous":
        raise ValueError("quantile linear regression supports continuous targets only")
    evaluation = _honest_predictions(data, recipe)
    quality_rows = data.quality_rows
    evaluated = evaluation[quality_rows]
    observed = data.y[quality_rows]
    crossing = np.any(np.diff(evaluated, axis=1) < 0.0, axis=1)
    median_index = recipe.quantile_levels.index(0.5)
    median = evaluated[:, median_index]
    residuals = observed - median
    lower = evaluated[:, 0]
    upper = evaluated[:, -1]
    usable_interval = ~crossing
    if not np.any(usable_interval):
        raise ValueError(
            f"{data.target}: every evaluated q05-q95 interval crosses"
        )
    crossing_bounds = None
    if np.any(crossing):
        crossing_features = data.x[quality_rows][crossing]
        crossing_bounds = {}
        for index, name in enumerate(data.feature_names):
            values = crossing_features[:, index]
            finite = values[np.isfinite(values)]
            if len(finite):
                crossing_bounds[name] = (
                    float(np.min(finite)),
                    float(np.max(finite)),
                )
        if not crossing_bounds:
            crossing_bounds = None

    weights, intercepts = _fit(
        prepared_feature_matrix(data),
        data.y,
        recipe,
    )
    final_training_predictions = (
        prepared_feature_matrix(data) @ weights.T + intercepts
    )
    final_crossing = np.any(
        np.diff(final_training_predictions, axis=1) < 0.0,
        axis=1,
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        artifact_path,
        quantile_levels=np.asarray(recipe.quantile_levels),
        coefficients=weights,
        intercepts=intercepts,
    )

    pinball = {
        format(level, ".12g"): _pinball(observed, evaluated[:, index], level)
        for index, level in enumerate(recipe.quantile_levels)
    }
    quality = TargetQualityMetric(
        target=data.target,
        parent_conditions=len(set(data.validation_groups)),
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        interval_coverage_90=float(
            np.mean(
                (observed[usable_interval] >= lower[usable_interval])
                & (observed[usable_interval] <= upper[usable_interval])
            )
        ),
        interval_coverage_method="outer-fold-conditional-quantiles",
        interval_coverage_observations=int(usable_interval.sum()),
        quantile_pinball_losses=pinball,
        mean_interval_width=float(
            np.mean(upper[usable_interval] - lower[usable_interval])
        ),
        quantile_crossing_count=int(crossing.sum()),
        quantile_crossing_observed_feature_bounds=crossing_bounds,
        quantile_crossing_scope=(
            "observed_outer_fold_rows_not_full_input_domain"
            if crossing_bounds is not None
            else None
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
        "id": f"{data.target.lower()}-quantile-linear",
        "target": data.target,
        "unit": data.unit,
        "target_kind": data.target_kind,
        "runtime_type": RUNTIME_TYPE,
        "architecture_id": "quantile_linear_v1",
        "artifact": artifact_path.as_posix(),
        "predictive_family": "empirical_quantiles",
        "feature_names": list(data.feature_names),
        "config": {
            "crossing_policy": recipe.crossing_policy,
            "training": standard_training_metadata(
                data,
                estimator_id=recipe.estimator_id,
                uncertainty=(
                    "conditional q05/q50/q95; observed coverage is reported "
                    "separately from nominal quantile levels"
                ),
                parameters=parameters,
            ),
        },
    }

    def predict(values: np.ndarray) -> float:
        quantiles = weights @ values + intercepts
        if np.any(np.diff(quantiles) < 0.0):
            raise ValueError("quantile predictions cross for the requested input")
        return float(quantiles[median_index])

    return TrainedPredictor(
        predictor=predictor,
        artifact=artifact_path,
        quality=quality,
        diagnostics={
            "estimator_id": recipe.estimator_id,
            "folds": data.folds,
            "cohort_digest": data.cohort_digest,
            "fold_digest": data.fold_digest,
            "evaluation": "outer-fold-refit",
            "quantile_levels": list(recipe.quantile_levels),
            "pinball_loss_by_quantile": pinball,
            "interval_coverage": quality.interval_coverage_90,
            "interval_evaluation_count": int(usable_interval.sum()),
            "mean_interval_width": quality.mean_interval_width,
            "crossing_policy": "reject",
            "evaluation_crossing_count": int(crossing.sum()),
            "final_training_crossing_count": int(final_crossing.sum()),
            "crossing_finding": (
                "unavailable_where_quantiles_cross"
                if bool(crossing.any() or final_crossing.any())
                else "none_on_evaluated_and_training_rows"
            ),
            "additive_quantile_status": "not_implemented_separate_candidate",
        },
        predict=predict,
        evaluation_predictions=evaluation[:, median_index],
    )
