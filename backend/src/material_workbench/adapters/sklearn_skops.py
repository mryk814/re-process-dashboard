from __future__ import annotations

from material_workbench.modeling.model_packages import MissingOptionalDependency, PackageContractError, PredictiveSummary, PredictorSpec, VerifiedModelPackage
from .base import feature_vector

try:
    import skops.io as skops_io
except ModuleNotFoundError:  # Optional runtime profile.
    skops_io = None


# A package may select a supported family, but it can never nominate arbitrary
# importable Python types as trusted.  Keep this list small and expand it only
# with a fixture-backed adapter change.
_TRUSTED_TYPES_BY_FAMILY = {
    "linear_regression_v1": frozenset({
        "sklearn.linear_model._base.LinearRegression",
        "numpy.dtype",
        "numpy.dtypes.Float64DType",
    }),
    "ridge_regression_v1": frozenset({
        "sklearn.linear_model._ridge.Ridge",
        "numpy.dtype",
        "numpy.dtypes.Float64DType",
    }),
}


class _SkopsPredictor:
    def __init__(self, spec: PredictorSpec, model: object) -> None:
        self.spec, self.model = spec, model

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        prediction = float(self.model.predict(feature_vector(self.spec, values).reshape(1, -1))[0])  # type: ignore[attr-defined]
        return PredictiveSummary(target=self.spec.target, target_kind=self.spec.target_kind, unit=self.spec.unit, point_statistic="mean", point_estimate=prediction, quantiles={"0.50": prediction}, distribution={"family": "empirical_quantiles", "support": "runtime_defined"})


class SklearnSkopsAdapter:
    runtime_type = "sklearn.skops.v1"

    def load(self, package: VerifiedModelPackage, predictor: PredictorSpec) -> _SkopsPredictor:
        if skops_io is None:
            raise MissingOptionalDependency("install runtime-sklearn to load sklearn.skops.v1")
        family = predictor.config.get("estimator_family")
        if not isinstance(family, str) or family not in _TRUSTED_TYPES_BY_FAMILY:
            raise PackageContractError("sklearn package must declare a supported estimator_family")
        allowed = _TRUSTED_TYPES_BY_FAMILY[family]
        artifact = package.artifact_path(predictor.artifact)
        untrusted = set(skops_io.get_untrusted_types(file=artifact))
        if not untrusted.issubset(allowed):
            raise PackageContractError("skops artifact contains types outside the application allow-list")
        return _SkopsPredictor(predictor, skops_io.load(artifact, trusted=sorted(allowed)))
