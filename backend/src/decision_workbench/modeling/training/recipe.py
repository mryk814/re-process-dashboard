from __future__ import annotations

from importlib.util import find_spec
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from decision_workbench.contracts.task_contracts import ContractModel, RuntimeCapability
from decision_workbench.modeling.training.validation_plan import ValidationPlan


class RidgeEstimatorRecipe(ContractModel):
    estimator_id: Literal["ridge.v1"] = "ridge.v1"
    alpha: Annotated[float, Field(gt=0, le=1_000_000)] = 1.0
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


class ExactGPEstimatorRecipe(ContractModel):
    estimator_id: Literal["exact-gp-rbf.v1"] = "exact-gp-rbf.v1"
    restarts: Annotated[int, Field(ge=1, le=12)] = 3
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    max_rows: Annotated[int, Field(ge=3, le=2_000)] = 500
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


class BayesianAdditiveSplineEstimatorRecipe(ContractModel):
    estimator_id: Literal["bayesian-additive-spline.v1"] = (
        "bayesian-additive-spline.v1"
    )
    spline_degree: Annotated[int, Field(ge=1, le=3)] = 2
    max_basis_per_feature: Annotated[int, Field(ge=4, le=12)] = 6
    min_unique_values_for_smooth: Annotated[int, Field(ge=4, le=50)] = 6
    smoothness_precision: Annotated[float, Field(gt=0, le=1_000_000)] = 10.0
    linear_precision: Annotated[float, Field(gt=0, le=1_000_000)] = 1.0
    intercept_precision: Annotated[float, Field(ge=0, le=1_000_000)] = 1e-8
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    knot_policy: Literal["training_quantiles"] = "training_quantiles"
    smoothing_policy: Literal["fixed_gaussian_prior"] = "fixed_gaussian_prior"
    noise_policy: Literal["plugin_training_residual"] = "plugin_training_residual"
    categorical_term_policy: Literal[
        "feature_recipe_encoded_centered_linear_terms"
    ] = "feature_recipe_encoded_centered_linear_terms"
    term_centering_policy: Literal[
        "training_cohort_mean_to_intercept"
    ] = "training_cohort_mean_to_intercept"
    extrapolation_policy: Literal[
        "constant_boundary_with_support_warning"
    ] = "constant_boundary_with_support_warning"
    capacity_policy_version: Literal[
        "bayesian-additive-capacity/v1"
    ] = "bayesian-additive-capacity/v1"
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


class QuantileLinearRegressionEstimatorRecipe(ContractModel):
    estimator_id: Literal["quantile-linear-regression.v1"] = (
        "quantile-linear-regression.v1"
    )
    quantile_levels: tuple[
        Literal[0.05],
        Literal[0.5],
        Literal[0.95],
    ] = (0.05, 0.5, 0.95)
    penalty: Annotated[float, Field(ge=0, le=1_000)] = 0.01
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    crossing_policy: Literal["reject"] = "reject"
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


class StudentTLinearRegressionEstimatorRecipe(ContractModel):
    estimator_id: Literal["student-t-linear-regression.v1"] = (
        "student-t-linear-regression.v1"
    )
    inference_preset: Literal[
        "quick-evidence",
        "standard-evidence",
    ] = "standard-evidence"
    df_policy: Literal[
        "bounded-beta-2-5-on-2p1-30"
    ] = "bounded-beta-2-5-on-2p1-30"
    coefficient_prior_scale: Literal[1.0] = 1.0
    intercept_prior_scale: Literal[2.0] = 2.0
    observation_scale_prior: Literal["half-normal-1"] = "half-normal-1"
    target_accept_probability: Literal[0.9] = 0.9
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**31 - 1)] = 20260730
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


class LightGBMRegressionEstimatorRecipe(ContractModel):
    estimator_id: Literal["lightgbm-regression.v1"] = "lightgbm-regression.v1"
    num_boost_round: Annotated[int, Field(ge=1, le=5_000)] = 200
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    predictive_family: Literal["empirical_quantiles", "normal"] = (
        "empirical_quantiles"
    )
    monotone_decreasing_features: tuple[str, ...] = ()
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


class LightGBMBinaryEstimatorRecipe(ContractModel):
    estimator_id: Literal["lightgbm-binary.v1"] = "lightgbm-binary.v1"
    num_boost_round: Annotated[int, Field(ge=1, le=5_000)] = 100
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


class LogisticEstimatorRecipe(ContractModel):
    estimator_id: Literal["logistic.v1"] = "logistic.v1"
    c: Annotated[float, Field(gt=0, le=1_000_000)] = 1.0
    max_iter: Annotated[int, Field(ge=100, le=2_000)] = 500
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    calibration: Literal["intrinsic-logistic-link"] = "intrinsic-logistic-link"
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


class PoissonEstimatorRecipe(ContractModel):
    estimator_id: Literal["poisson.v1"] = "poisson.v1"
    alpha: Annotated[float, Field(ge=0, le=1_000_000)] = 1.0
    max_iter: Annotated[int, Field(ge=100, le=2_000)] = 500
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    validation_plan: ValidationPlan | None = None
    validation_plans_by_target: dict[str, ValidationPlan] | None = None


ConcreteEstimatorRecipe = (
    RidgeEstimatorRecipe
    | ExactGPEstimatorRecipe
    | BayesianAdditiveSplineEstimatorRecipe
    | QuantileLinearRegressionEstimatorRecipe
    | StudentTLinearRegressionEstimatorRecipe
    | LightGBMRegressionEstimatorRecipe
    | LightGBMBinaryEstimatorRecipe
    | LogisticEstimatorRecipe
    | PoissonEstimatorRecipe
)
EstimatorRecipe = Annotated[
    ConcreteEstimatorRecipe,
    Field(discriminator="estimator_id"),
]
_RECIPE_ADAPTER = TypeAdapter(EstimatorRecipe)
ESTIMATOR_IDS = (
    "ridge.v1",
    "exact-gp-rbf.v1",
    "bayesian-additive-spline.v1",
    "quantile-linear-regression.v1",
    "student-t-linear-regression.v1",
    "lightgbm-regression.v1",
    "lightgbm-binary.v1",
    "logistic.v1",
    "poisson.v1",
)


class CsvOnboardingEstimatorOption(ContractModel):
    """A reviewed standard builder the CSV flow may present to a user.

    This is deliberately narrower than ``ESTIMATOR_IDS``.  The latter is the
    internal recipe universe; this list is the user-facing allow-list for a
    new continuous tabular Task.  Keeping the distinction here prevents the
    UI from turning an estimator name into a free-form runtime selector.
    """

    estimator_id: Literal["ridge.v1", "lightgbm-regression.v1"]
    label: str
    summary: str
    available: bool
    unavailable_reason: str | None = None
    dependency: str | None = None
    point_statistic: Literal["mean"] = "mean"
    quantiles: bool = True
    standard_deviation: bool = False
    parametric_distribution: bool = False
    goal_probability: Literal["unavailable"] = "unavailable"
    training_cost: Literal["light", "moderate"]
    artifact_size: Literal["small", "moderate"]
    fixed_parameters: dict[str, int | float | str]
    readiness_schema_version: Literal["standard-estimator-readiness/v1"]
    runtime_type: str
    artifact_format: str
    min_rows: Annotated[int, Field(ge=1)]
    max_rows: Annotated[int, Field(ge=1)]
    max_features: Annotated[int, Field(ge=1)]


CSV_ONBOARDING_ESTIMATOR_IDS = (
    "ridge.v1",
    "lightgbm-regression.v1",
)


def csv_onboarding_estimator_options() -> tuple[CsvOnboardingEstimatorOption, ...]:
    """Return the only standard estimators a new CSV regression Task may use.

    Checking a pinned optional dependency without importing it lets the UI
    explain an unavailable reviewed option before it starts the compensating
    build/promote operation.  It never discovers arbitrary Python modules.
    """

    from decision_workbench.modeling.training.readiness import (
        standard_estimator_catalog,
    )

    catalog = {
        entry.estimator_id: entry
        for entry in standard_estimator_catalog().entries
    }
    ridge = catalog["ridge.v1"]
    lightgbm = catalog["lightgbm-regression.v1"]
    assert ridge.runtime_type is not None and ridge.artifact_format is not None
    assert lightgbm.runtime_type is not None and lightgbm.artifact_format is not None
    lightgbm_available = find_spec("lightgbm") is not None
    return (
        CsvOnboardingEstimatorOption(
            estimator_id="ridge.v1",
            label="Ridge 回帰（既定）",
            summary="軽量な線形 baseline。平均予測と経験的な予測区間を返します。",
            available=True,
            training_cost="light",
            artifact_size="small",
            readiness_schema_version="standard-estimator-readiness/v1",
            runtime_type=ridge.runtime_type,
            artifact_format=ridge.artifact_format,
            min_rows=ridge.limits.min_rows,
            max_rows=ridge.limits.max_rows,
            max_features=ridge.limits.max_features,
            fixed_parameters=estimator_recipe("ridge.v1").model_dump(
                mode="json",
                exclude={"estimator_id"},
                exclude_none=True,
            ),
        ),
        CsvOnboardingEstimatorOption(
            estimator_id="lightgbm-regression.v1",
            label="LightGBM 回帰",
            summary="非線形な関係を表せる tree 系。平均予測と経験的な予測区間を返します。",
            available=lightgbm_available,
            unavailable_reason=(
                None
                if lightgbm_available
                else "この実行環境には allow-list 済み LightGBM runtime がありません。"
            ),
            dependency="lightgbm",
            training_cost="moderate",
            artifact_size="moderate",
            readiness_schema_version="standard-estimator-readiness/v1",
            runtime_type=lightgbm.runtime_type,
            artifact_format=lightgbm.artifact_format,
            min_rows=lightgbm.limits.min_rows,
            max_rows=lightgbm.limits.max_rows,
            max_features=lightgbm.limits.max_features,
            fixed_parameters={
                "num_boost_round": 200,
                "folds": 5,
                "seed": 20260730,
                "predictive_family": "empirical_quantiles",
            },
        ),
    )


def csv_onboarding_estimator_recipe(estimator_id: str) -> ConcreteEstimatorRecipe:
    """Resolve one available, fixed-parameter CSV onboarding recipe."""

    option = next(
        (item for item in csv_onboarding_estimator_options() if item.estimator_id == estimator_id),
        None,
    )
    if option is None:
        raise ValueError("CSV onboarding does not allow the requested estimator")
    if not option.available:
        raise ValueError(option.unavailable_reason or "CSV onboarding estimator is unavailable")
    return estimator_recipe(estimator_id)


def estimator_recipe(
    estimator_id: str,
    parameters: dict[str, Any] | None = None,
) -> ConcreteEstimatorRecipe:
    return _RECIPE_ADAPTER.validate_python(
        {"estimator_id": estimator_id, **(parameters or {})}
    )


def validate_recipe_capability(
    recipe: ConcreteEstimatorRecipe,
    capability: RuntimeCapability,
) -> None:
    """Reject estimators that cannot satisfy the Task's declared prediction meaning."""

    errors: list[str] = []
    if capability.joint_samples:
        errors.append("standard estimators do not expose joint samples")
    for target in capability.targets:
        if recipe.estimator_id in {"lightgbm-binary.v1", "logistic.v1"}:
            if tuple(target.point_statistics) != ("probability",):
                errors.append(
                    f"{target.target}: binary estimator point statistic must be probability"
                )
            if target.standard_deviation or target.quantiles or target.samples:
                errors.append(
                    f"{target.target}: binary estimator exposes probability only"
                )
            if not target.parametric_distribution:
                errors.append(
                    f"{target.target}: binary estimator exposes a Bernoulli distribution"
                )
            if target.uncertainty_components:
                errors.append(
                    f"{target.target}: binary estimator has no uncertainty components"
                )
            if target.goal_probability != "unavailable":
                errors.append(
                    f"{target.target}: binary estimator cannot provide goal probability"
                )
            continue
        if recipe.estimator_id == "poisson.v1":
            if tuple(target.point_statistics) != ("rate",):
                errors.append(
                    f"{target.target}: Poisson point statistic must be rate"
                )
            if target.standard_deviation or target.samples:
                errors.append(
                    f"{target.target}: Poisson standard path exposes rate and quantiles"
                )
            if not target.quantiles or not target.parametric_distribution:
                errors.append(
                    f"{target.target}: Poisson exposes discrete quantiles and a distribution"
                )
            if target.uncertainty_components:
                errors.append(
                    f"{target.target}: Poisson has no decomposed uncertainty components"
                )
            if target.goal_probability != "unavailable":
                errors.append(
                    f"{target.target}: Poisson standard path cannot provide goal probability"
                )
            continue
        if recipe.estimator_id == "quantile-linear-regression.v1":
            if tuple(target.point_statistics) != ("median",):
                errors.append(
                    f"{target.target}: quantile linear point statistic must be median"
                )
            if target.standard_deviation or target.samples:
                errors.append(
                    f"{target.target}: quantile linear exposes learned quantiles only"
                )
            if not target.quantiles:
                errors.append(
                    f"{target.target}: quantile linear exposes learned quantiles"
                )
            if target.parametric_distribution or target.uncertainty_components:
                errors.append(
                    f"{target.target}: quantile linear has no parametric distribution"
                )
            if target.goal_probability != "unavailable":
                errors.append(
                    f"{target.target}: quantile linear cannot provide goal probability"
                )
            continue
        if recipe.estimator_id == "student-t-linear-regression.v1":
            if tuple(target.point_statistics) != ("mean",):
                errors.append(
                    f"{target.target}: Student-t linear point statistic must be mean"
                )
            if not target.standard_deviation:
                errors.append(
                    f"{target.target}: Student-t linear exposes predictive standard deviation"
                )
            if not target.quantiles:
                errors.append(
                    f"{target.target}: Student-t linear exposes predictive quantiles"
                )
            if target.samples:
                errors.append(
                    f"{target.target}: Student-t linear does not expose raw posterior samples"
                )
            if not target.parametric_distribution:
                errors.append(
                    f"{target.target}: Student-t linear exposes a Student-t distribution"
                )
            if target.uncertainty_components:
                errors.append(
                    f"{target.target}: Student-t linear does not expose decomposed uncertainty components"
                )
            if target.goal_probability != "distribution":
                errors.append(
                    f"{target.target}: Student-t linear requires distribution goal probability"
                )
            continue
        if tuple(target.point_statistics) != ("mean",):
            errors.append(f"{target.target}: point statistic must be mean")
        if recipe.estimator_id == "ridge.v1" or (
            recipe.estimator_id == "lightgbm-regression.v1"
            and recipe.predictive_family == "empirical_quantiles"
        ):
            if target.standard_deviation:
                errors.append(
                    f"{target.target}: {recipe.estimator_id} has no standard deviation"
                )
            if not target.quantiles:
                errors.append(f"{target.target}: {recipe.estimator_id} exposes quantiles")
            if target.samples:
                errors.append(f"{target.target}: {recipe.estimator_id} exposes no samples")
            if target.parametric_distribution:
                errors.append(
                    f"{target.target}: {recipe.estimator_id} has no parametric distribution"
                )
            if target.uncertainty_components:
                errors.append(
                    f"{target.target}: {recipe.estimator_id} has no uncertainty components"
                )
            if target.goal_probability != "unavailable":
                errors.append(
                    f"{target.target}: {recipe.estimator_id} cannot provide goal probability"
                )
        elif recipe.estimator_id == "lightgbm-regression.v1":
            if not target.standard_deviation:
                errors.append(
                    f"{target.target}: normal LightGBM exposes standard deviation"
                )
            if not target.quantiles:
                errors.append(f"{target.target}: normal LightGBM exposes quantiles")
            if target.samples:
                errors.append(f"{target.target}: normal LightGBM exposes no samples")
            if not target.parametric_distribution:
                errors.append(
                    f"{target.target}: normal LightGBM exposes a normal distribution"
                )
            if not target.uncertainty_components:
                errors.append(
                    f"{target.target}: normal LightGBM exposes uncertainty components"
                )
            if target.goal_probability != "unavailable":
                errors.append(
                    f"{target.target}: normal LightGBM cannot provide goal probability"
                )
        else:
            label = (
                "Bayesian additive"
                if recipe.estimator_id == "bayesian-additive-spline.v1"
                else "exact GP"
            )
            if not target.standard_deviation:
                errors.append(f"{target.target}: {label} exposes standard deviation")
            if not target.quantiles:
                errors.append(f"{target.target}: {label} exposes quantiles")
            if target.samples:
                errors.append(f"{target.target}: {label} exposes no samples")
            if not target.parametric_distribution:
                errors.append(f"{target.target}: {label} exposes a normal distribution")
            if not target.uncertainty_components:
                errors.append(f"{target.target}: {label} exposes uncertainty components")
            expected_goal = (
                "unavailable"
                if recipe.estimator_id == "bayesian-additive-spline.v1"
                else "distribution"
            )
            if target.goal_probability != expected_goal:
                errors.append(
                    f"{target.target}: {label} requires {expected_goal} goal probability"
                )
    if errors:
        raise ValueError(
            f"{recipe.estimator_id} is incompatible with {capability.task_id}: "
            + "; ".join(errors)
        )
