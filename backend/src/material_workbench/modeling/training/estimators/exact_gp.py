from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

from material_workbench.modeling.model_lifecycle import TargetQualityMetric
from material_workbench.modeling.training.feature_dataset import TargetTrainingSet
from material_workbench.modeling.training.recipe import ExactGPEstimatorRecipe

from .types import TrainedPredictor


def _grouped_quality(
    data: TargetTrainingSet,
    alpha: np.ndarray,
    precision: np.ndarray,
) -> TargetQualityMetric:
    indices_by_group: dict[str, list[int]] = {}
    for index, group in enumerate(data.validation_groups):
        indices_by_group.setdefault(group, []).append(index)
    residuals = np.empty(len(data.y), dtype=float)
    conditional_variance = np.empty(len(data.y), dtype=float)
    for indexes in indices_by_group.values():
        block = np.ix_(indexes, indexes)
        conditional_covariance = np.linalg.inv(precision[block])
        residuals[indexes] = conditional_covariance @ alpha[indexes]
        conditional_variance[indexes] = np.diag(conditional_covariance)
    if np.any(conditional_variance <= 0):
        raise ValueError(
            f"{data.target}: GP conditional variance must be positive"
        )
    z90 = 1.6448536269514722
    return TargetQualityMetric(
        target=data.target,
        parent_conditions=len(indices_by_group),
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        interval_coverage_90=float(
            np.mean(
                np.abs(residuals)
                <= z90 * np.sqrt(conditional_variance)
            )
        ),
        interval_coverage_method="loo-predictive-interval",
        interval_coverage_observations=len(residuals),
    )


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
            options={"maxiter": 90, "ftol": 1e-6, "gtol": 1e-5, "maxls": 20},
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


def train(
    data: TargetTrainingSet,
    recipe: ExactGPEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if len(data.y) > recipe.max_rows:
        raise ValueError(
            f"{data.target}: exact GP received {len(data.y)} rows; "
            f"recipe max_rows is {recipe.max_rows}"
        )
    feature_mean = data.x.mean(axis=0)
    feature_scale = data.x.std(axis=0)
    feature_scale[feature_scale < 1e-9] = 1.0
    train_x = (data.x - feature_mean) / feature_scale
    mean_repeats = max(float(np.median(data.repeat_counts)), 1.0)
    noise_anchor = max(data.observation_variance / mean_repeats, 1e-9)
    lengthscale, outputscale, train_noise, diagnostics = _fit_hyperparameters(
        train_x,
        data.y,
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
    mean = float(data.y.mean())
    alpha = precision @ (data.y - mean)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(
        artifact_path,
        train_x=train_x,
        train_y=data.y,
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        lengthscale=lengthscale,
        outputscale=np.asarray(outputscale),
        train_noise=np.asarray(train_noise),
        observation_noise=np.asarray(data.observation_variance),
        mean=np.asarray(mean),
        precision=precision,
        alpha=alpha,
    )

    def predict(values: np.ndarray) -> float:
        point = (values - feature_mean) / feature_scale
        cross_scaled = (train_x - point) / lengthscale
        cross = outputscale * np.exp(
            -0.5 * np.sum(cross_scaled * cross_scaled, axis=1)
        )
        return mean + float(cross @ alpha)

    return TrainedPredictor(
        predictor={
            "id": f"{data.target.lower()}-exact-gp",
            "target": data.target,
            "unit": data.unit,
            "target_kind": data.target_kind,
            "runtime_type": "builtin.exact_gp.v1",
            "architecture_id": "exact_rbf_ard_v1",
            "artifact": artifact_path.as_posix(),
            "predictive_family": "normal",
            "feature_names": list(data.feature_names),
            "config": {
                "training_method": "exact-gp-rbf.v1",
                "training_unit": "replicate_context_mean",
                "validation_method": "leave-one-validation-group-out",
                "interval_method": "normal predictive distribution",
                "kernel": "ARD-RBF",
                "replicate_noise": "pooled_within_training_context",
                "optimizer_restarts": recipe.restarts,
                "seed": recipe.seed,
            },
        },
        artifact=artifact_path,
        quality=_grouped_quality(
            data,
            alpha,
            precision,
        ),
        diagnostics=diagnostics,
        predict=predict,
    )
