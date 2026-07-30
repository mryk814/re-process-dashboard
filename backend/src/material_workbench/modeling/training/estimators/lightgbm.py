from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from material_workbench.modeling.model_lifecycle import TargetQualityMetric
from material_workbench.modeling.training.feature_dataset import TargetTrainingSet
from material_workbench.modeling.training.recipe import (
    LightGBMBinaryEstimatorRecipe,
    LightGBMRegressionEstimatorRecipe,
)

from .types import TrainedPredictor, standard_training_metadata


def _parameters(
    *,
    objective: str,
    seed: int,
    monotone_constraints: list[int] | None = None,
) -> dict[str, Any]:
    parameters: dict[str, Any] = {
        "objective": objective,
        "metric": "binary_logloss" if objective == "binary" else "l2",
        "learning_rate": 0.035 if objective != "binary" else 0.03,
        "num_leaves": 15,
        "max_depth": 6 if objective != "binary" else 5,
        "min_data_in_leaf": 30 if objective != "binary" else 25,
        "feature_fraction": 0.9 if objective != "binary" else 0.85,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "lambda_l1": 0.05 if objective != "binary" else 0.1,
        "lambda_l2": 1.0 if objective != "binary" else 2.0,
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
        "seed": seed,
        "feature_fraction_seed": seed,
        "bagging_seed": seed,
        "data_random_seed": seed,
    }
    if monotone_constraints is not None:
        parameters.update({
            "monotone_constraints": monotone_constraints,
            "monotone_constraints_method": "advanced",
        })
    return parameters


def _train_booster(
    x: np.ndarray,
    y: np.ndarray,
    *,
    objective: str,
    seed: int,
    num_boost_round: int,
    monotone_constraints: list[int] | None = None,
):
    try:
        import lightgbm as lgb
    except ModuleNotFoundError as exc:
        raise ValueError(
            "lightgbm estimator requires the allow-listed LightGBM dependency"
        ) from exc

    return lgb.train(
        _parameters(
            objective=objective,
            seed=seed,
            monotone_constraints=monotone_constraints,
        ),
        lgb.Dataset(x, label=y, free_raw_data=False),
        num_boost_round=num_boost_round,
        callbacks=[lgb.log_evaluation(0)],
    )


def _fit_platt(probabilities: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    clipped = np.clip(probabilities, 1e-7, 1 - 1e-7)
    logits = np.log(clipped / (1 - clipped))
    design = np.column_stack((np.ones(len(y)), logits))
    weights = np.asarray([0.0, 1.0])
    penalty = np.diag([1e-6, 1e-3])
    for _ in range(30):
        fitted = 1 / (1 + np.exp(-np.clip(design @ weights, -30, 30)))
        curvature = np.maximum(fitted * (1 - fitted), 1e-8)
        hessian = design.T @ (curvature[:, None] * design) + penalty
        gradient = design.T @ (fitted - y) + penalty @ weights
        step = np.linalg.solve(hessian, gradient)
        weights -= step
        if float(np.max(np.abs(step))) < 1e-8:
            break
    return float(weights[0]), float(weights[1])


def _calibrate(
    probabilities: np.ndarray,
    calibration: tuple[float, float],
) -> np.ndarray:
    intercept, slope = calibration
    clipped = np.clip(probabilities, 1e-7, 1 - 1e-7)
    logits = np.log(clipped / (1 - clipped))
    return 1 / (1 + np.exp(-np.clip(intercept + slope * logits, -30, 30)))


def _cross_fitted_predictions(
    data: TargetTrainingSet,
    *,
    objective: str,
    seed: int,
    num_boost_round: int,
    monotone_constraints: list[int] | None = None,
) -> np.ndarray:
    predictions = np.empty(len(data.y), dtype=float)
    for fold in range(data.folds):
        evaluate = data.fold_ids == fold
        train_rows = ~evaluate
        if objective == "binary" and set(np.unique(data.y[train_rows])) != {0.0, 1.0}:
            raise ValueError(
                f"{data.target}: every binary training fold must contain both classes"
            )
        booster = _train_booster(
            data.x[train_rows],
            data.y[train_rows],
            objective=objective,
            seed=seed + fold + 1,
            num_boost_round=num_boost_round,
            monotone_constraints=monotone_constraints,
        )
        predictions[evaluate] = booster.predict(
            data.x[evaluate],
            num_iteration=num_boost_round,
        )
    return predictions


def _cross_fitted_quantile_coverage(
    residuals: np.ndarray,
    fold_ids: np.ndarray,
) -> float:
    covered = np.zeros(len(residuals), dtype=bool)
    for fold in sorted(set(fold_ids.tolist())):
        evaluate = fold_ids == fold
        lower, upper = np.quantile(residuals[~evaluate], (0.05, 0.95))
        covered[evaluate] = (
            (residuals[evaluate] >= lower)
            & (residuals[evaluate] <= upper)
        )
    return float(np.mean(covered))


def _cross_fitted_normal_coverage(
    residuals: np.ndarray,
    fold_ids: np.ndarray,
) -> float:
    z90 = 1.6448536269514722
    covered = np.zeros(len(residuals), dtype=bool)
    for fold in sorted(set(fold_ids.tolist())):
        evaluate = fold_ids == fold
        residual_std = max(
            float(np.sqrt(np.mean(residuals[~evaluate] ** 2))),
            1e-6,
        )
        covered[evaluate] = np.abs(residuals[evaluate]) <= z90 * residual_std
    return float(np.mean(covered))


def _auc(y: np.ndarray, probabilities: np.ndarray) -> float:
    positives = y == 1
    negatives = ~positives
    order = np.argsort(probabilities, kind="stable")
    ranks = np.empty(len(y), dtype=float)
    ranks[order] = np.arange(1, len(y) + 1)
    return float(
        (
            ranks[positives].sum()
            - positives.sum() * (positives.sum() + 1) / 2
        )
        / (positives.sum() * negatives.sum())
    )


def _regression(
    data: TargetTrainingSet,
    recipe: LightGBMRegressionEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    unknown_monotone = (
        set(recipe.monotone_decreasing_features)
        - set(data.feature_names)
    )
    if unknown_monotone:
        raise ValueError(
            "monotone features are not in the FeatureDataset: "
            + ", ".join(sorted(unknown_monotone))
        )
    monotone_constraints = [
        -1 if name in recipe.monotone_decreasing_features else 0
        for name in data.feature_names
    ]
    oof = _cross_fitted_predictions(
        data,
        objective="regression_l2",
        seed=recipe.seed,
        num_boost_round=recipe.num_boost_round,
        monotone_constraints=monotone_constraints,
    )
    residuals = data.y - oof
    booster = _train_booster(
        data.x,
        data.y,
        objective="regression_l2",
        seed=recipe.seed,
        num_boost_round=recipe.num_boost_round,
        monotone_constraints=monotone_constraints,
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(artifact_path))
    residual_std = max(float(np.sqrt(np.mean(residuals**2))), 1e-6)
    if recipe.predictive_family == "normal":
        coverage = _cross_fitted_normal_coverage(residuals, data.fold_ids)
        coverage_method = "cross-fitted-oof-normal-scale"
        uncertainty = "cross-fitted OOF normal residual scale"
    else:
        coverage = _cross_fitted_quantile_coverage(residuals, data.fold_ids)
        coverage_method = "cross-fitted-oof-residual-quantiles"
        uncertainty = "cross-fitted OOF residual quantiles"
    parameters = {
        "num_boost_round": recipe.num_boost_round,
        "seed": recipe.seed,
        "monotone_decreasing_features": list(
            recipe.monotone_decreasing_features
        ),
    }
    return TrainedPredictor(
        predictor={
            "id": f"{data.target.lower()}-lightgbm",
            "target": data.target,
            "unit": data.unit,
            "target_kind": data.target_kind,
            "runtime_type": "lightgbm.booster.v1",
            "architecture_id": "lightgbm_regression_v1",
            "artifact": artifact_path.as_posix(),
            "predictive_family": recipe.predictive_family,
            "feature_names": list(data.feature_names),
            "config": {
                "training_method": recipe.estimator_id,
                "training_unit": "replicate_context_mean",
                "validation_method": f"{data.folds}-fold grouped validation CV",
                "interval_method": uncertainty,
                "num_boost_round": recipe.num_boost_round,
                "residual_std": residual_std,
                "monotone_decreasing_features": list(
                    recipe.monotone_decreasing_features
                ),
                "training": standard_training_metadata(
                    data,
                    estimator_id=recipe.estimator_id,
                    uncertainty=uncertainty,
                    parameters=parameters,
                ),
            },
        },
        artifact=artifact_path,
        quality=TargetQualityMetric(
            target=data.target,
            parent_conditions=len(set(data.validation_groups)),
            mae=float(np.mean(np.abs(residuals))),
            rmse=residual_std,
            interval_coverage_90=coverage,
            interval_coverage_method=coverage_method,
            interval_coverage_observations=len(residuals),
        ),
        diagnostics={
            "estimator_id": recipe.estimator_id,
            **parameters,
            "folds": data.folds,
            "cohort_digest": data.cohort_digest,
            "fold_digest": data.fold_digest,
        },
        predict=lambda values: float(booster.predict(values.reshape(1, -1))[0]),
    )


def _binary(
    data: TargetTrainingSet,
    recipe: LightGBMBinaryEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if set(np.unique(data.y)) != {0.0, 1.0}:
        raise ValueError(
            f"{data.target}: binary LightGBM target must contain both 0 and 1"
        )
    raw_oof = _cross_fitted_predictions(
        data,
        objective="binary",
        seed=recipe.seed,
        num_boost_round=recipe.num_boost_round,
    )
    calibrated_oof = np.empty(len(data.y), dtype=float)
    for fold in range(data.folds):
        evaluate = data.fold_ids == fold
        calibrated_oof[evaluate] = _calibrate(
            raw_oof[evaluate],
            _fit_platt(raw_oof[~evaluate], data.y[~evaluate]),
        )
    calibration = _fit_platt(raw_oof, data.y)
    booster = _train_booster(
        data.x,
        data.y,
        objective="binary",
        seed=recipe.seed,
        num_boost_round=recipe.num_boost_round,
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(artifact_path))
    residuals = data.y - calibrated_oof
    predicted_class = calibrated_oof >= 0.5
    positives = data.y == 1
    negatives = ~positives
    parameters = {
        "num_boost_round": recipe.num_boost_round,
        "seed": recipe.seed,
    }
    return TrainedPredictor(
        predictor={
            "id": f"{data.target.lower()}-lightgbm",
            "target": data.target,
            "unit": data.unit,
            "target_kind": "binary",
            "runtime_type": "lightgbm.booster.v1",
            "architecture_id": "lightgbm_binary_calibrated_v1",
            "artifact": artifact_path.as_posix(),
            "predictive_family": "bernoulli_logit",
            "feature_names": list(data.feature_names),
            "config": {
                "training_method": recipe.estimator_id,
                "training_unit": "replicate_context_mean",
                "validation_method": f"{data.folds}-fold grouped validation CV",
                "interval_method": "cross-fitted OOF Platt calibration",
                "num_boost_round": recipe.num_boost_round,
                "calibration": {
                    "method": "out-of-fold Platt scaling",
                    "quality_evaluation": (
                        "cross-fitted out-of-fold Platt scaling"
                    ),
                    "intercept": calibration[0],
                    "slope": calibration[1],
                },
                "training": standard_training_metadata(
                    data,
                    estimator_id=recipe.estimator_id,
                    uncertainty="cross-fitted OOF probability calibration",
                    parameters=parameters,
                ),
            },
        },
        artifact=artifact_path,
        quality=TargetQualityMetric(
            target=data.target,
            parent_conditions=len(set(data.validation_groups)),
            mae=float(np.mean(np.abs(residuals))),
            rmse=float(np.sqrt(np.mean(residuals**2))),
            interval_coverage_90=0.0,
        ),
        diagnostics={
            "estimator_id": recipe.estimator_id,
            **parameters,
            "folds": data.folds,
            "cohort_digest": data.cohort_digest,
            "fold_digest": data.fold_digest,
            "roc_auc": _auc(data.y, calibrated_oof),
            "brier_score": float(np.mean(residuals**2)),
            "balanced_accuracy": float(
                (
                    np.mean(predicted_class[positives])
                    + np.mean(~predicted_class[negatives])
                )
                / 2
            ),
        },
        predict=lambda values: float(
            _calibrate(
                np.asarray(
                    booster.predict(values.reshape(1, -1)),
                    dtype=float,
                ),
                calibration,
            )[0]
        ),
    )


def train(
    data: TargetTrainingSet,
    recipe: LightGBMRegressionEstimatorRecipe | LightGBMBinaryEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if isinstance(recipe, LightGBMBinaryEstimatorRecipe):
        return _binary(data, recipe, artifact_path)
    return _regression(data, recipe, artifact_path)
