from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import poisson

from decision_workbench.modeling.model_lifecycle import TargetQualityMetric
from decision_workbench.modeling.packages.contracts import MissingOptionalDependency
from decision_workbench.modeling.training.feature_dataset import (
    TargetTrainingSet,
    prepared_feature_matrix,
)
from decision_workbench.modeling.training.recipe import (
    LogisticEstimatorRecipe,
    PoissonEstimatorRecipe,
)

from .types import TrainedPredictor, standard_training_metadata


RUNTIME_TYPE = "sklearn.skops.v1"
ARTIFACT_SUFFIX = ".skops"
ARTIFACT_FORMAT = "skops-allow-listed"


def _dependencies() -> tuple[Any, Any, Any, Any, Any]:
    try:
        import skops.io as skops_io
        from sklearn.linear_model import LogisticRegression, PoissonRegressor
        from sklearn.metrics import mean_poisson_deviance, roc_auc_score
    except ModuleNotFoundError as exc:
        raise MissingOptionalDependency(
            "install runtime-sklearn to train logistic.v1 or poisson.v1"
        ) from exc
    return (
        skops_io,
        LogisticRegression,
        PoissonRegressor,
        mean_poisson_deviance,
        roc_auc_score,
    )


def _evaluation_masks(data: TargetTrainingSet) -> list[tuple[np.ndarray, np.ndarray]]:
    if data.is_temporal_validation:
        return [(data.training_rows_for_fold(0), data.quality_rows)]
    return [
        (data.training_rows_for_fold(fold), data.fold_ids == fold)
        for fold in range(data.folds)
    ]


def _logistic(
    data: TargetTrainingSet,
    recipe: LogisticEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    (
        skops_io,
        LogisticRegression,
        _,
        _,
        roc_auc_score,
    ) = _dependencies()
    if data.target_kind != "binary" or set(np.unique(data.y)) != {0.0, 1.0}:
        raise ValueError(f"{data.target}: logistic.v1 requires binary labels 0 and 1")
    probabilities = np.full(len(data.y), np.nan, dtype=float)
    for train_rows, evaluate_rows in _evaluation_masks(data):
        model = LogisticRegression(
            C=recipe.c,
            max_iter=recipe.max_iter,
            random_state=recipe.seed,
            solver="lbfgs",
        )
        model.fit(
            prepared_feature_matrix(
                data,
                fit_rows=train_rows,
                transform_rows=train_rows,
            ),
            data.y[train_rows],
        )
        probabilities[evaluate_rows] = model.predict_proba(
            prepared_feature_matrix(
                data,
                fit_rows=train_rows,
                transform_rows=evaluate_rows,
            )
        )[:, 1]
    quality_rows = data.quality_rows
    evaluated_y = data.y[quality_rows]
    evaluated_probabilities = probabilities[quality_rows]
    residuals = evaluated_y - evaluated_probabilities
    predicted = evaluated_probabilities >= 0.5
    positives = evaluated_y == 1
    negatives = ~positives
    brier_score = float(np.mean(residuals**2))
    balanced_accuracy = float(
        (
            np.mean(predicted[positives])
            + np.mean(~predicted[negatives])
        )
        / 2
    )
    model = LogisticRegression(
        C=recipe.c,
        max_iter=recipe.max_iter,
        random_state=recipe.seed,
        solver="lbfgs",
    ).fit(prepared_feature_matrix(data), data.y)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    skops_io.dump(model, artifact_path)
    parameters = {
        "c": recipe.c,
        "max_iter": recipe.max_iter,
        "seed": recipe.seed,
        "calibration": recipe.calibration,
    }
    return TrainedPredictor(
        predictor={
            "id": f"{data.target.lower()}-logistic",
            "target": data.target,
            "unit": data.unit,
            "target_kind": "binary",
            "runtime_type": RUNTIME_TYPE,
            "architecture_id": "logistic_regression_v1",
            "artifact": artifact_path.as_posix(),
            "predictive_family": "bernoulli_logit",
            "feature_names": list(data.feature_names),
            "config": {
                "estimator_family": "logistic_regression_v1",
                "training_method": recipe.estimator_id,
                "calibration": {
                    "method": recipe.calibration,
                    "brier_score": brier_score,
                },
                "training": standard_training_metadata(
                    data,
                    estimator_id=recipe.estimator_id,
                    uncertainty="intrinsic logistic probability",
                    parameters=parameters,
                ),
            },
        },
        artifact=artifact_path,
        quality=TargetQualityMetric(
            target=data.target,
            parent_conditions=len(set(data.validation_groups)),
            mae=float(np.mean(np.abs(residuals))),
            rmse=float(np.sqrt(brier_score)),
            interval_coverage_90=0.0,
        ),
        diagnostics={
            "estimator_id": recipe.estimator_id,
            "folds": data.folds,
            "cohort_digest": data.cohort_digest,
            "fold_digest": data.fold_digest,
            "evaluation": "fold-local-logistic-probability",
            "roc_auc": float(
                roc_auc_score(evaluated_y, evaluated_probabilities)
            ),
            "brier_score": brier_score,
            "balanced_accuracy": balanced_accuracy,
            "calibration": recipe.calibration,
        },
        predict=lambda values: float(
            model.predict_proba(values.reshape(1, -1))[0, 1]
        ),
        evaluation_predictions=probabilities,
    )


def _poisson(
    data: TargetTrainingSet,
    recipe: PoissonEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    (
        skops_io,
        _,
        PoissonRegressor,
        mean_poisson_deviance,
        _,
    ) = _dependencies()
    if data.target_kind != "count":
        raise ValueError(f"{data.target}: poisson.v1 requires a count target")
    if np.any(data.y < 0) or np.any(data.y != np.floor(data.y)):
        raise ValueError(
            f"{data.target}: poisson.v1 requires nonnegative integer observations"
        )
    rates = np.full(len(data.y), np.nan, dtype=float)
    for train_rows, evaluate_rows in _evaluation_masks(data):
        model = PoissonRegressor(
            alpha=recipe.alpha,
            max_iter=recipe.max_iter,
        )
        model.fit(
            prepared_feature_matrix(
                data,
                fit_rows=train_rows,
                transform_rows=train_rows,
            ),
            data.y[train_rows],
        )
        rates[evaluate_rows] = model.predict(
            prepared_feature_matrix(
                data,
                fit_rows=train_rows,
                transform_rows=evaluate_rows,
            )
        )
    quality_rows = data.quality_rows
    evaluated_y = data.y[quality_rows]
    evaluated_rates = rates[quality_rows]
    if np.any(~np.isfinite(evaluated_rates)) or np.any(evaluated_rates < 0):
        raise ValueError(f"{data.target}: poisson.v1 produced invalid OOF rates")
    residuals = evaluated_y - evaluated_rates
    lower = poisson.ppf(0.05, evaluated_rates)
    upper = poisson.ppf(0.95, evaluated_rates)
    interval_coverage = float(
        np.mean((evaluated_y >= lower) & (evaluated_y <= upper))
    )
    model = PoissonRegressor(
        alpha=recipe.alpha,
        max_iter=recipe.max_iter,
    ).fit(prepared_feature_matrix(data), data.y)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    skops_io.dump(model, artifact_path)
    deviance = float(mean_poisson_deviance(evaluated_y, evaluated_rates))
    parameters = {
        "alpha": recipe.alpha,
        "max_iter": recipe.max_iter,
    }
    return TrainedPredictor(
        predictor={
            "id": f"{data.target.lower()}-poisson",
            "target": data.target,
            "unit": data.unit,
            "target_kind": "count",
            "runtime_type": RUNTIME_TYPE,
            "architecture_id": "poisson_regression_v1",
            "artifact": artifact_path.as_posix(),
            "predictive_family": "poisson_log",
            "feature_names": list(data.feature_names),
            "config": {
                "estimator_family": "poisson_regression_v1",
                "training_method": recipe.estimator_id,
                "training": standard_training_metadata(
                    data,
                    estimator_id=recipe.estimator_id,
                    uncertainty="Poisson observation distribution",
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
            interval_coverage_90=interval_coverage,
        ),
        diagnostics={
            "estimator_id": recipe.estimator_id,
            "folds": data.folds,
            "cohort_digest": data.cohort_digest,
            "fold_digest": data.fold_digest,
            "evaluation": "fold-local-poisson-rate",
            "mean_poisson_deviance": deviance,
            "minimum_oof_rate": float(np.min(evaluated_rates)),
            "interval_coverage_90": interval_coverage,
            "interval_method": "poisson-equal-tail-5-95",
            "interval_observations": int(len(evaluated_y)),
        },
        predict=lambda values: float(model.predict(values.reshape(1, -1))[0]),
        evaluation_predictions=rates,
    )


def train(
    data: TargetTrainingSet,
    recipe: LogisticEstimatorRecipe | PoissonEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if isinstance(recipe, LogisticEstimatorRecipe):
        return _logistic(data, recipe, artifact_path)
    return _poisson(data, recipe, artifact_path)
