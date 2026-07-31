from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from decision_workbench.contracts.task_contracts import ContractModel, RuntimeCapability


class RidgeEstimatorRecipe(ContractModel):
    estimator_id: Literal["ridge.v1"] = "ridge.v1"
    alpha: Annotated[float, Field(gt=0, le=1_000_000)] = 1.0
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730


class ExactGPEstimatorRecipe(ContractModel):
    estimator_id: Literal["exact-gp-rbf.v1"] = "exact-gp-rbf.v1"
    restarts: Annotated[int, Field(ge=1, le=12)] = 3
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    max_rows: Annotated[int, Field(ge=3, le=2_000)] = 500


class LightGBMRegressionEstimatorRecipe(ContractModel):
    estimator_id: Literal["lightgbm-regression.v1"] = "lightgbm-regression.v1"
    num_boost_round: Annotated[int, Field(ge=1, le=5_000)] = 200
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730
    predictive_family: Literal["empirical_quantiles", "normal"] = (
        "empirical_quantiles"
    )
    monotone_decreasing_features: tuple[str, ...] = ()


class LightGBMBinaryEstimatorRecipe(ContractModel):
    estimator_id: Literal["lightgbm-binary.v1"] = "lightgbm-binary.v1"
    num_boost_round: Annotated[int, Field(ge=1, le=5_000)] = 100
    folds: Annotated[int, Field(ge=2, le=20)] = 5
    seed: Annotated[int, Field(ge=0, le=2**32 - 1)] = 20260730


ConcreteEstimatorRecipe = (
    RidgeEstimatorRecipe
    | ExactGPEstimatorRecipe
    | LightGBMRegressionEstimatorRecipe
    | LightGBMBinaryEstimatorRecipe
)
EstimatorRecipe = Annotated[
    ConcreteEstimatorRecipe,
    Field(discriminator="estimator_id"),
]
_RECIPE_ADAPTER = TypeAdapter(EstimatorRecipe)
ESTIMATOR_IDS = (
    "ridge.v1",
    "exact-gp-rbf.v1",
    "lightgbm-regression.v1",
    "lightgbm-binary.v1",
)


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
        if recipe.estimator_id == "lightgbm-binary.v1":
            if tuple(target.point_statistics) != ("probability",):
                errors.append(
                    f"{target.target}: binary LightGBM point statistic must be probability"
                )
            if target.standard_deviation or target.quantiles or target.samples:
                errors.append(
                    f"{target.target}: binary LightGBM exposes probability only"
                )
            if not target.parametric_distribution:
                errors.append(
                    f"{target.target}: binary LightGBM exposes a Bernoulli distribution"
                )
            if target.uncertainty_components:
                errors.append(
                    f"{target.target}: binary LightGBM has no uncertainty components"
                )
            if target.goal_probability != "unavailable":
                errors.append(
                    f"{target.target}: binary LightGBM cannot provide goal probability"
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
            if not target.standard_deviation:
                errors.append(f"{target.target}: exact GP exposes standard deviation")
            if not target.quantiles:
                errors.append(f"{target.target}: exact GP exposes quantiles")
            if target.samples:
                errors.append(f"{target.target}: exact GP exposes no samples")
            if not target.parametric_distribution:
                errors.append(f"{target.target}: exact GP exposes a normal distribution")
            if not target.uncertainty_components:
                errors.append(f"{target.target}: exact GP exposes uncertainty components")
            if target.goal_probability != "distribution":
                errors.append(
                    f"{target.target}: exact GP requires distribution goal probability"
                )
    if errors:
        raise ValueError(
            f"{recipe.estimator_id} is incompatible with {capability.task_id}: "
            + "; ".join(errors)
        )
