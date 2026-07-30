from __future__ import annotations

from pathlib import Path

import numpy as np

from material_workbench.modeling.model_lifecycle import TargetQualityMetric
from material_workbench.modeling.training.feature_dataset import TargetTrainingSet
from material_workbench.modeling.training.recipe import RidgeEstimatorRecipe

from .types import TrainedPredictor, standard_training_metadata


def _fit(x: np.ndarray, y: np.ndarray, alpha: float) -> tuple[np.ndarray, float]:
    mean = x.mean(axis=0)
    scale = x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    normalized = (x - mean) / scale
    design = np.column_stack([np.ones(len(x)), normalized])
    penalty = np.eye(design.shape[1]) * alpha
    penalty[0, 0] = 0.0
    coefficients = np.linalg.solve(design.T @ design + penalty, design.T @ y)
    weights = coefficients[1:] / scale
    bias = float(coefficients[0] - weights @ mean)
    return weights, bias


def _honest_grouped_evaluation(
    data: TargetTrainingSet,
    alpha: float,
) -> tuple[np.ndarray, float]:
    if data.folds < 3:
        raise ValueError(
            f"{data.target}: nested interval evaluation requires at least three folds"
        )
    predictions = np.empty(len(data.y), dtype=float)
    covered = np.zeros(len(data.y), dtype=bool)
    for outer_fold in range(data.folds):
        evaluate = data.fold_ids == outer_fold
        outer_train = ~evaluate
        weights, bias = _fit(
            data.x[outer_train],
            data.y[outer_train],
            alpha,
        )
        predictions[evaluate] = data.x[evaluate] @ weights + bias

        calibration_residuals: list[np.ndarray] = []
        for inner_fold in range(data.folds):
            if inner_fold == outer_fold:
                continue
            calibrate = outer_train & (data.fold_ids == inner_fold)
            inner_train = outer_train & ~calibrate
            if not calibrate.any() or inner_train.sum() < 2:
                raise ValueError(
                    f"{data.target}: ridge nested fold has insufficient rows"
                )
            inner_weights, inner_bias = _fit(
                data.x[inner_train],
                data.y[inner_train],
                alpha,
            )
            calibration_residuals.append(
                data.y[calibrate]
                - (data.x[calibrate] @ inner_weights + inner_bias)
            )
        lower, upper = np.quantile(
            np.concatenate(calibration_residuals),
            (0.05, 0.95),
        )
        outer_residuals = data.y[evaluate] - predictions[evaluate]
        covered[evaluate] = (
            (outer_residuals >= lower)
            & (outer_residuals <= upper)
        )
    return predictions, float(np.mean(covered))


def train(
    data: TargetTrainingSet,
    recipe: RidgeEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    folds = data.folds
    predictions, coverage = _honest_grouped_evaluation(data, recipe.alpha)
    residuals = data.y - predictions
    lower, upper = np.quantile(residuals, (0.05, 0.95))
    weights, bias = _fit(data.x, data.y, recipe.alpha)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        artifact_path,
        weights=weights,
        bias=np.asarray(bias),
        lower_offset=np.asarray(float(lower)),
        upper_offset=np.asarray(float(upper)),
    )
    quality = TargetQualityMetric(
        target=data.target,
        parent_conditions=len(set(data.validation_groups)),
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        interval_coverage_90=coverage,
        interval_coverage_method="nested-grouped-oof-residual-quantiles",
        interval_coverage_observations=len(residuals),
    )
    return TrainedPredictor(
        predictor={
            "id": f"{data.target.lower()}-ridge",
            "target": data.target,
            "unit": data.unit,
            "target_kind": data.target_kind,
            "runtime_type": "builtin.linear.v1",
            "architecture_id": "profile_transformed_ridge_v1",
            "artifact": artifact_path.as_posix(),
            "predictive_family": "empirical_quantiles",
            "feature_names": list(data.feature_names),
            "config": {
                "training_method": "ridge.v1",
                "training_unit": "replicate_context_mean",
                "validation_method": f"{folds}-fold grouped validation CV",
                "interval_method": "nested grouped OOF residual quantiles",
                "ridge_alpha": recipe.alpha,
                "seed": recipe.seed,
                "training": standard_training_metadata(
                    data,
                    estimator_id=recipe.estimator_id,
                    uncertainty="nested grouped OOF residual quantiles",
                    parameters={"alpha": recipe.alpha, "seed": recipe.seed},
                ),
            },
        },
        artifact=artifact_path,
        quality=quality,
        diagnostics={
            "estimator_id": recipe.estimator_id,
            "alpha": recipe.alpha,
            "folds": folds,
            "seed": recipe.seed,
            "cohort_digest": data.cohort_digest,
            "fold_digest": data.fold_digest,
            "evaluation": "outer-fold-refit-with-inner-calibration",
        },
        predict=lambda values: float(values @ weights + bias),
    )
