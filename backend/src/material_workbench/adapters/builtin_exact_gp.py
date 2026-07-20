"""Deterministic exact Gaussian-process regression backed by safe NumPy arrays."""
from __future__ import annotations

import numpy as np

from ..model_packages import PackageContractError, PredictiveSummary, PredictorSpec, VerifiedModelPackage
from .base import feature_vector, normal_predictive_summary


class _ExactGPPredictor:
    def __init__(self, spec: PredictorSpec, arrays: dict[str, np.ndarray]) -> None:
        self.spec = spec
        self.train_x = arrays["train_x"].astype(np.float64, copy=False)
        self.train_y = arrays["train_y"].astype(np.float64, copy=False).reshape(-1)
        self.feature_mean = arrays["feature_mean"].astype(np.float64, copy=False).reshape(-1)
        self.feature_scale = arrays["feature_scale"].astype(np.float64, copy=False).reshape(-1)
        self.lengthscale = arrays["lengthscale"].astype(np.float64, copy=False).reshape(-1)
        self.outputscale = float(arrays["outputscale"].reshape(()))
        self.train_noise = float(arrays["train_noise"].reshape(()))
        self.observation_noise = float(arrays["observation_noise"].reshape(()))
        self.mean = float(arrays["mean"].reshape(()))
        self.precision = arrays["precision"].astype(np.float64, copy=False)
        self.alpha = arrays["alpha"].astype(np.float64, copy=False).reshape(-1)

        feature_count = len(spec.feature_names)
        if (
            self.train_x.ndim != 2
            or self.train_x.shape != (len(self.train_y), feature_count)
            or any(len(item) != feature_count for item in (self.feature_mean, self.feature_scale, self.lengthscale))
            or len(self.train_y) < 2
            or self.precision.shape != (len(self.train_y), len(self.train_y))
            or self.alpha.shape != self.train_y.shape
            or not all(np.isfinite(item).all() for item in (self.train_x, self.train_y, self.feature_mean, self.feature_scale, self.lengthscale))
            or not np.isfinite([self.outputscale, self.train_noise, self.observation_noise, self.mean]).all()
            or (self.feature_scale <= 0).any()
            or (self.lengthscale <= 0).any()
            or self.outputscale <= 0
            or self.train_noise <= 0
            or self.observation_noise < 0
        ):
            raise PackageContractError("invalid built-in exact GP artifact schema")

        if not np.isfinite(self.precision).all() or not np.allclose(self.precision, self.precision.T, rtol=1e-7, atol=1e-9):
            raise PackageContractError("built-in exact GP precision matrix is invalid")

    def _kernel(self, left: np.ndarray, right: np.ndarray) -> np.ndarray:
        scaled = (left[:, None, :] - right[None, :, :]) / self.lengthscale
        return self.outputscale * np.exp(-0.5 * np.sum(scaled * scaled, axis=2))

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        del seed
        raw = feature_vector(self.spec, values)
        point = ((raw - self.feature_mean) / self.feature_scale).reshape(1, -1)
        cross = self._kernel(self.train_x, point)[:, 0]
        estimate = self.mean + float(cross @ self.alpha)
        model_variance = max(self.outputscale - float(cross @ self.precision @ cross), 0.0)
        return normal_predictive_summary(self.spec, estimate, model_variance, self.observation_noise)


class BuiltinExactGPAdapter:
    runtime_type = "builtin.exact_gp.v1"

    def load(self, package: VerifiedModelPackage, predictor: PredictorSpec) -> _ExactGPPredictor:
        if predictor.predictive_family != "normal" or predictor.architecture_id != "exact_rbf_grouped_v1":
            raise PackageContractError("builtin.exact_gp.v1 requires normal / exact_rbf_grouped_v1")
        try:
            with np.load(package.artifact_path(predictor.artifact), allow_pickle=False) as artifact:
                arrays = {name: artifact[name] for name in artifact.files}
        except (OSError, ValueError, KeyError) as exc:
            raise PackageContractError(f"invalid built-in exact GP artifact: {exc}") from exc
        required = {
            "train_x", "train_y", "feature_mean", "feature_scale", "lengthscale",
            "outputscale", "train_noise", "observation_noise", "mean", "precision", "alpha",
        }
        if set(arrays) != required:
            raise PackageContractError("built-in exact GP artifact has an unexpected array schema")
        return _ExactGPPredictor(predictor, arrays)
