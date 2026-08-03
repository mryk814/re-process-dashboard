from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from decision_workbench.adapters.builtin_additive_terms import bspline_basis
from decision_workbench.contracts.inference_policy_contracts import (
    InferenceDiagnostics,
    InferenceIdentity,
)
from decision_workbench.modeling.inference_policy import inference_policy
from decision_workbench.modeling.model_lifecycle import TargetQualityMetric
from decision_workbench.modeling.training.feature_dataset import (
    TargetTrainingSet,
    observation_variance_for_rows,
    prepared_feature_matrix,
)
from decision_workbench.modeling.training.recipe import (
    BayesianAdditiveSplineEstimatorRecipe,
)

from .types import TrainedPredictor, standard_training_metadata

RUNTIME_TYPE = "builtin.additive_terms.v1"
ARTIFACT_SUFFIX = ".npz"
ARTIFACT_FORMAT = "bounded-npz"
_Z90 = 1.6448536269514722
_MAX_FEATURES = 48
_MAX_ROWS = 20_000
_MAX_BASIS_COLUMNS = 288


@dataclass(frozen=True)
class _TermFit:
    feature_index: int
    kind: str
    degree: int | None
    knots: np.ndarray | None
    center: np.ndarray

    def basis(self, values: np.ndarray) -> np.ndarray:
        column = values[:, self.feature_index]
        if self.kind == "linear":
            return column[:, None]
        assert self.knots is not None and self.degree is not None
        return np.vstack(
            [bspline_basis(float(value), self.knots, self.degree) for value in column]
        )


@dataclass(frozen=True)
class _Fit:
    terms: tuple[_TermFit, ...]
    coefficients: np.ndarray
    covariance: np.ndarray
    observation_variance: float
    diagnostics: dict[str, Any]

    def design(self, values: np.ndarray) -> np.ndarray:
        blocks = [np.ones((len(values), 1), dtype=float)]
        blocks.extend(
            term.basis(values) - term.center
            for term in self.terms
        )
        return np.column_stack(blocks)

    def predict(self, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        design = self.design(values)
        mean = design @ self.coefficients
        latent = np.einsum(
            "ij,jk,ik->i",
            design,
            self.covariance,
            design,
            optimize=True,
        )
        return mean, np.maximum(latent, 0.0) + self.observation_variance


def _knots(
    values: np.ndarray,
    recipe: BayesianAdditiveSplineEstimatorRecipe,
) -> np.ndarray:
    lower, upper = float(np.min(values)), float(np.max(values))
    internal_count = recipe.max_basis_per_feature - recipe.spline_degree - 1
    quantiles = np.linspace(0.0, 1.0, internal_count + 2)[1:-1]
    internal = np.unique(np.quantile(values, quantiles))
    internal = internal[(internal > lower) & (internal < upper)]
    return np.r_[
        np.repeat(lower, recipe.spline_degree + 1),
        internal,
        np.repeat(upper, recipe.spline_degree + 1),
    ]


def _fit(
    values: np.ndarray,
    target: np.ndarray,
    recipe: BayesianAdditiveSplineEstimatorRecipe,
    *,
    observation_variance_floor: float,
) -> _Fit:
    if values.shape[1] > _MAX_FEATURES:
        raise ValueError(
            f"Bayesian additive recipe supports at most {_MAX_FEATURES} prepared features"
        )
    terms: list[_TermFit] = []
    centered_blocks: list[np.ndarray] = [np.ones((len(values), 1), dtype=float)]
    penalty_blocks: list[np.ndarray] = [np.asarray([[recipe.intercept_precision]])]
    term_kinds: list[str] = []
    for feature_index in range(values.shape[1]):
        column = values[:, feature_index]
        unique = np.unique(column)
        if len(unique) < 2:
            continue
        if len(unique) < recipe.min_unique_values_for_smooth:
            basis = column[:, None]
            kind = "linear"
            degree = None
            knots = None
            penalty = np.asarray([[recipe.linear_precision]])
        else:
            knots = _knots(column, recipe)
            basis = np.vstack(
                [
                    bspline_basis(float(value), knots, recipe.spline_degree)
                    for value in column
                ]
            )
            kind = "bspline_univariate"
            degree = recipe.spline_degree
            difference = np.diff(np.eye(basis.shape[1]), n=2, axis=0)
            penalty = (
                recipe.smoothness_precision * (difference.T @ difference)
                + np.eye(basis.shape[1]) * recipe.linear_precision
            )
        center = basis.mean(axis=0)
        centered_blocks.append(basis - center)
        penalty_blocks.append(penalty)
        terms.append(
            _TermFit(
                feature_index=feature_index,
                kind=kind,
                degree=degree,
                knots=knots,
                center=center,
            )
        )
        term_kinds.append(kind)
    if not terms:
        raise ValueError("Bayesian additive recipe needs at least one varying feature")

    design = np.column_stack(centered_blocks)
    if design.shape[1] - 1 > _MAX_BASIS_COLUMNS:
        raise ValueError(
            "Bayesian additive recipe exceeds its 288 basis-column capacity"
        )
    penalty = np.zeros((design.shape[1], design.shape[1]), dtype=float)
    offset = 0
    for block in penalty_blocks:
        width = block.shape[0]
        penalty[offset : offset + width, offset : offset + width] = block
        offset += width

    initial = np.linalg.solve(
        design.T @ design + penalty,
        design.T @ target,
    )
    residual = target - design @ initial
    plugin_variance = max(
        float(np.mean(residual**2)),
        float(observation_variance_floor),
        1e-10,
    )
    posterior_precision = design.T @ design / plugin_variance + penalty
    posterior_covariance = np.linalg.solve(
        posterior_precision,
        np.eye(posterior_precision.shape[0]),
    )
    posterior_covariance = (
        posterior_covariance + posterior_covariance.T
    ) / 2.0
    centered_mean = np.linalg.solve(
        posterior_precision,
        design.T @ target / plugin_variance,
    )

    return _Fit(
        terms=tuple(terms),
        coefficients=centered_mean,
        covariance=posterior_covariance,
        observation_variance=plugin_variance,
        diagnostics={
            "term_kinds": term_kinds,
            "coefficient_count": len(centered_mean),
            "conditioning": "fixed_basis_fixed_smoothing_plugin_noise",
            "noise_policy": recipe.noise_policy,
            "penalized_posterior_precision_condition_number": float(
                np.linalg.cond(posterior_precision)
            ),
        },
    )


def _honest_predictions(
    data: TargetTrainingSet,
    recipe: BayesianAdditiveSplineEstimatorRecipe,
) -> tuple[np.ndarray, np.ndarray]:
    predictions = np.full(len(data.y), np.nan, dtype=float)
    variances = np.full(len(data.y), np.nan, dtype=float)
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
        fitted = _fit(
            train_x,
            data.y[train_rows],
            recipe,
            observation_variance_floor=observation_variance_for_rows(
                data, train_rows
            ),
        )
        evaluate_x = prepared_feature_matrix(
            data,
            fit_rows=train_rows,
            transform_rows=evaluate,
        )
        predictions[evaluate], variances[evaluate] = fitted.predict(evaluate_x)
    return predictions, variances


def train(
    data: TargetTrainingSet,
    recipe: BayesianAdditiveSplineEstimatorRecipe,
    artifact_path: Path,
) -> TrainedPredictor:
    if data.target_kind != "continuous":
        raise ValueError("Bayesian additive spline supports continuous targets only")
    if len(data.y) > _MAX_ROWS:
        raise ValueError(
            f"Bayesian additive recipe supports at most {_MAX_ROWS} rows"
        )
    predictions, variances = _honest_predictions(data, recipe)
    quality_rows = data.quality_rows
    residuals = data.y[quality_rows] - predictions[quality_rows]
    evaluated_variance = variances[quality_rows]
    fitted = _fit(
        prepared_feature_matrix(data),
        data.y,
        recipe,
        observation_variance_floor=observation_variance_for_rows(
            data, np.ones(len(data.y), dtype=bool)
        ),
    )

    arrays: dict[str, np.ndarray] = {
        "intercept": np.asarray(fitted.coefficients[0]),
        "posterior_covariance": fitted.covariance,
        "observation_noise_variance": np.asarray(
            fitted.observation_variance
        ),
    }
    terms: list[dict[str, Any]] = []
    coefficient_offset = 1
    for index, term in enumerate(fitted.terms):
        width = len(term.center)
        coefficients = fitted.coefficients[
            coefficient_offset : coefficient_offset + width
        ]
        coefficient_offset += width
        term_config: dict[str, Any] = {
            "id": f"feature_{term.feature_index}",
            "kind": term.kind,
            "feature_index": term.feature_index,
        }
        arrays[f"term_{index}_coefficients"] = coefficients
        arrays[f"term_{index}_center"] = term.center
        if term.kind == "bspline_univariate":
            assert term.knots is not None and term.degree is not None
            arrays[f"term_{index}_knots"] = term.knots
            term_config["degree"] = term.degree
        terms.append(term_config)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(artifact_path, **arrays)

    quality = TargetQualityMetric(
        target=data.target,
        parent_conditions=len(set(data.validation_groups)),
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals**2))),
        interval_coverage_90=float(
            np.mean(np.abs(residuals) <= _Z90 * np.sqrt(evaluated_variance))
        ),
        interval_coverage_method=(
            "temporal-holdout-predictive-interval"
            if data.is_temporal_validation
            else "grouped-fold-predictive-interval"
        ),
        interval_coverage_observations=int(quality_rows.sum()),
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
    inference_identity = InferenceIdentity.create(
        policy=inference_policy("analytic-gaussian"),
        parameterization="penalized-spline-coefficient-posterior/v1",
        diagnostics=InferenceDiagnostics(status="not_applicable"),
        resource_limits={
            "coefficient_count": int(fitted.covariance.shape[0]),
        },
        convergence_criteria={
            "linear_solver": "positive-definite penalized precision",
        },
    )
    predictor = {
        "id": f"{data.target.lower()}-bayesian-additive",
        "target": data.target,
        "unit": data.unit,
        "target_kind": data.target_kind,
        "runtime_type": RUNTIME_TYPE,
        "architecture_id": "additive_terms_v1",
        "artifact": artifact_path.as_posix(),
        "predictive_family": "normal",
        "feature_names": list(data.feature_names),
        "inference_identity": inference_identity.model_dump(mode="json"),
        "config": {
            "link_id": "identity",
            "extrapolation": "constant_boundary",
            "terms": terms,
            "posterior": {
                "representation": "analytic_gaussian_coefficients_v1",
                "coefficient_order": "intercept_then_terms_in_config_order",
                "conditioning": "fixed_basis_fixed_smoothing_plugin_noise",
                "interval_level": 0.9,
            },
            "term_centering_policy": "training_cohort_mean_to_intercept",
            "capacity_policy_version": "bayesian-additive-capacity/v1",
            "training": standard_training_metadata(
                data,
                estimator_id=recipe.estimator_id,
                uncertainty=(
                    "conditional empirical-Bayes Gaussian posterior; "
                    "fixed basis and smoothing, plug-in observation noise"
                ),
                parameters=parameters,
            ),
        },
    }

    def predict(values: np.ndarray) -> float:
        mean, _ = fitted.predict(values.reshape(1, -1))
        return float(mean[0])

    diagnostics = dict(fitted.diagnostics)
    diagnostics.update({
        "estimator_id": recipe.estimator_id,
        "folds": data.folds,
        "cohort_digest": data.cohort_digest,
        "fold_digest": data.fold_digest,
        "evaluation": "outer-fold-refit",
        "interval_estimand": {
            "credible": "latent_mean",
            "predictive": "new_observation",
        },
    })
    return TrainedPredictor(
        predictor=predictor,
        artifact=artifact_path,
        quality=quality,
        diagnostics=diagnostics,
        predict=predict,
        evaluation_predictions=predictions,
    )
