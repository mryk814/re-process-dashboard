from __future__ import annotations

from ..model_packages import MissingOptionalDependency, PredictiveSummary, PredictorSpec, VerifiedModelPackage
from .base import feature_vector

try:
    import lightgbm
except ModuleNotFoundError:  # Optional runtime profile.
    lightgbm = None


class _LightGBMPredictor:
    def __init__(self, spec: PredictorSpec, booster: object) -> None:
        self.spec, self.booster = spec, booster

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        value = float(self.booster.predict(feature_vector(self.spec, values).reshape(1, -1))[0])  # type: ignore[attr-defined]
        return PredictiveSummary(target=self.spec.target, target_kind=self.spec.target_kind, unit=self.spec.unit, point_statistic="mean", point_estimate=value, quantiles={"0.50": value}, distribution={"family": "empirical_quantiles", "support": "runtime_defined"})


class LightGBMBoosterAdapter:
    runtime_type = "lightgbm.booster.v1"

    def load(self, package: VerifiedModelPackage, predictor: PredictorSpec) -> _LightGBMPredictor:
        if lightgbm is None:
            raise MissingOptionalDependency("install runtime-lightgbm to load lightgbm.booster.v1")
        return _LightGBMPredictor(predictor, lightgbm.Booster(model_file=str(package.artifact_path(predictor.artifact))))
