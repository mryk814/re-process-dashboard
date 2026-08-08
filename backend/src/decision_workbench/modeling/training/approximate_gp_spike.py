"""Bounded, non-production random-feature GP spike.

This module is intentionally not imported by the estimator registry or the
Package runtime.  It exists to measure one safe approximation on the exact
same compiled cohort, folds, and replicate semantics before any adoption
decision is made.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from decision_workbench.modeling.training.feature_dataset import (
    TargetTrainingSet,
    prepared_feature_matrix,
)


SPIKE_ID = "fixed-random-feature-gp-spike.v1"
SPIKE_ARTIFACT_FORMAT = "bounded-npz"
SPIKE_DEFAULT_BASIS_COUNT = 64
SPIKE_MAX_BASIS_COUNT = 128
SPIKE_MAX_ROWS = 2_000
SPIKE_MAX_FEATURES = 64


@dataclass(frozen=True)
class _RffFit:
    feature_mean: np.ndarray
    feature_scale: np.ndarray
    weights: np.ndarray
    phases: np.ndarray
    coefficient: np.ndarray
    target_mean: float
    predictive_std: float


@dataclass(frozen=True)
class ApproximateGpSpikeEvaluation:
    estimator_id: str
    basis_count: int
    seed: int
    cohort_digest: str
    fold_digest: str
    validation_plan_digest: str
    raw_observation_count: int
    effective_replicate_context_count: int
    predictions: np.ndarray
    predictive_std: np.ndarray
    mae: float
    rmse: float
    interval_coverage_90: float
    uncertainty_label: str = "approximate predictive observation interval"


def _standardize(
    values: np.ndarray,
    *,
    feature_mean: np.ndarray | None = None,
    feature_scale: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = values.mean(axis=0) if feature_mean is None else feature_mean
    scale = values.std(axis=0) if feature_scale is None else feature_scale
    scale = np.asarray(scale, dtype=float).copy()
    scale[scale < 1e-9] = 1.0
    return (values - mean) / scale, np.asarray(mean, dtype=float), scale


def _features(
    values: np.ndarray,
    weights: np.ndarray,
    phases: np.ndarray,
) -> np.ndarray:
    return np.sqrt(2.0 / weights.shape[1]) * np.cos(values @ weights + phases)


def _fit(
    x: np.ndarray,
    y: np.ndarray,
    *,
    basis_count: int,
    seed: int,
    ridge_alpha: float,
) -> _RffFit:
    standardized, feature_mean, feature_scale = _standardize(x)
    rng = np.random.default_rng(seed)
    weights = rng.normal(0.0, 1.0, size=(x.shape[1], basis_count))
    phases = rng.uniform(0.0, 2.0 * np.pi, size=basis_count)
    design = _features(standardized, weights, phases)
    target_mean = float(y.mean())
    centered = y - target_mean
    gram = design.T @ design
    gram.flat[:: len(gram) + 1] += ridge_alpha
    coefficient = np.linalg.solve(gram, design.T @ centered)
    residual = centered - design @ coefficient
    predictive_std = max(float(np.std(residual, ddof=1)), 1e-8)
    return _RffFit(
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        weights=weights,
        phases=phases,
        coefficient=coefficient,
        target_mean=target_mean,
        predictive_std=predictive_std,
    )

def _predict(fitted: _RffFit, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    standardized, _, _ = _standardize(
        x,
        feature_mean=fitted.feature_mean,
        feature_scale=fitted.feature_scale,
    )
    mean = fitted.target_mean + _features(
        standardized,
        fitted.weights,
        fitted.phases,
    ) @ fitted.coefficient
    variance = np.full(len(x), fitted.predictive_std**2, dtype=float)
    return mean, variance


def fit_spike(
    data: TargetTrainingSet,
    *,
    train_rows: np.ndarray | None = None,
    basis_count: int = SPIKE_DEFAULT_BASIS_COUNT,
    seed: int = 20260730,
    ridge_alpha: float = 1e-3,
    artifact_path: Path | None = None,
) -> _RffFit:
    """Fit a deterministic numeric-only RFF approximation for a bounded spike."""

    if not 1 <= basis_count <= SPIKE_MAX_BASIS_COUNT:
        raise ValueError(f"{SPIKE_ID}: basis_count must be 1..{SPIKE_MAX_BASIS_COUNT}")
    if len(data.y) > SPIKE_MAX_ROWS:
        raise ValueError(
            f"{SPIKE_ID}: effective training rows {len(data.y)} exceed {SPIKE_MAX_ROWS}; "
            "rows are never truncated or subsampled"
        )
    if data.x.shape[1] > SPIKE_MAX_FEATURES:
        raise ValueError(
            f"{SPIKE_ID}: feature count {data.x.shape[1]} exceeds {SPIKE_MAX_FEATURES}"
        )
    rows = (
        np.ones(len(data.y), dtype=bool)
        if train_rows is None
        else np.asarray(train_rows, dtype=bool)
    )
    if rows.shape != (len(data.y),) or rows.sum() < 2:
        raise ValueError(f"{SPIKE_ID}: at least two training contexts are required")
    x = prepared_feature_matrix(data, fit_rows=rows, transform_rows=rows)
    fitted = _fit(
        x,
        data.y[rows],
        basis_count=basis_count,
        seed=seed,
        ridge_alpha=ridge_alpha,
    )
    if artifact_path is not None:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            artifact_path,
            estimator_id=np.asarray(SPIKE_ID),
            basis_count=np.asarray(basis_count),
            seed=np.asarray(seed),
            feature_mean=fitted.feature_mean,
            feature_scale=fitted.feature_scale,
            weights=fitted.weights,
            phases=fitted.phases,
            coefficient=fitted.coefficient,
            target_mean=np.asarray(fitted.target_mean),
            predictive_std=np.asarray(fitted.predictive_std),
        )
    return fitted


def evaluate_same_cohort(
    data: TargetTrainingSet,
    *,
    basis_count: int = SPIKE_DEFAULT_BASIS_COUNT,
    seed: int = 20260730,
    ridge_alpha: float = 1e-3,
) -> ApproximateGpSpikeEvaluation:
    """Evaluate the spike using the already-fixed validation assignment."""

    predictions = np.full(len(data.y), np.nan, dtype=float)
    predictive_variance = np.full(len(data.y), np.nan, dtype=float)
    if data.is_temporal_validation:
        evaluate = data.quality_rows
        train_rows = data.training_rows_for_fold(0) | data.temporal_calibration_rows
        fitted = fit_spike(
            data,
            train_rows=train_rows,
            basis_count=basis_count,
            seed=seed,
            ridge_alpha=ridge_alpha,
        )
        predictions[evaluate], predictive_variance[evaluate] = _predict(
            fitted,
            prepared_feature_matrix(
                data,
                fit_rows=train_rows,
                transform_rows=evaluate,
            ),
        )
    else:
        for fold in range(data.folds):
            evaluate = data.fold_ids == fold
            train_rows = data.training_rows_for_fold(fold)
            fitted = fit_spike(
                data,
                train_rows=train_rows,
                basis_count=basis_count,
                seed=(seed + fold + 1) % (2**32),
                ridge_alpha=ridge_alpha,
            )
            predictions[evaluate], predictive_variance[evaluate] = _predict(
                fitted,
                prepared_feature_matrix(
                    data,
                    fit_rows=train_rows,
                    transform_rows=evaluate,
                ),
            )
    quality_rows = data.quality_rows & np.isfinite(predictions)
    residuals = data.y[quality_rows] - predictions[quality_rows]
    z90 = 1.6448536269514722
    return ApproximateGpSpikeEvaluation(
        estimator_id=SPIKE_ID,
        basis_count=basis_count,
        seed=seed,
        cohort_digest=data.cohort_digest,
        fold_digest=data.fold_digest,
        validation_plan_digest=data.validation_plan_digest,
        raw_observation_count=data.raw_observation_count,
        effective_replicate_context_count=data.effective_replicate_context_count,
        predictions=predictions,
        predictive_std=np.sqrt(predictive_variance),
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        interval_coverage_90=float(
            np.mean(np.abs(residuals) <= z90 * np.sqrt(predictive_variance[quality_rows]))
        ),
    )
