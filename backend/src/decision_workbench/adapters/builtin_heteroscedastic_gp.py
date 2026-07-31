"""Two-stage exact GP using repeated-observation sufficient statistics."""
from __future__ import annotations

import math

import numpy as np

from decision_workbench.modeling.packages.contracts import (
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
)
from decision_workbench.modeling.packages.ports import VerifiedPackageArtifacts
from .base import feature_vector
from .safe_npz import safe_npz_arrays


class _HeteroscedasticExactGPPredictor:
    def __init__(self, spec: PredictorSpec, arrays: dict[str, np.ndarray]) -> None:
        self.spec = spec
        self.train_x = arrays["train_x"].astype(np.float64, copy=False)
        self.feature_mean = arrays["feature_mean"].astype(np.float64, copy=False).reshape(-1)
        self.feature_scale = arrays["feature_scale"].astype(np.float64, copy=False).reshape(-1)
        self.mean_lengthscale = arrays["mean_lengthscale"].astype(np.float64, copy=False).reshape(-1)
        self.mean_outputscale = float(arrays["mean_outputscale"].reshape(()))
        self.mean_value = float(arrays["mean_value"].reshape(()))
        self.mean_precision = arrays["mean_precision"].astype(np.float64, copy=False)
        self.mean_alpha = arrays["mean_alpha"].astype(np.float64, copy=False).reshape(-1)
        self.noise_lengthscale = arrays["noise_lengthscale"].astype(np.float64, copy=False).reshape(-1)
        self.noise_mean = float(arrays["noise_mean"].reshape(()))
        self.noise_alpha = arrays["noise_alpha"].astype(np.float64, copy=False).reshape(-1)
        self.noise_floor = float(arrays["noise_floor"].reshape(()))
        self.noise_ceiling = float(arrays["noise_ceiling"].reshape(()))

        feature_count = len(spec.feature_names)
        parent_count = len(self.train_x)
        vectors = (
            self.feature_mean,
            self.feature_scale,
            self.mean_lengthscale,
            self.noise_lengthscale,
        )
        if (
            self.train_x.ndim != 2
            or self.train_x.shape[1] != feature_count
            or parent_count < 2
            or any(item.shape != (feature_count,) for item in vectors)
            or self.mean_precision.shape != (parent_count, parent_count)
            or self.mean_alpha.shape != (parent_count,)
            or self.noise_alpha.shape != (parent_count,)
            or not all(np.isfinite(item).all() for item in (
                self.train_x, *vectors, self.mean_precision, self.mean_alpha, self.noise_alpha,
            ))
            or not np.isfinite([
                self.mean_outputscale, self.mean_value, self.noise_mean,
                self.noise_floor, self.noise_ceiling,
            ]).all()
            or np.any(self.feature_scale <= 0)
            or np.any(self.mean_lengthscale <= 0)
            or np.any(self.noise_lengthscale <= 0)
            or self.mean_outputscale <= 0
            or not 0 < self.noise_floor <= self.noise_ceiling
            or not np.allclose(self.mean_precision, self.mean_precision.T, rtol=1e-7, atol=1e-9)
        ):
            raise PackageContractError("invalid heteroscedastic exact GP artifact schema")

    @staticmethod
    def _cross(train_x: np.ndarray, point: np.ndarray, lengthscale: np.ndarray) -> np.ndarray:
        scaled = (train_x - point) / lengthscale
        return np.exp(-0.5 * np.sum(scaled * scaled, axis=1))

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        del seed
        raw = feature_vector(self.spec, values)
        point = (raw - self.feature_mean) / self.feature_scale
        mean_cross_base = self._cross(self.train_x, point, self.mean_lengthscale)
        mean_cross = self.mean_outputscale * mean_cross_base
        estimate = self.mean_value + float(mean_cross @ self.mean_alpha)
        latent_variance = max(
            self.mean_outputscale - float(mean_cross @ self.mean_precision @ mean_cross),
            0.0,
        )
        noise_cross = self._cross(self.train_x, point, self.noise_lengthscale)
        log_noise = self.noise_mean + float(noise_cross @ self.noise_alpha)
        observation_variance = min(max(math.exp(log_noise), self.noise_floor), self.noise_ceiling)
        total_variance = latent_variance + observation_variance
        total_std = math.sqrt(total_variance)
        z90 = 1.6448536269514722
        return PredictiveSummary(
            target=self.spec.target,
            target_kind=self.spec.target_kind,
            unit=self.spec.unit,
            point_statistic="mean",
            point_estimate=estimate,
            quantiles={
                "0.05": estimate - z90 * total_std,
                "0.50": estimate,
                "0.95": estimate + z90 * total_std,
            },
            distribution={"family": "normal", "support": "real", "mean": estimate, "std": total_std},
            uncertainty_components={
                "latent_model_variance": latent_variance,
                "input_dependent_observation_variance": observation_variance,
                "total_predictive_variance": total_variance,
            },
        )


class BuiltinHeteroscedasticExactGPAdapter:
    runtime_type = "builtin.heteroscedastic_exact_gp.v1"

    def load(
        self,
        package: VerifiedPackageArtifacts,
        predictor: PredictorSpec,
    ) -> _HeteroscedasticExactGPPredictor:
        if (
            predictor.architecture_id != "heteroscedastic_rbf_individual_v1"
            or predictor.predictive_family != "normal"
        ):
            raise PackageContractError(
                "heteroscedastic exact GP requires heteroscedastic_rbf_individual_v1 / normal"
            )
        arrays = safe_npz_arrays(package.artifact_path(predictor.artifact), max_entries=14)
        required = {
            "train_x", "feature_mean", "feature_scale",
            "mean_lengthscale", "mean_outputscale", "mean_value", "mean_precision", "mean_alpha",
            "noise_lengthscale", "noise_mean", "noise_alpha", "noise_floor", "noise_ceiling",
        }
        if set(arrays) != required:
            raise PackageContractError("heteroscedastic exact GP artifact has an unexpected array schema")
        return _HeteroscedasticExactGPPredictor(predictor, arrays)
