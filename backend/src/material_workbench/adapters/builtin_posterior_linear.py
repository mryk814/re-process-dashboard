"""Pure NumPy posterior-predictive inference for exported linear draws."""
from __future__ import annotations

import numpy as np

from ..model_packages import PackageContractError, PredictiveSummary, PredictorSpec, VerifiedModelPackage
from .base import feature_vector, quantile_summary
from .safe_npz import safe_npz_arrays


MAX_POSTERIOR_DRAWS = 4096


class _PosteriorLinearPredictor:
    def __init__(self, spec: PredictorSpec, beta: np.ndarray, intercept: np.ndarray, noise_scale: np.ndarray) -> None:
        self.spec, self.beta, self.intercept, self.noise_scale = spec, beta, intercept, noise_scale

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        latent = self.beta @ feature_vector(self.spec, values) + self.intercept
        rng = np.random.default_rng(seed)
        samples = latent + self.noise_scale * rng.standard_normal(len(latent))
        epistemic_std = float(np.std(latent))
        aleatoric_std = float(np.sqrt(np.mean(self.noise_scale**2)))
        total_std = float(np.std(samples))
        return PredictiveSummary(
            target=self.spec.target,
            target_kind=self.spec.target_kind,
            unit=self.spec.unit,
            point_statistic="mean",
            point_estimate=float(np.mean(samples)),
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
        if predictor.architecture_id != "posterior_linear_v1" or predictor.predictive_family != "empirical_quantiles":
            raise PackageContractError("posterior linear requires posterior_linear_v1 empirical quantiles")
        arrays = safe_npz_arrays(package.artifact_path(predictor.artifact), max_entries=5)
        required = {"beta_draws", "intercept_draws", "noise_scale_draws"}
        optional = {"indicator_draws", "local_scale_draws"}
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
        if "indicator_draws" in arrays:
            indicators = np.asarray(arrays["indicator_draws"], dtype=float)
            if indicators.shape != beta.shape or not np.isin(indicators, (0, 1)).all():
                raise PackageContractError("posterior indicator draws must be binary and match beta shape")
        if "local_scale_draws" in arrays:
            local_scales = np.asarray(arrays["local_scale_draws"], dtype=float)
            if local_scales.shape != beta.shape or np.any(local_scales <= 0):
                raise PackageContractError("posterior local scale draws must be positive and match beta shape")
        return _PosteriorLinearPredictor(predictor, beta, intercept, noise_scale)
