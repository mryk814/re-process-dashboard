"""Pure NumPy posterior-predictive inference for exported linear draws."""
from __future__ import annotations

import math

import numpy as np

from material_workbench.modeling.model_package_contracts import (
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
)
from material_workbench.modeling.model_package_verification import VerifiedModelPackage
from .base import feature_vector, quantile_summary
from .safe_npz import safe_npz_arrays


MAX_POSTERIOR_DRAWS = 4096


class _PosteriorLinearPredictor:
    def __init__(
        self,
        spec: PredictorSpec,
        beta: np.ndarray,
        intercept: np.ndarray,
        noise_scale: np.ndarray,
        parent_scale: np.ndarray | None = None,
    ) -> None:
        self.spec, self.beta, self.intercept, self.noise_scale = spec, beta, intercept, noise_scale
        self.parent_scale = parent_scale

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        latent = self.beta @ feature_vector(self.spec, values) + self.intercept
        epistemic_std = float(np.std(latent))
        within_variance = self.noise_scale**2
        between_variance = np.zeros_like(within_variance) if self.parent_scale is None else self.parent_scale**2
        aleatoric_std = float(np.sqrt(np.mean(within_variance + between_variance)))
        if self.spec.predictive_family == "normal":
            total_std = math.sqrt(epistemic_std**2 + aleatoric_std**2)
            mean = float(np.mean(latent))
            z90 = 1.6448536269514722
            return PredictiveSummary(
                target=self.spec.target,
                target_kind=self.spec.target_kind,
                unit=self.spec.unit,
                point_statistic="mean",
                point_estimate=mean,
                quantiles={"0.05": mean - z90 * total_std, "0.50": mean, "0.95": mean + z90 * total_std},
                distribution={
                    "family": "normal",
                    "support": "real",
                    "mean": mean,
                    "std": total_std,
                    "approximation": "posterior_predictive_moment_matched",
                },
                uncertainty_components={
                    "epistemic_std": epistemic_std,
                    "between_parent_std": float(np.sqrt(np.mean(between_variance))),
                    "within_parent_observation_std": float(np.sqrt(np.mean(within_variance))),
                    "aleatoric_std": aleatoric_std,
                    "total_predictive_std": total_std,
                },
            )
        rng = np.random.default_rng(seed)
        samples = latent + np.sqrt(within_variance + between_variance) * rng.standard_normal(len(latent))
        total_std = float(np.std(samples))
        return PredictiveSummary(
            target=self.spec.target,
            target_kind=self.spec.target_kind,
            unit=self.spec.unit,
            point_statistic="mean",
            point_estimate=float(np.mean(latent)),
            quantiles=quantile_summary(samples),
            distribution={
                "family": "empirical_quantiles",
                "support": "real",
                "std": total_std,
                "std_semantics": "posterior_predictive_samples",
            },
            uncertainty_components={
                "epistemic_std": epistemic_std,
                "aleatoric_std": aleatoric_std,
            },
        )


class BuiltinPosteriorLinearAdapter:
    runtime_type = "builtin.posterior_linear.v1"

    def load(self, package: VerifiedModelPackage, predictor: PredictorSpec) -> _PosteriorLinearPredictor:
        if predictor.architecture_id not in {
            "posterior_linear_v1",
            "hierarchical_parent_random_intercept_v1",
        } or predictor.predictive_family not in {"empirical_quantiles", "normal"}:
            raise PackageContractError("posterior linear has an unsupported architecture or predictive family")
        if predictor.predictive_family == "normal" and predictor.config.get("output_representation") != "moment_matched_normal":
            raise PackageContractError("posterior linear normal output requires output_representation=moment_matched_normal")
        arrays = safe_npz_arrays(package.artifact_path(predictor.artifact), max_entries=6)
        required = {"beta_draws", "intercept_draws", "noise_scale_draws"}
        optional = {"indicator_draws", "local_scale_draws", "parent_scale_draws"}
        if not required <= set(arrays) or set(arrays) - required - optional:
            raise PackageContractError("posterior linear artifact has an unexpected tensor schema")
        beta = np.asarray(arrays["beta_draws"], dtype=float)
        intercept = np.asarray(arrays["intercept_draws"], dtype=float)
        noise_scale = np.asarray(arrays["noise_scale_draws"], dtype=float)
        draws = beta.shape[0] if beta.ndim == 2 else 0
        if not 2 <= draws <= MAX_POSTERIOR_DRAWS or beta.shape[1:] != (len(predictor.feature_names),):
            raise PackageContractError("posterior coefficient draw shape or count is invalid")
        if intercept.shape != (draws,) or noise_scale.shape != (draws,) or np.any(noise_scale <= 0):
            raise PackageContractError("posterior intercept or noise scale draws are invalid")
        parent_scale = None
        if predictor.architecture_id == "hierarchical_parent_random_intercept_v1":
            if "parent_scale_draws" not in arrays:
                raise PackageContractError("hierarchical posterior requires parent scale draws")
            parent_scale = np.asarray(arrays["parent_scale_draws"], dtype=float)
            if parent_scale.shape != (draws,) or np.any(parent_scale <= 0):
                raise PackageContractError("posterior parent scale draws are invalid")
        if "indicator_draws" in arrays:
            indicators = np.asarray(arrays["indicator_draws"], dtype=float)
            if indicators.shape != beta.shape or not np.isin(indicators, (0, 1)).all():
                raise PackageContractError("posterior indicator draws must be binary and match beta shape")
        if "local_scale_draws" in arrays:
            local_scales = np.asarray(arrays["local_scale_draws"], dtype=float)
            if local_scales.shape != beta.shape or np.any(local_scales <= 0):
                raise PackageContractError("posterior local scale draws must be positive and match beta shape")
        return _PosteriorLinearPredictor(predictor, beta, intercept, noise_scale, parent_scale)
