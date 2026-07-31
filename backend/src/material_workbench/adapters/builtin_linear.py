from __future__ import annotations

import numpy as np

from material_workbench.modeling.packages.contracts import (
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
)
from material_workbench.modeling.packages.ports import VerifiedPackageArtifacts
from .base import feature_vector
from .safe_npz import safe_npz_arrays


class _BuiltinLinearPredictor:
    def __init__(self, spec: PredictorSpec, weights: np.ndarray, bias: float, lower: float, upper: float) -> None:
        self.spec, self.weights, self.bias, self.lower, self.upper = spec, weights, bias, lower, upper

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        value = float(feature_vector(self.spec, values) @ self.weights + self.bias)
        return PredictiveSummary(target=self.spec.target, target_kind=self.spec.target_kind, unit=self.spec.unit, point_statistic="mean", point_estimate=value, quantiles={"0.05": value + self.lower, "0.50": value, "0.95": value + self.upper}, distribution={"family": "empirical_quantiles", "support": "real"})

    def sample(
        self,
        values: dict[str, float],
        *,
        sample_count: int,
        seed: int,
    ) -> np.ndarray:
        """Approximate residual draws from the declared empirical 5–95% span."""

        if sample_count < 2:
            raise PackageContractError("sample_count must be at least 2")
        mean = float(feature_vector(self.spec, values) @ self.weights + self.bias)
        sigma = float((self.upper - self.lower) / (2 * 1.6448536269514722))
        return mean + sigma * np.random.default_rng(seed).standard_normal(sample_count)


class BuiltinLinearAdapter:
    runtime_type = "builtin.linear.v1"

    def load(self, package: VerifiedPackageArtifacts, predictor: PredictorSpec) -> _BuiltinLinearPredictor:
        if predictor.predictive_family != "empirical_quantiles":
            raise PackageContractError("builtin.linear.v1 requires empirical_quantiles")
        arrays = safe_npz_arrays(package.artifact_path(predictor.artifact), max_entries=4)
        required = {"weights", "bias", "lower_offset", "upper_offset"}
        if set(arrays) != required:
            raise PackageContractError("builtin linear artifact has an unexpected tensor schema")
        weights = np.asarray(arrays["weights"], dtype=float).reshape(-1)
        if len(weights) != len(predictor.feature_names):
            raise PackageContractError("builtin linear weights do not match feature_names")
        bias, lower, upper = (float(np.asarray(arrays[name]).reshape(())) for name in ("bias", "lower_offset", "upper_offset"))
        if not np.isfinite(weights).all() or not np.isfinite([bias, lower, upper]).all() or lower > 0 or upper < 0:
            raise PackageContractError("invalid builtin linear parameters")
        return _BuiltinLinearPredictor(predictor, weights, bias, lower, upper)
