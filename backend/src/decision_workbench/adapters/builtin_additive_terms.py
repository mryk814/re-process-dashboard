"""Library-neutral additive terms with typed local contribution explanations."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from decision_workbench.modeling.packages.contracts import (
    AdditiveExplanation,
    LatentMeanCredibleInterval,
    PackageContractError,
    PredictionInterval,
    PredictiveSummary,
    PredictorSpec,
    TermContribution,
)
from decision_workbench.modeling.packages.ports import VerifiedPackageArtifacts
from .base import feature_vector
from .safe_npz import safe_npz_arrays


_TERM_KINDS = {"linear", "bspline_univariate", "categorical_lookup"}
_NORMAL_90_Z = 1.6448536269514722


def bspline_basis(value: float, knots: np.ndarray, degree: int) -> np.ndarray:
    """Evaluate a clamped B-spline basis with constant boundary extrapolation."""

    lower, upper = float(knots[degree]), float(knots[-degree - 1])
    x = min(max(float(value), lower), upper)
    basis = np.zeros(len(knots) - 1, dtype=float)
    for index in range(len(basis)):
        if knots[index] <= x < knots[index + 1] or (x == upper and knots[index] < x <= knots[index + 1]):
            basis[index] = 1.0
    for order in range(1, degree + 1):
        next_basis = np.zeros(len(basis) - 1, dtype=float)
        for index in range(len(next_basis)):
            left_denominator = knots[index + order] - knots[index]
            right_denominator = knots[index + order + 1] - knots[index + 1]
            left = 0.0 if left_denominator == 0 else (x - knots[index]) / left_denominator * basis[index]
            right = 0.0 if right_denominator == 0 else (knots[index + order + 1] - x) / right_denominator * basis[index + 1]
            next_basis[index] = left + right
        basis = next_basis
    return basis


@dataclass(frozen=True)
class _Term:
    id: str
    kind: Literal["linear", "bspline_univariate", "categorical_lookup"]
    feature_index: int
    arrays: tuple[np.ndarray, ...]
    degree: int | None = None
    center: np.ndarray | None = None

    def basis(self, value: float) -> np.ndarray:
        if self.kind == "linear":
            raw = np.asarray([value], dtype=float)
            return raw if self.center is None else raw - self.center
        if self.kind == "bspline_univariate":
            knots, _ = self.arrays
            assert self.degree is not None
            raw = bspline_basis(value, knots, self.degree)
            return raw if self.center is None else raw - self.center
        categories, _ = self.arrays
        matches = np.flatnonzero(np.isclose(categories, value, rtol=0, atol=1e-12))
        if len(matches) != 1:
            raise PackageContractError(f"unknown category value for additive term {self.id!r}")
        basis = np.zeros(len(categories), dtype=float)
        basis[int(matches[0])] = 1.0
        return basis if self.center is None else basis - self.center

    def evaluate(self, value: float) -> float:
        coefficients = self.arrays[-1]
        return float(self.basis(value) @ coefficients)


class _AdditivePredictor:
    def __init__(
        self,
        spec: PredictorSpec,
        intercept: float,
        terms: tuple[_Term, ...],
        residual_scale: float | None,
        posterior_covariance: np.ndarray | None,
    ) -> None:
        self.spec = spec
        self.intercept = intercept
        self.terms = terms
        self.residual_scale = residual_scale
        self.posterior_covariance = posterior_covariance

    def _posterior_design(self, vector: np.ndarray) -> np.ndarray:
        return np.concatenate((
            np.ones(1, dtype=float),
            *(
                term.basis(float(vector[term.feature_index]))
                for term in self.terms
            ),
        ))

    def explain(self, values: dict[str, float]) -> AdditiveExplanation:
        vector = feature_vector(self.spec, values)
        contributions = tuple(
            TermContribution(
                term_id=term.id,
                kind=term.kind,
                feature_names=(self.spec.feature_names[term.feature_index],),
                contribution=term.evaluate(float(vector[term.feature_index])),
            )
            for term in self.terms
        )
        score = self.intercept + sum(term.contribution for term in contributions)
        return AdditiveExplanation(
            target=self.spec.target,
            link_id="identity",
            intercept=self.intercept,
            terms=contributions,
            link_score=score,
            prediction=score,
        )

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        del seed
        score = self.explain(values).prediction
        if self.residual_scale is None:
            return PredictiveSummary(
                target=self.spec.target,
                target_kind=self.spec.target_kind,
                unit=self.spec.unit,
                point_statistic="mean",
                point_estimate=score,
                distribution={"family": "empirical_quantiles", "support": "real"},
            )
        predictive_scale = self.residual_scale
        uncertainty_components = None
        prediction_interval = None
        credible_interval = None
        if self.posterior_covariance is not None:
            vector = feature_vector(self.spec, values)
            design = self._posterior_design(vector)
            latent_variance = max(
                float(design @ self.posterior_covariance @ design),
                0.0,
            )
            observation_variance = self.residual_scale**2
            predictive_scale = float(
                np.sqrt(latent_variance + observation_variance)
            )
            uncertainty_components = {
                "latent_mean_standard_deviation": float(
                    np.sqrt(latent_variance)
                ),
                "observation_standard_deviation": self.residual_scale,
                "predictive_standard_deviation": predictive_scale,
            }
            prediction_interval = PredictionInterval(
                method="bayesian",
                coverage_level=0.9,
                lower=score - _NORMAL_90_Z * predictive_scale,
                upper=score + _NORMAL_90_Z * predictive_scale,
            )
            latent_scale = float(np.sqrt(latent_variance))
            credible_interval = LatentMeanCredibleInterval(
                coverage_level=0.9,
                lower=score - _NORMAL_90_Z * latent_scale,
                upper=score + _NORMAL_90_Z * latent_scale,
            )
        spread = _NORMAL_90_Z * predictive_scale
        return PredictiveSummary(
            target=self.spec.target,
            target_kind=self.spec.target_kind,
            unit=self.spec.unit,
            point_statistic="mean",
            point_estimate=score,
            quantiles={"0.05": score - spread, "0.5": score, "0.95": score + spread},
            distribution={
                "family": "normal",
                "support": "real",
                "std": predictive_scale,
            },
            uncertainty_components=uncertainty_components,
            latent_mean_credible_interval=credible_interval,
            prediction_interval=prediction_interval,
        )


class BuiltinAdditiveTermsAdapter:
    runtime_type = "builtin.additive_terms.v1"

    def load(self, package: VerifiedPackageArtifacts, predictor: PredictorSpec) -> _AdditivePredictor:
        if predictor.architecture_id != "additive_terms_v1" or predictor.predictive_family not in {"empirical_quantiles", "normal"}:
            raise PackageContractError("builtin additive terms requires additive_terms_v1 and a supported family")
        if predictor.config.get("link_id") != "identity" or predictor.config.get("extrapolation") != "constant_boundary":
            raise PackageContractError("additive example requires identity link and constant boundary extrapolation")
        raw_terms = predictor.config.get("terms")
        if not isinstance(raw_terms, list) or not raw_terms:
            raise PackageContractError("additive terms config must be a nonempty list")
        arrays = safe_npz_arrays(package.artifact_path(predictor.artifact), max_entries=160)
        posterior_config = predictor.config.get("posterior")
        expected_keys = {"intercept"}
        terms: list[_Term] = []
        ids: set[str] = set()
        for index, raw in enumerate(raw_terms):
            if not isinstance(raw, dict) or set(raw) - {"id", "kind", "feature_index", "degree"}:
                raise PackageContractError("additive term config has an unexpected field")
            term_id, kind, feature_index = raw.get("id"), raw.get("kind"), raw.get("feature_index")
            if not isinstance(term_id, str) or not term_id or term_id in ids or kind not in _TERM_KINDS or not isinstance(feature_index, int) or not 0 <= feature_index < len(predictor.feature_names):
                raise PackageContractError("additive term identity, kind, or feature index is invalid")
            ids.add(term_id)
            prefix = f"term_{index}"
            if kind == "linear":
                keys = (f"{prefix}_coefficients",)
                degree = None
            elif kind == "bspline_univariate":
                keys = (f"{prefix}_knots", f"{prefix}_coefficients")
                degree = raw.get("degree")
                if not isinstance(degree, int) or not 1 <= degree <= 3:
                    raise PackageContractError("B-spline degree must be between 1 and 3")
            else:
                keys = (f"{prefix}_categories", f"{prefix}_scores")
                degree = None
            expected_keys.update(keys)
            try:
                term_arrays = tuple(np.asarray(arrays[key], dtype=float) for key in keys)
            except KeyError as exc:
                raise PackageContractError("additive artifact is missing a term tensor") from exc
            if kind == "linear" and term_arrays[0].shape != (1,):
                raise PackageContractError("linear additive coefficient must be scalar")
            if kind == "bspline_univariate":
                knots, coefficients = term_arrays
                assert degree is not None
                if knots.ndim != 1 or coefficients.shape != (len(knots) - degree - 1,) or len(knots) < 2 * degree + 2 or np.any(np.diff(knots) < 0) or knots[degree] >= knots[-degree - 1]:
                    raise PackageContractError("B-spline knots and coefficients have incompatible shapes")
            if kind == "categorical_lookup":
                categories, scores = term_arrays
                if categories.ndim != 1 or scores.shape != categories.shape or not len(categories) or len(np.unique(categories)) != len(categories):
                    raise PackageContractError("categorical lookup arrays are invalid")
            center = None
            if posterior_config is not None:
                center_key = f"{prefix}_center"
                expected_keys.add(center_key)
                try:
                    center = np.asarray(arrays[center_key], dtype=float)
                except KeyError as exc:
                    raise PackageContractError(
                        "Bayesian additive artifact requires term centers"
                    ) from exc
                if center.shape != term_arrays[-1].shape:
                    raise PackageContractError(
                        "Bayesian additive term center shape is invalid"
                    )
            terms.append(
                _Term(
                    term_id,
                    kind,
                    feature_index,
                    term_arrays,
                    degree,
                    center,
                )
            )
        residual_scale = None
        posterior_covariance = None
        if predictor.predictive_family == "normal":
            posterior = posterior_config
            if posterior is not None:
                if posterior != {
                    "representation": "analytic_gaussian_coefficients_v1",
                    "coefficient_order": "intercept_then_terms_in_config_order",
                    "conditioning": "fixed_basis_fixed_smoothing_plugin_noise",
                    "interval_level": 0.9,
                }:
                    raise PackageContractError(
                        "Bayesian additive posterior identity is invalid"
                    )
                expected_keys.update({
                    "posterior_covariance",
                    "observation_noise_variance",
                })
                try:
                    posterior_covariance = np.asarray(
                        arrays["posterior_covariance"],
                        dtype=float,
                    )
                    observation_variance = np.asarray(
                        arrays["observation_noise_variance"],
                        dtype=float,
                    )
                except KeyError as exc:
                    raise PackageContractError(
                        "Bayesian additive artifact requires posterior tensors"
                    ) from exc
                if (
                    observation_variance.shape not in {(), (1,)}
                    or float(observation_variance.reshape(-1)[0]) <= 0
                ):
                    raise PackageContractError(
                        "Bayesian additive observation variance must be positive scalar"
                    )
                residual_scale = float(
                    np.sqrt(observation_variance.reshape(-1)[0])
                )
                coefficient_count = 1 + sum(
                    len(term.arrays[-1]) for term in terms
                )
                if (
                    posterior_covariance.shape
                    != (coefficient_count, coefficient_count)
                    or not np.allclose(
                        posterior_covariance,
                        posterior_covariance.T,
                        rtol=1e-10,
                        atol=1e-12,
                    )
                    or float(
                        np.min(np.linalg.eigvalsh(posterior_covariance))
                    )
                    < -1e-10
                ):
                    raise PackageContractError(
                        "Bayesian additive posterior covariance is invalid"
                    )
            else:
                expected_keys.add("residual_scale")
                if "residual_scale" not in arrays:
                    raise PackageContractError(
                        "normal additive artifact requires residual_scale"
                    )
                scale = np.asarray(arrays["residual_scale"], dtype=float)
                if (
                    scale.shape not in {(), (1,)}
                    or float(scale.reshape(-1)[0]) <= 0
                ):
                    raise PackageContractError(
                        "normal additive residual_scale must be positive scalar"
                    )
                residual_scale = float(scale.reshape(-1)[0])
        if set(arrays) != expected_keys:
            raise PackageContractError("additive artifact has an unexpected tensor schema")
        intercept = np.asarray(arrays["intercept"], dtype=float)
        if intercept.shape not in {(), (1,)}:
            raise PackageContractError("additive intercept must be scalar")
        return _AdditivePredictor(
            predictor,
            float(intercept.reshape(-1)[0]),
            tuple(terms),
            residual_scale,
            posterior_covariance,
        )
