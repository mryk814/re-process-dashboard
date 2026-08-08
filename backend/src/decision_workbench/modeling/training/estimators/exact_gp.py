from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

from decision_workbench.modeling.model_lifecycle import TargetQualityMetric
from decision_workbench.modeling.training.feature_dataset import (
    TargetTrainingSet,
    observation_variance_for_rows,
    prepared_feature_matrix,
)
from decision_workbench.modeling.training.recipe import ExactGPEstimatorRecipe
from decision_workbench.modeling.training.capacity import (
    EXACT_GP_OPTIMIZER_MAX_ITERATIONS,
    capacity_context_from_training_set,
    resolve_exact_gp_capacity,
)

from .types import TrainedPredictor, standard_training_metadata

RUNTIME_TYPE = "builtin.exact_gp.v1"
ARTIFACT_SUFFIX = ".npz"
ARTIFACT_FORMAT = "bounded-npz"


@dataclass(frozen=True)
class _GPFit:
    train_x: np.ndarray
    train_y: np.ndarray
    feature_mean: np.ndarray
    feature_scale: np.ndarray
    lengthscale: np.ndarray
    outputscale: float
    train_noise: float
    observation_noise: float
    mean: float
    precision: np.ndarray
    alpha: np.ndarray
    diagnostics: dict[str, Any]


def _fit_hyperparameters(
    train_x: np.ndarray,
    train_y: np.ndarray,
    noise_anchor: float,
    recipe: ExactGPEstimatorRecipe,
) -> tuple[np.ndarray, float, float, dict[str, Any]]:
    target_mean = float(np.mean(train_y))
    target_scale = max(float(np.std(train_y)), 1e-8)
    target = (train_y - target_mean) / target_scale
    pairwise_sq = (train_x[:, None, :] - train_x[None, :, :]) ** 2
    normalized_noise = float(
        np.clip(noise_anchor / (target_scale * target_scale), 1e-5, 1.0)
    )
    feature_count = train_x.shape[1]
    log_length_prior = np.log(2.0)
    length_bounds = (np.log(0.08), np.log(20.0))
    signal_bounds = (np.log(0.03), np.log(20.0))
    noise_bounds = (
        np.log(max(1e-6, normalized_noise / 8.0)),
        np.log(min(2.0, max(normalized_noise * 8.0, 2e-5))),
    )
    identity = np.eye(len(train_x))

    def objective(theta: np.ndarray) -> tuple[float, np.ndarray]:
        log_length = theta[:feature_count]
        signal = float(np.exp(theta[-2]))
        noise = float(np.exp(theta[-1]))
        scaled_sq = pairwise_sq / np.exp(2.0 * log_length)[None, None, :]
        signal_kernel = signal * np.exp(-0.5 * np.sum(scaled_sq, axis=2))
        covariance = signal_kernel + (noise + 1e-8) * identity
        try:
            cholesky = np.linalg.cholesky(covariance)
        except np.linalg.LinAlgError:
            return 1e30, np.zeros_like(theta)
        alpha = np.linalg.solve(
            cholesky.T,
            np.linalg.solve(cholesky, target),
        )
        precision = np.linalg.solve(
            cholesky.T,
            np.linalg.solve(cholesky, identity),
        )
        common = precision - np.outer(alpha, alpha)
        nll = float(
            0.5 * target @ alpha
            + np.log(np.diag(cholesky)).sum()
            + 0.5 * len(train_x) * np.log(2.0 * np.pi)
        )
        centered = log_length - float(np.mean(log_length))
        nll += (
            0.04 * float(centered @ centered)
            + 0.01 * float(np.sum((log_length - log_length_prior) ** 2))
            + 0.04 * float((theta[-1] - np.log(normalized_noise)) ** 2)
        )
        gradient = np.empty_like(theta)
        gradient[:feature_count] = 0.5 * np.einsum(
            "ij,ijk->k",
            common * signal_kernel,
            scaled_sq,
            optimize=True,
        )
        gradient[:feature_count] += (
            0.08 * centered
            + 0.02 * (log_length - log_length_prior)
        )
        gradient[-2] = 0.5 * np.sum(common * signal_kernel)
        gradient[-1] = (
            0.5 * noise * np.trace(common)
            + 0.08 * (theta[-1] - np.log(normalized_noise))
        )
        return nll, gradient

    base = np.r_[
        np.full(feature_count, log_length_prior),
        np.log(1.0),
        np.log(normalized_noise),
    ]
    rng = np.random.default_rng(recipe.seed)
    starts = [base]
    for _ in range(1, recipe.restarts):
        candidate = base.copy()
        candidate[:feature_count] += rng.normal(0.0, 0.55, feature_count)
        candidate[-2] += rng.normal(0.0, 0.45)
        candidate[-1] += rng.normal(0.0, 0.35)
        starts.append(candidate)
    bounds = [length_bounds] * feature_count + [signal_bounds, noise_bounds]
    results = [
        minimize(
            objective,
            np.clip(
                start,
                [item[0] for item in bounds],
                [item[1] for item in bounds],
            ),
            method="L-BFGS-B",
            jac=True,
            bounds=bounds,
            options={
                "maxiter": EXACT_GP_OPTIMIZER_MAX_ITERATIONS,
                "ftol": 1e-6,
                "gtol": 1e-5,
                "maxls": 20,
            },
        )
        for start in starts
    ]
    finite = [result for result in results if np.isfinite(result.fun)]
    if not finite:
        raise RuntimeError("exact GP optimization found no finite solution")
    best = min(finite, key=lambda result: float(result.fun))
    lengthscale = np.exp(best.x[:feature_count])
    outputscale = float(np.exp(best.x[-2]) * target_scale * target_scale)
    train_noise = float(np.exp(best.x[-1]) * target_scale * target_scale)
    return lengthscale, outputscale, train_noise, {
        "estimator_id": recipe.estimator_id,
        "optimizer": "L-BFGS-B",
        "restarts": len(results),
        "converged_restarts": sum(bool(result.success) for result in results),
        "best_objective": float(best.fun),
        "seed": recipe.seed,
        "lengthscale": {
            "min": float(np.min(lengthscale)),
            "median": float(np.median(lengthscale)),
            "max": float(np.max(lengthscale)),
        },
    }


def _fit_model(
    data: TargetTrainingSet,
    rows: np.ndarray,
    recipe: ExactGPEstimatorRecipe,
) -> _GPFit:
    raw_x = prepared_feature_matrix(
        data,
        fit_rows=rows,
        transform_rows=rows,
    )
    train_y = data.y[rows]
    feature_mean = raw_x.mean(axis=0)
    feature_scale = raw_x.std(axis=0)
    feature_scale[feature_scale < 1e-9] = 1.0
    train_x = (raw_x - feature_mean) / feature_scale
    observation_noise = observation_variance_for_rows(data, rows)
    repeat_counts = np.asarray(data.repeat_counts, dtype=float)[rows]
    mean_repeats = max(float(np.median(repeat_counts)), 1.0)
    noise_anchor = max(observation_noise / mean_repeats, 1e-9)
    lengthscale, outputscale, train_noise, diagnostics = _fit_hyperparameters(
        train_x,
        train_y,
        noise_anchor,
        recipe,
    )
    scaled = (train_x[:, None, :] - train_x[None, :, :]) / lengthscale
    covariance = outputscale * np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
    covariance.flat[:: len(train_x) + 1] += train_noise
    cholesky = np.linalg.cholesky(covariance)
    identity = np.eye(len(train_x))
    precision = np.linalg.solve(
        cholesky.T,
        np.linalg.solve(cholesky, identity),
    )
    mean = float(train_y.mean())
    return _GPFit(
        train_x=train_x,
        train_y=train_y,
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        lengthscale=lengthscale,
        outputscale=outputscale,
        train_noise=train_noise,
        observation_noise=observation_noise,
        mean=mean,
        precision=precision,
        alpha=precision @ (train_y - mean),
        diagnostics=diagnostics,
    )


def _predict_model(
    fitted: _GPFit,
    raw_x: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    points = (raw_x - fitted.feature_mean) / fitted.feature_scale
    scaled = (
        fitted.train_x[:, None, :] - points[None, :, :]
    ) / fitted.lengthscale
    cross = fitted.outputscale * np.exp(
        -0.5 * np.sum(scaled * scaled, axis=2)
    )
    estimates = fitted.mean + cross.T @ fitted.alpha
    latent_variance = fitted.outputscale - np.einsum(
        "ik,ij,jk->k",
        cross,
        fitted.precision,
        cross,
        optimize=True,
    )
    predictive_variance = (
        np.maximum(latent_variance, 0.0) + fitted.observation_noise
    )
    return estimates, predictive_variance


def _honest_grouped_predictions(
    data: TargetTrainingSet,
    recipe: ExactGPEstimatorRecipe,
) -> tuple[np.ndarray, np.ndarray]:
    predictions = np.full(len(data.y), np.nan, dtype=float)
    predictive_variance = np.full(len(data.y), np.nan, dtype=float)
    if data.is_temporal_validation:
        evaluate = data.quality_rows
        train_rows = data.training_rows_for_fold(0) | data.temporal_calibration_rows
        fitted = _fit_model(data, train_rows, recipe)
        predictions[evaluate], predictive_variance[evaluate] = _predict_model(
            fitted,
            prepared_feature_matrix(
                data,
                fit_rows=train_rows,
                transform_rows=evaluate,
            ),
        )
        return predictions, predictive_variance
    for fold in range(data.folds):
        evaluate = data.fold_ids == fold
        train_rows = data.training_rows_for_fold(fold)
        fold_recipe = recipe.model_copy(
            update={"seed": (recipe.seed + fold + 1) % (2**32)}
        )
        fitted = _fit_model(data, train_rows, fold_recipe)
        (
            predictions[evaluate],
            predictive_variance[evaluate],
        ) = _predict_model(
            fitted,
            prepared_feature_matrix(
                data,
                fit_rows=train_rows,
                transform_rows=evaluate,
            ),
        )
    return predictions, predictive_variance


def _honest_grouped_quality(
    data: TargetTrainingSet,
    recipe: ExactGPEstimatorRecipe,
) -> tuple[TargetQualityMetric, np.ndarray]:
    predictions, predictive_variance = _honest_grouped_predictions(data, recipe)
    quality_rows = data.quality_rows
    residuals = data.y[quality_rows] - predictions[quality_rows]
    evaluated_variance = predictive_variance[quality_rows]
    z90 = 1.6448536269514722
    return (
        TargetQualityMetric(
            target=data.target,
            parent_conditions=len(set(data.validation_groups)),
            mae=float(np.mean(np.abs(residuals))),
            rmse=float(np.sqrt(np.mean(residuals**2))),
            interval_coverage_90=float(
                np.mean(
                    np.abs(residuals)
                    <= z90 * np.sqrt(evaluated_variance)
                )
            ),
            interval_coverage_method=(
                "temporal-holdout-predictive-interval"
                if data.is_temporal_validation
                else "grouped-fold-predictive-interval"
            ),
            interval_coverage_observations=int(quality_rows.sum()),
        ),
        predictions,
    )


def train(
    data: TargetTrainingSet,
    recipe: ExactGPEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    capacity_context = capacity_context_from_training_set(data, recipe)
    capacity_resolution = resolve_exact_gp_capacity(capacity_context)
    if capacity_resolution.decision == "approximate_required":
        raise ValueError(
            f"{data.target}: exact GP capacity requires an explicit alternative path; "
            f"recipe max_rows is {recipe.max_rows}; "
            + "; ".join(capacity_resolution.reasons)
        )
    quality, evaluation_predictions = _honest_grouped_quality(data, recipe)
    fitted = _fit_model(data, np.ones(len(data.y), dtype=bool), recipe)
    diagnostics = dict(fitted.diagnostics)
    diagnostics.update({
        "folds": data.folds,
        "cohort_digest": data.cohort_digest,
        "fold_digest": data.fold_digest,
        "evaluation": "outer-fold-refit",
        "capacity_resolution": capacity_resolution.model_dump(mode="json"),
    })
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        artifact_path,
        train_x=fitted.train_x,
        train_y=fitted.train_y,
        feature_mean=fitted.feature_mean,
        feature_scale=fitted.feature_scale,
        lengthscale=fitted.lengthscale,
        outputscale=np.asarray(fitted.outputscale),
        train_noise=np.asarray(fitted.train_noise),
        observation_noise=np.asarray(fitted.observation_noise),
        mean=np.asarray(fitted.mean),
        precision=fitted.precision,
        alpha=fitted.alpha,
    )

    def predict(values: np.ndarray) -> float:
        estimates, _ = _predict_model(fitted, values.reshape(1, -1))
        return float(estimates[0])

    return TrainedPredictor(
        predictor={
            "id": f"{data.target.lower()}-exact-gp",
            "target": data.target,
            "unit": data.unit,
            "target_kind": data.target_kind,
            "runtime_type": RUNTIME_TYPE,
            "architecture_id": "exact_rbf_ard_v1",
            "artifact": artifact_path.as_posix(),
            "predictive_family": "normal",
            "feature_names": list(data.feature_names),
            "config": {
                "training_method": "exact-gp-rbf.v1",
                "training_unit": "replicate_context_mean",
                "validation_method": (
                    f"temporal holdout by {data.validation_plan.time_key}"
                    if data.is_temporal_validation
                    else f"{data.folds}-fold "
                    + (
                        "grouped"
                        if data.validation_plan.strategy == "grouped_kfold"
                        else data.validation_plan.strategy
                    )
                    + " validation"
                ),
                "interval_method": "normal predictive distribution",
                "kernel": "ARD-RBF",
                "replicate_noise": "pooled_within_training_context",
                "optimizer_restarts": recipe.restarts,
                "seed": recipe.seed,
                "training": standard_training_metadata(
                    data,
                    estimator_id=recipe.estimator_id,
                    uncertainty="normal predictive distribution",
                    parameters={
                        "restarts": recipe.restarts,
                        "max_rows": recipe.max_rows,
                        "seed": recipe.seed,
                    },
                    capacity=capacity_resolution.model_dump(mode="json"),
                ),
            },
        },
        artifact=artifact_path,
        quality=quality,
        diagnostics=diagnostics,
        predict=predict,
        evaluation_predictions=evaluation_predictions,
    )
