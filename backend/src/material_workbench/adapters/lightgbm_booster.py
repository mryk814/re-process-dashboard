from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from material_workbench.modeling.model_packages import (
    MissingOptionalDependency,
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
    VerifiedModelPackage,
)
from .base import feature_vector

try:
    import lightgbm
except ModuleNotFoundError:  # Optional runtime profile.
    lightgbm = None


class _LightGBMPredictor:
    def __init__(self, spec: PredictorSpec, booster: object) -> None:
        self.spec, self.booster = spec, booster
        residual_std = spec.config.get("residual_std")
        self.residual_std = (
            float(residual_std)
            if isinstance(residual_std, (int, float)) and math.isfinite(float(residual_std))
            else None
        )
        if spec.predictive_family == "normal" and (
            self.residual_std is None or self.residual_std <= 0
        ):
            raise PackageContractError(
                "normal LightGBM predictors require a positive finite residual_std"
            )

    def _summary(self, value: float) -> PredictiveSummary:
        if self.spec.predictive_family == "bernoulli_logit":
            calibration = self.spec.config.get("calibration", {})
            if isinstance(calibration, dict):
                slope = float(calibration.get("slope", 1.0))
                intercept = float(calibration.get("intercept", 0.0))
                clipped = min(max(value, 1e-7), 1 - 1e-7)
                logit = math.log(clipped / (1 - clipped))
                value = 1 / (1 + math.exp(-(intercept + slope * logit)))
            value = min(max(value, 0.0), 1.0)
            return PredictiveSummary(
                target=self.spec.target,
                target_kind="binary",
                unit=self.spec.unit,
                point_statistic="probability",
                point_estimate=value,
                event_probability=value,
                distribution={
                    "family": "bernoulli_logit",
                    "support": "{0,1}",
                    "event": "fail",
                    "probability": value,
                },
            )
        if self.spec.predictive_family == "normal":
            assert self.residual_std is not None
            z90 = 1.6448536269514722
            variance = self.residual_std * self.residual_std
            return PredictiveSummary(
                target=self.spec.target,
                target_kind=self.spec.target_kind,
                unit=self.spec.unit,
                point_statistic="mean",
                point_estimate=value,
                quantiles={
                    "0.05": value - z90 * self.residual_std,
                    "0.50": value,
                    "0.95": value + z90 * self.residual_std,
                },
                distribution={
                    "family": "normal",
                    "support": "real",
                    "mean": value,
                    "std": self.residual_std,
                },
                uncertainty_components={
                    "latent_model_variance": 0.0,
                    "latent_model_std": 0.0,
                    "observation_noise_variance": variance,
                    "observation_noise_std": self.residual_std,
                    "total_predictive_variance": variance,
                    "total_predictive_std": self.residual_std,
                },
            )
        return PredictiveSummary(
            target=self.spec.target,
            target_kind=self.spec.target_kind,
            unit=self.spec.unit,
            point_statistic="mean",
            point_estimate=value,
            quantiles={"0.50": value},
            distribution={"family": "empirical_quantiles", "support": "runtime_defined"},
        )

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        del seed
        value = float(self.booster.predict(feature_vector(self.spec, values).reshape(1, -1))[0])  # type: ignore[attr-defined]
        return self._summary(value)

    def predict_batch(
        self,
        values: Sequence[dict[str, float]],
        *,
        seed: int = 0,
    ) -> list[PredictiveSummary]:
        del seed
        if not values:
            return []
        matrix = np.vstack([feature_vector(self.spec, item) for item in values])
        estimates = self.booster.predict(matrix)  # type: ignore[attr-defined]
        return [self._summary(float(value)) for value in estimates]


class LightGBMBoosterAdapter:
    runtime_type = "lightgbm.booster.v1"

    def load(self, package: VerifiedModelPackage, predictor: PredictorSpec) -> _LightGBMPredictor:
        if lightgbm is None:
            raise MissingOptionalDependency("install runtime-lightgbm to load lightgbm.booster.v1")
        if predictor.predictive_family not in {"normal", "empirical_quantiles", "bernoulli_logit"}:
            raise PackageContractError(
                "lightgbm.booster.v1 requires normal, empirical_quantiles, or bernoulli_logit"
            )
        return _LightGBMPredictor(predictor, lightgbm.Booster(model_file=str(package.artifact_path(predictor.artifact))))
