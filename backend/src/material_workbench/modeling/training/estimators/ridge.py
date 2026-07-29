from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np

from material_workbench.modeling.model_lifecycle import TargetQualityMetric
from material_workbench.modeling.training.feature_dataset import TargetTrainingSet
from material_workbench.modeling.training.recipe import RidgeEstimatorRecipe

from .types import TrainedPredictor


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


def _fold_order(group: str, *, seed: int) -> bytes:
    payload = f"{seed}:{group}".encode("utf-8")
    return hashlib.sha256(payload).digest()


def _balanced_fold_ids(
    groups: tuple[str, ...],
    *,
    requested_folds: int,
    seed: int,
) -> tuple[np.ndarray, int]:
    unique = sorted(
        set(groups),
        key=lambda group: (_fold_order(group, seed=seed), group),
    )
    folds = min(requested_folds, len(unique))
    if folds < 2:
        raise ValueError("ridge requires at least two independent validation groups")
    assignment = {
        group: index % folds
        for index, group in enumerate(unique)
    }
    return np.asarray([assignment[group] for group in groups], dtype=int), folds


def _cross_fitted_quantile_coverage(
    residuals: np.ndarray,
    fold_ids: np.ndarray,
) -> float:
    covered = np.zeros(len(residuals), dtype=bool)
    for fold in sorted(set(fold_ids.tolist())):
        evaluate = fold_ids == fold
        calibrate = ~evaluate
        lower, upper = np.quantile(residuals[calibrate], (0.05, 0.95))
        covered[evaluate] = (
            (residuals[evaluate] >= lower)
            & (residuals[evaluate] <= upper)
        )
    return float(np.mean(covered))


def train(
    data: TargetTrainingSet,
    recipe: RidgeEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    fold_ids, folds = _balanced_fold_ids(
        data.validation_groups,
        requested_folds=recipe.folds,
        seed=recipe.seed,
    )
    predictions = np.empty(len(data.y), dtype=float)
    for fold in sorted(set(fold_ids.tolist())):
        test = fold_ids == fold
        train_rows = ~test
        if train_rows.sum() < 2:
            raise ValueError(f"{data.target}: ridge fold has fewer than two training rows")
        weights, bias = _fit(data.x[train_rows], data.y[train_rows], recipe.alpha)
        predictions[test] = data.x[test] @ weights + bias
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
        interval_coverage_90=_cross_fitted_quantile_coverage(
            residuals,
            fold_ids,
        ),
        interval_coverage_method="cross-fitted-oof-residual-quantiles",
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
                "interval_method": "cross-fitted OOF residual quantiles",
                "ridge_alpha": recipe.alpha,
                "seed": recipe.seed,
            },
        },
        artifact=artifact_path,
        quality=quality,
        diagnostics={
            "estimator_id": recipe.estimator_id,
            "alpha": recipe.alpha,
            "folds": folds,
            "seed": recipe.seed,
        },
        predict=lambda values: float(values @ weights + bias),
    )
