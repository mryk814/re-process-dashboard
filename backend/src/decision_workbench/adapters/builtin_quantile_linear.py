"""Library-neutral fixed linear quantile-set inference."""
from __future__ import annotations

import numpy as np

from decision_workbench.modeling.packages.contracts import (
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
)
from decision_workbench.modeling.packages.ports import VerifiedPackageArtifacts
from .base import feature_vector
from .safe_npz import safe_npz_arrays


MAX_QUANTILES = 9


class _QuantileLinearPredictor:
    def __init__(self, spec: PredictorSpec, levels: np.ndarray, coefficients: np.ndarray, intercepts: np.ndarray) -> None:
        self.spec = spec
        self.levels = levels
        self.coefficients = coefficients
        self.intercepts = intercepts
        self.point_index = int(np.flatnonzero(np.isclose(levels, 0.5, rtol=0, atol=1e-12))[0])

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        del seed
        predictions = self.coefficients @ feature_vector(self.spec, values) + self.intercepts
        if np.any(np.diff(predictions) < 0):
            raise PackageContractError("quantile predictions cross for the requested input")
        quantiles = {format(float(level), ".12g"): float(value) for level, value in zip(self.levels, predictions)}
        return PredictiveSummary(
            target=self.spec.target,
            target_kind=self.spec.target_kind,
            unit=self.spec.unit,
            point_statistic="median",
            point_estimate=float(predictions[self.point_index]),
            quantiles=quantiles,
            distribution={"family": "empirical_quantiles", "support": "runtime_defined"},
        )


class BuiltinQuantileLinearAdapter:
    runtime_type = "builtin.quantile_linear.v1"

    def load(self, package: VerifiedPackageArtifacts, predictor: PredictorSpec) -> _QuantileLinearPredictor:
        if predictor.architecture_id != "quantile_linear_v1" or predictor.predictive_family != "empirical_quantiles":
            raise PackageContractError("builtin quantile linear requires quantile_linear_v1 empirical quantiles")
        arrays = safe_npz_arrays(package.artifact_path(predictor.artifact), max_entries=3)
        if set(arrays) != {"quantile_levels", "coefficients", "intercepts"}:
            raise PackageContractError("quantile linear artifact has an unexpected tensor schema")
        levels = np.asarray(arrays["quantile_levels"], dtype=float)
        coefficients = np.asarray(arrays["coefficients"], dtype=float)
        intercepts = np.asarray(arrays["intercepts"], dtype=float)
        if levels.ndim != 1 or not 2 <= len(levels) <= MAX_QUANTILES:
            raise PackageContractError("quantile levels must be a bounded one-dimensional set")
        if np.any(levels <= 0) or np.any(levels >= 1) or np.any(np.diff(levels) <= 0):
            raise PackageContractError("quantile levels must be unique, ordered, and inside (0, 1)")
        if not np.any(np.isclose(levels, 0.5, rtol=0, atol=1e-12)):
            raise PackageContractError("quantile linear artifact requires a median level")
        if coefficients.shape != (len(levels), len(predictor.feature_names)) or intercepts.shape != (len(levels),):
            raise PackageContractError("quantile linear tensors have incompatible shapes")
        if predictor.config.get("crossing_policy") != "reject":
            raise PackageContractError("quantile linear crossing_policy must be reject")
        return _QuantileLinearPredictor(predictor, levels, coefficients, intercepts)
