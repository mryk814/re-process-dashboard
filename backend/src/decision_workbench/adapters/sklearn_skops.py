from __future__ import annotations

import math

from scipy.stats import poisson

from decision_workbench.modeling.packages.contracts import (
    MissingOptionalDependency,
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
)
from decision_workbench.modeling.packages.ports import VerifiedPackageArtifacts
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
    "logistic_regression_v1": frozenset({
        "sklearn.linear_model._logistic.LogisticRegression",
        "numpy.dtype",
        "numpy.dtypes.Float64DType",
    }),
    "poisson_regression_v1": frozenset({
        "sklearn.linear_model._glm.glm.PoissonRegressor",
        "numpy.dtype",
        "numpy.dtypes.Float64DType",
    }),
}
_EXPECTED_CLASS_BY_FAMILY = {
    "linear_regression_v1": "sklearn.linear_model._base.LinearRegression",
    "ridge_regression_v1": "sklearn.linear_model._ridge.Ridge",
    "logistic_regression_v1": "sklearn.linear_model._logistic.LogisticRegression",
    "poisson_regression_v1": "sklearn.linear_model._glm.glm.PoissonRegressor",
}


class _SkopsPredictor:
    def __init__(self, spec: PredictorSpec, model: object) -> None:
        self.spec, self.model = spec, model

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        vector = feature_vector(self.spec, values).reshape(1, -1)
        family = self.spec.config["estimator_family"]
        if family == "logistic_regression_v1":
            probability = float(self.model.predict_proba(vector)[0, 1])  # type: ignore[attr-defined]
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise PackageContractError(
                    "logistic regression returned an invalid probability"
                )
            return PredictiveSummary(
                target=self.spec.target,
                target_kind="binary",
                unit=self.spec.unit,
                point_statistic="probability",
                point_estimate=probability,
                event_probability=probability,
                distribution={
                    "family": "bernoulli_logit",
                    "support": "{0,1}",
                },
            )
        prediction = float(self.model.predict(vector)[0])  # type: ignore[attr-defined]
        if family == "poisson_regression_v1":
            if not math.isfinite(prediction) or prediction < 0:
                raise PackageContractError(
                    "Poisson regression returned an invalid nonnegative rate"
                )
            quantiles = {
                level: float(poisson.ppf(probability, prediction))
                for level, probability in (
                    ("0.05", 0.05),
                    ("0.50", 0.50),
                    ("0.95", 0.95),
                )
            }
            return PredictiveSummary(
                target=self.spec.target,
                target_kind="count",
                unit=self.spec.unit,
                point_statistic="rate",
                point_estimate=prediction,
                quantiles=quantiles,
                distribution={
                    "family": "poisson_log",
                    "support": "nonnegative_integers",
                    "rate": prediction,
                },
            )
        return PredictiveSummary(target=self.spec.target, target_kind=self.spec.target_kind, unit=self.spec.unit, point_statistic="mean", point_estimate=prediction, quantiles={"0.50": prediction}, distribution={"family": "empirical_quantiles", "support": "runtime_defined"})


class SklearnSkopsAdapter:
    runtime_type = "sklearn.skops.v1"

    def load(self, package: VerifiedPackageArtifacts, predictor: PredictorSpec) -> _SkopsPredictor:
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
        model = skops_io.load(artifact, trusted=sorted(allowed))
        actual_type = f"{type(model).__module__}.{type(model).__name__}"
        if actual_type != _EXPECTED_CLASS_BY_FAMILY[family]:
            raise PackageContractError(
                "skops artifact estimator class does not match estimator_family"
            )
        return _SkopsPredictor(predictor, model)
