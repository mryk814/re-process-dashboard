"""Deterministic exact Gaussian-process regression backed by safe NumPy arrays."""
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


class _ExactGPPredictor:
    def __init__(self, spec: PredictorSpec, arrays: dict[str, np.ndarray]) -> None:
        self.spec = spec
        # For predictive_family=lognormal the stored GP is fit on log1p(target);
        # predictions are transformed back to the target unit at query time.
        self.log1p_latent = spec.predictive_family == "lognormal"
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
        predictive_variance = model_variance + self.observation_noise
        model_std = math.sqrt(model_variance)
        observation_std = math.sqrt(self.observation_noise)
        predictive_std = math.sqrt(predictive_variance)
        z90 = 1.6448536269514722
        uncertainty_components = {
            "latent_model_variance": model_variance,
            "latent_model_std": model_std,
            "observation_noise_variance": self.observation_noise,
            "observation_noise_std": observation_std,
            "total_predictive_variance": predictive_variance,
            "total_predictive_std": predictive_std,
        }
        if self.log1p_latent:
            # Shifted lognormal: latent = log1p(target). Quantiles transform
            # exactly; the physical support is clipped at zero.
            def back(latent: float) -> float:
                return max(math.expm1(latent), 0.0)

            mean = math.exp(estimate + predictive_variance / 2.0) - 1.0
            variance = (math.exp(predictive_variance) - 1.0) * math.exp(2.0 * estimate + predictive_variance)
            return PredictiveSummary(
                target=self.spec.target,
                target_kind=self.spec.target_kind,
                unit=self.spec.unit,
                point_statistic="median",
                point_estimate=back(estimate),
                quantiles={
                    "0.05": back(estimate - z90 * predictive_std),
                    "0.50": back(estimate),
                    "0.95": back(estimate + z90 * predictive_std),
                },
                distribution={
                    "family": "lognormal",
                    "support": "nonnegative",
                    "log_mean": estimate,
                    "log_std": predictive_std,
                    "shift": -1.0,
                    "mean": max(mean, 0.0),
                    "std": math.sqrt(max(variance, 0.0)),
                },
                uncertainty_components=uncertainty_components,
            )
        return PredictiveSummary(
            target=self.spec.target,
            target_kind=self.spec.target_kind,
            unit=self.spec.unit,
            point_statistic="mean",
            point_estimate=estimate,
            quantiles={
                "0.05": estimate - z90 * predictive_std,
                "0.50": estimate,
                "0.95": estimate + z90 * predictive_std,
            },
            distribution={"family": "normal", "support": "real", "mean": estimate, "std": predictive_std},
            uncertainty_components=uncertainty_components,
        )


class BuiltinExactGPAdapter:
    runtime_type = "builtin.exact_gp.v1"

    def load(self, package: VerifiedPackageArtifacts, predictor: PredictorSpec) -> _ExactGPPredictor:
        if (
            predictor.predictive_family not in {"normal", "lognormal"}
            or predictor.architecture_id not in {"exact_rbf_grouped_v1", "exact_rbf_ard_v1"}
        ):
            raise PackageContractError(
                "builtin.exact_gp.v1 requires normal or lognormal / "
                "exact_rbf_grouped_v1 or exact_rbf_ard_v1"
            )
        if predictor.predictive_family == "lognormal" and predictor.config.get("latent_transform") != "log1p":
            raise PackageContractError("builtin.exact_gp.v1 lognormal requires config.latent_transform=log1p")
        if predictor.predictive_family == "lognormal" and predictor.target_kind != "continuous_positive":
            raise PackageContractError("builtin.exact_gp.v1 lognormal requires target_kind=continuous_positive")
        arrays = safe_npz_arrays(package.artifact_path(predictor.artifact), max_entries=11)
        required = {
            "train_x", "train_y", "feature_mean", "feature_scale", "lengthscale",
            "outputscale", "train_noise", "observation_noise", "mean", "precision", "alpha",
        }
        if set(arrays) != required:
            raise PackageContractError("built-in exact GP artifact has an unexpected array schema")
        return _ExactGPPredictor(predictor, arrays)
