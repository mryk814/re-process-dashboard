"""Machine-readable readiness for the bounded standard estimator catalog.

The catalog describes what the application can build safely.  The resolver is
deliberately diagnostic: it never substitutes an estimator, starts a build, or
promotes a Package.
"""
from __future__ import annotations

from importlib.util import find_spec
from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.feature_recipe_contracts import FeatureRecipe
from decision_workbench.contracts.task_contracts import ContractModel, OutputDefinition
from decision_workbench.modeling.training.estimators import estimator_implementation
from decision_workbench.modeling.training.recipe import estimator_recipe
from decision_workbench.modeling.training.validation_plan import (
    ValidationPlan,
    ValidationStrategy,
)


READINESS_SCHEMA_VERSION = "standard-estimator-readiness/v1"

TargetKind = Literal[
    "continuous",
    "continuous_positive",
    "binary",
    "count",
    "ordinal",
    "nominal_multiclass",
]
ReadinessStatus = Literal[
    "ready",
    "unavailable_missing_dependency",
    "needs_feature_recipe",
    "needs_validation_plan",
    "needs_target_contract",
    "external_verified_package_only",
    "out_of_scope",
]
ContractStatus = Literal["ready", "missing", "invalid"]
SupportLevel = Literal[
    "native",
    "feature_recipe",
    "unsupported",
]
BuilderStatus = Literal[
    "standard_builder",
    "external_verified_package_only",
    "not_available",
]
RuntimeStatus = Literal[
    "ready",
    "needs_adapter_allowlist",
    "external_verified_package_only",
    "not_available",
]
ArtifactStatus = Literal[
    "ready",
    "needs_adapter_allowlist",
    "external_verified_package_only",
    "not_available",
]


class EstimatorLimits(ContractModel):
    min_rows: Annotated[int, Field(ge=1)]
    max_rows: Annotated[int, Field(ge=1)]
    min_independent_groups: Annotated[int, Field(ge=2)]
    max_features: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def limits_are_ordered(self) -> "EstimatorLimits":
        if self.max_rows < self.min_rows:
            raise ValueError("max_rows must be greater than or equal to min_rows")
        return self


class StandardEstimatorEntry(ContractModel):
    estimator_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    target_kinds: Annotated[tuple[TargetKind, ...], Field(min_length=1)]
    role: Literal["interpretable_baseline", "nonlinear_candidate", "specialized_path"]
    builder_status: BuilderStatus
    runtime_status: RuntimeStatus
    runtime_type: Annotated[str, Field(min_length=1)] | None
    artifact_status: ArtifactStatus
    artifact_format: Annotated[str, Field(min_length=1)] | None
    required_dependency: str | None = None
    limits: EstimatorLimits
    categorical_support: SupportLevel
    missing_support: SupportLevel
    validation_strategies: tuple[ValidationStrategy, ...]
    predictive_capabilities: tuple[
        Literal[
            "point",
            "probability",
            "quantiles",
            "standard_deviation",
            "parametric_distribution",
        ],
        ...,
    ]
    quality_metrics: Annotated[tuple[str, ...], Field(min_length=1)]
    fixed_parameters: dict[str, int | float | str | bool | tuple[str, ...]]

    @model_validator(mode="after")
    def builder_and_runtime_are_consistent(self) -> "StandardEstimatorEntry":
        if (
            self.builder_status == "external_verified_package_only"
            and self.required_dependency is not None
        ):
            raise ValueError(
                "external verified Package paths must not claim a local builder dependency"
            )
        if (self.runtime_status == "ready") != (self.runtime_type is not None):
            raise ValueError("only ready runtime entries declare a runtime_type")
        if (self.artifact_status == "ready") != (self.artifact_format is not None):
            raise ValueError("only ready artifact entries declare an artifact_format")
        return self


class StandardEstimatorCatalog(ContractModel):
    schema_version: Literal[READINESS_SCHEMA_VERSION] = READINESS_SCHEMA_VERSION
    promotion_policy: Literal["explicit_only"] = "explicit_only"
    entries: Annotated[tuple[StandardEstimatorEntry, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def estimator_ids_are_unique(self) -> "StandardEstimatorCatalog":
        ids = [entry.estimator_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("estimator readiness ids must be unique")
        return self


class EstimatorReadinessContext(ContractModel):
    estimator_id: Annotated[str, Field(min_length=1)]
    target_kind: TargetKind
    row_count: Annotated[int, Field(ge=0)]
    independent_group_count: Annotated[int, Field(ge=0)]
    feature_count: Annotated[int, Field(ge=0)]
    has_categorical_features: bool = False
    has_missing_features: bool = False
    has_count_exposure: bool = False
    observed_target_min: float | None = None
    observed_targets_are_integers: bool | None = None
    target_contract: ContractStatus
    validation_plan: ContractStatus
    validation_strategy: ValidationStrategy | None = None
    feature_recipe: ContractStatus
    missing_policy: ContractStatus = "ready"

    @model_validator(mode="after")
    def ready_validation_has_a_strategy(self) -> "EstimatorReadinessContext":
        if (self.validation_plan == "ready") != (
            self.validation_strategy is not None
        ):
            raise ValueError(
                "ready validation_plan requires one concrete validation_strategy"
            )
        return self


class EstimatorReadinessResolution(ContractModel):
    schema_version: Literal[READINESS_SCHEMA_VERSION] = READINESS_SCHEMA_VERSION
    estimator_id: str
    target_kind: TargetKind
    status: ReadinessStatus
    reasons: Annotated[tuple[str, ...], Field(min_length=1)]
    required_dependency: str | None
    runtime_type: str | None
    artifact_format: str | None
    builder_status: BuilderStatus | None
    limits: EstimatorLimits | None
    alternative_baseline_ids: tuple[str, ...] = ()
    starts_build: Literal[False] = False
    promotes_package: Literal[False] = False


def _implementation_fields(estimator_id: str) -> dict[str, str]:
    implementation = estimator_implementation(estimator_id)
    return {
        "runtime_type": implementation.runtime_type,
        "artifact_format": implementation.artifact_format,
    }


def _fixed_parameters(
    estimator_id: str,
) -> dict[str, int | float | str | bool | tuple[str, ...]]:
    return estimator_recipe(estimator_id).model_dump(
        mode="python",
        exclude={
            "estimator_id",
            "validation_plan",
            "validation_plans_by_target",
        },
        exclude_none=True,
    )


_CATALOG = StandardEstimatorCatalog(
    entries=(
        StandardEstimatorEntry(
            estimator_id="ridge.v1",
            label="Ridge regression",
            target_kinds=("continuous",),
            role="interpretable_baseline",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields("ridge.v1")["runtime_type"],
            artifact_status="ready",
            artifact_format=_implementation_fields("ridge.v1")["artifact_format"],
            limits=EstimatorLimits(
                min_rows=4,
                max_rows=100_000,
                min_independent_groups=4,
                max_features=512,
            ),
            categorical_support="feature_recipe",
            missing_support="feature_recipe",
            validation_strategies=(
                "kfold",
                "grouped_kfold",
                "temporal_holdout",
                "grouped_temporal",
            ),
            predictive_capabilities=("point", "quantiles"),
            quality_metrics=("mae", "rmse", "interval_coverage_90"),
            fixed_parameters=_fixed_parameters("ridge.v1"),
        ),
        StandardEstimatorEntry(
            estimator_id="exact-gp-rbf.v1",
            label="Small-data exact GP",
            target_kinds=("continuous",),
            role="nonlinear_candidate",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields("exact-gp-rbf.v1")["runtime_type"],
            artifact_status="ready",
            artifact_format=_implementation_fields("exact-gp-rbf.v1")[
                "artifact_format"
            ],
            limits=EstimatorLimits(
                min_rows=3,
                max_rows=500,
                min_independent_groups=3,
                max_features=64,
            ),
            categorical_support="feature_recipe",
            missing_support="feature_recipe",
            validation_strategies=(
                "kfold",
                "grouped_kfold",
                "temporal_holdout",
                "grouped_temporal",
            ),
            predictive_capabilities=(
                "point",
                "quantiles",
                "standard_deviation",
                "parametric_distribution",
            ),
            quality_metrics=("mae", "rmse", "interval_coverage_90"),
            fixed_parameters=_fixed_parameters("exact-gp-rbf.v1"),
        ),
        StandardEstimatorEntry(
            estimator_id="numpyro-lognormal-external.v1",
            label="Verified lognormal Package",
            target_kinds=("continuous_positive",),
            role="specialized_path",
            builder_status="external_verified_package_only",
            runtime_status="ready",
            runtime_type="numpyro.dense_posterior.v1",
            artifact_status="ready",
            artifact_format="bounded-npz",
            limits=EstimatorLimits(
                min_rows=4,
                max_rows=100_000,
                min_independent_groups=4,
                max_features=512,
            ),
            categorical_support="feature_recipe",
            missing_support="feature_recipe",
            validation_strategies=(
                "kfold",
                "grouped_kfold",
                "temporal_holdout",
                "grouped_temporal",
            ),
            predictive_capabilities=(
                "point",
                "quantiles",
                "parametric_distribution",
            ),
            quality_metrics=("mae", "rmse", "interval_coverage_90"),
            fixed_parameters={},
        ),
        StandardEstimatorEntry(
            estimator_id="lightgbm-regression.v1",
            label="LightGBM regression",
            target_kinds=("continuous",),
            role="nonlinear_candidate",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields("lightgbm-regression.v1")[
                "runtime_type"
            ],
            artifact_status="ready",
            artifact_format=_implementation_fields("lightgbm-regression.v1")[
                "artifact_format"
            ],
            required_dependency="lightgbm",
            limits=EstimatorLimits(
                min_rows=4,
                max_rows=1_000_000,
                min_independent_groups=4,
                max_features=2_048,
            ),
            categorical_support="feature_recipe",
            missing_support="feature_recipe",
            validation_strategies=(
                "kfold",
                "grouped_kfold",
                "temporal_holdout",
                "grouped_temporal",
            ),
            predictive_capabilities=("point", "quantiles"),
            quality_metrics=("mae", "rmse", "interval_coverage_90"),
            fixed_parameters=_fixed_parameters("lightgbm-regression.v1"),
        ),
        StandardEstimatorEntry(
            estimator_id="logistic.v1",
            label="Logistic regression",
            target_kinds=("binary",),
            role="interpretable_baseline",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields("logistic.v1")["runtime_type"],
            artifact_status="ready",
            artifact_format=_implementation_fields("logistic.v1")[
                "artifact_format"
            ],
            required_dependency="skops",
            limits=EstimatorLimits(
                min_rows=6,
                max_rows=100_000,
                min_independent_groups=4,
                max_features=512,
            ),
            categorical_support="feature_recipe",
            missing_support="feature_recipe",
            validation_strategies=(
                "stratified_kfold",
                "stratified_grouped_kfold",
                "temporal_holdout",
                "grouped_temporal",
            ),
            predictive_capabilities=(
                "point",
                "probability",
                "parametric_distribution",
            ),
            quality_metrics=(
                "roc_auc",
                "brier_score",
                "balanced_accuracy",
                "calibration",
            ),
            fixed_parameters=_fixed_parameters("logistic.v1"),
        ),
        StandardEstimatorEntry(
            estimator_id="lightgbm-binary.v1",
            label="Calibrated LightGBM binary",
            target_kinds=("binary",),
            role="nonlinear_candidate",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields("lightgbm-binary.v1")[
                "runtime_type"
            ],
            artifact_status="ready",
            artifact_format=_implementation_fields("lightgbm-binary.v1")[
                "artifact_format"
            ],
            required_dependency="lightgbm",
            limits=EstimatorLimits(
                min_rows=6,
                max_rows=1_000_000,
                min_independent_groups=4,
                max_features=2_048,
            ),
            categorical_support="feature_recipe",
            missing_support="feature_recipe",
            validation_strategies=(
                "stratified_kfold",
                "stratified_grouped_kfold",
                "temporal_holdout",
                "grouped_temporal",
            ),
            predictive_capabilities=(
                "point",
                "probability",
                "parametric_distribution",
            ),
            quality_metrics=(
                "roc_auc",
                "brier_score",
                "balanced_accuracy",
                "calibration",
            ),
            fixed_parameters=_fixed_parameters("lightgbm-binary.v1"),
        ),
        StandardEstimatorEntry(
            estimator_id="poisson.v1",
            label="Poisson regression",
            target_kinds=("count",),
            role="interpretable_baseline",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields("poisson.v1")["runtime_type"],
            artifact_status="ready",
            artifact_format=_implementation_fields("poisson.v1")[
                "artifact_format"
            ],
            required_dependency="skops",
            limits=EstimatorLimits(
                min_rows=4,
                max_rows=100_000,
                min_independent_groups=4,
                max_features=512,
            ),
            categorical_support="feature_recipe",
            missing_support="feature_recipe",
            validation_strategies=(
                "kfold",
                "grouped_kfold",
                "temporal_holdout",
                "grouped_temporal",
            ),
            predictive_capabilities=(
                "point",
                "quantiles",
                "parametric_distribution",
            ),
            quality_metrics=(
                "mae",
                "mean_poisson_deviance",
                "interval_coverage_90",
            ),
            fixed_parameters=_fixed_parameters("poisson.v1"),
        ),
        StandardEstimatorEntry(
            estimator_id="numpyro-ordinal-external.v1",
            label="Verified ordinal Package",
            target_kinds=("ordinal",),
            role="specialized_path",
            builder_status="external_verified_package_only",
            runtime_status="ready",
            runtime_type="numpyro.dense_posterior.v1",
            artifact_status="ready",
            artifact_format="bounded-npz",
            limits=EstimatorLimits(
                min_rows=4,
                max_rows=100_000,
                min_independent_groups=4,
                max_features=512,
            ),
            categorical_support="feature_recipe",
            missing_support="feature_recipe",
            validation_strategies=(
                "kfold",
                "grouped_kfold",
                "temporal_holdout",
                "grouped_temporal",
            ),
            predictive_capabilities=(
                "point",
                "quantiles",
                "parametric_distribution",
            ),
            quality_metrics=(
                "ordinal_log_loss",
                "ranked_probability_score",
                "accuracy",
            ),
            fixed_parameters={},
        ),
    )
)


def standard_estimator_catalog() -> StandardEstimatorCatalog:
    return _CATALOG


def _dependency_available(name: str, available_dependencies: frozenset[str] | None) -> bool:
    if available_dependencies is not None:
        return name in available_dependencies
    return find_spec(name) is not None


def _resolution(
    context: EstimatorReadinessContext,
    entry: StandardEstimatorEntry | None,
    status: ReadinessStatus,
    reasons: list[str],
) -> EstimatorReadinessResolution:
    alternatives = tuple(
        item.estimator_id
        for item in _CATALOG.entries
        if item.role == "interpretable_baseline"
        and context.target_kind in item.target_kinds
        and item.estimator_id != context.estimator_id
        and item.required_dependency is None
        and item.limits.min_rows <= context.row_count <= item.limits.max_rows
        and context.independent_group_count
        >= item.limits.min_independent_groups
        and context.feature_count <= item.limits.max_features
        and not (
            context.has_categorical_features
            and item.categorical_support == "unsupported"
        )
        and not (
            context.has_missing_features
            and item.missing_support == "unsupported"
        )
    )
    return EstimatorReadinessResolution(
        estimator_id=context.estimator_id,
        target_kind=context.target_kind,
        status=status,
        reasons=tuple(reasons),
        required_dependency=entry.required_dependency if entry else None,
        runtime_type=entry.runtime_type if entry else None,
        artifact_format=entry.artifact_format if entry else None,
        builder_status=entry.builder_status if entry else None,
        limits=entry.limits if entry else None,
        alternative_baseline_ids=alternatives,
    )


def resolve_estimator_readiness(
    context: EstimatorReadinessContext,
    *,
    available_dependencies: frozenset[str] | None = None,
) -> EstimatorReadinessResolution:
    """Explain one requested estimator without selecting or executing another."""

    if context.target_kind == "nominal_multiclass":
        return _resolution(
            context,
            None,
            "out_of_scope",
            [
                "nominal multiclass needs a dedicated target, predictive-summary, "
                "quality, and UI slice; it is never reduced to binary"
            ],
        )
    entry = next(
        (item for item in _CATALOG.entries if item.estimator_id == context.estimator_id),
        None,
    )
    if entry is None:
        return _resolution(
            context,
            None,
            "out_of_scope",
            ["the estimator is not in the bounded standard catalog"],
        )
    if context.target_kind not in entry.target_kinds:
        return _resolution(
            context,
            entry,
            "needs_target_contract",
            [
                f"{entry.estimator_id} does not implement target kind "
                f"{context.target_kind}"
            ],
        )
    if context.target_contract != "ready":
        return _resolution(
            context,
            entry,
            "needs_target_contract",
            ["the Task must declare valid target semantics before authoring"],
        )
    if context.validation_plan != "ready":
        return _resolution(
            context,
            entry,
            "needs_validation_plan",
            ["a valid validation-plan/v1 is required before model comparison"],
        )
    assert context.validation_strategy is not None
    if context.validation_strategy not in entry.validation_strategies:
        return _resolution(
            context,
            entry,
            "needs_validation_plan",
            [
                f"{context.validation_strategy} is not reviewed for "
                f"{entry.estimator_id} and target kind {context.target_kind}"
            ],
        )
    if context.feature_recipe != "ready":
        return _resolution(
            context,
            entry,
            "needs_feature_recipe",
            ["a valid feature-recipe/v1 is required before standard authoring"],
        )
    if context.target_kind == "count":
        if context.has_count_exposure:
            return _resolution(
                context,
                entry,
                "out_of_scope",
                [
                    "count exposure requires a separate offset/exposure contract; "
                    "poisson.v1 never ignores it"
                ],
            )
        if (
            context.observed_target_min is None
            or context.observed_targets_are_integers is None
        ):
            return _resolution(
                context,
                entry,
                "out_of_scope",
                ["count target support must be inspected before authoring"],
            )
        if (
            context.observed_target_min < 0
            or not context.observed_targets_are_integers
        ):
            return _resolution(
                context,
                entry,
                "out_of_scope",
                ["poisson.v1 requires nonnegative integer observations"],
            )
    if context.has_missing_features and context.missing_policy != "ready":
        return _resolution(
            context,
            entry,
            "needs_feature_recipe",
            ["missing features require an explicit fold-local missing policy"],
        )
    if context.has_categorical_features and entry.categorical_support == "unsupported":
        return _resolution(
            context,
            entry,
            "needs_feature_recipe",
            ["the estimator has no reviewed categorical feature encoding"],
        )
    if context.has_missing_features and entry.missing_support == "unsupported":
        return _resolution(
            context,
            entry,
            "needs_feature_recipe",
            ["the estimator has no reviewed missing-feature path"],
        )
    limits = entry.limits
    limit_reasons: list[str] = []
    if not limits.min_rows <= context.row_count <= limits.max_rows:
        limit_reasons.append(
            f"row count {context.row_count} is outside "
            f"{limits.min_rows}..{limits.max_rows}"
        )
    if context.independent_group_count < limits.min_independent_groups:
        limit_reasons.append(
            f"independent groups {context.independent_group_count} are below "
            f"{limits.min_independent_groups}"
        )
    if context.feature_count > limits.max_features:
        limit_reasons.append(
            f"feature count {context.feature_count} exceeds {limits.max_features}"
        )
    if limit_reasons:
        return _resolution(
            context,
            entry,
            "out_of_scope",
            limit_reasons,
        )
    if entry.builder_status == "external_verified_package_only":
        return _resolution(
            context,
            entry,
            "external_verified_package_only",
            ["the runtime is supported but no standard training builder is shipped"],
        )
    if entry.builder_status == "not_available":
        return _resolution(
            context,
            entry,
            "out_of_scope",
            ["the catalog entry is typed, but its safe standard builder is not shipped"],
        )
    if entry.required_dependency and not _dependency_available(
        entry.required_dependency,
        available_dependencies,
    ):
        return _resolution(
            context,
            entry,
            "unavailable_missing_dependency",
            [
                f"optional dependency {entry.required_dependency} is unavailable; "
                "no alternative estimator was selected"
            ],
        )
    return _resolution(
        context,
        entry,
        "ready",
        ["the requested estimator satisfies the declared bounded standard path"],
    )


def resolve_estimator_contract_readiness(
    *,
    estimator_id: str,
    output: OutputDefinition,
    validation_plan: ValidationPlan | None,
    feature_recipe: FeatureRecipe | None,
    row_count: int,
    independent_group_count: int,
    has_missing_features: bool = False,
    missing_policy: ContractStatus = "ready",
    observed_target_min: float | None = None,
    observed_targets_are_integers: bool | None = None,
    available_dependencies: frozenset[str] | None = None,
) -> EstimatorReadinessResolution:
    """Resolve from the typed contracts used by the real standard builder."""

    return resolve_estimator_readiness(
        EstimatorReadinessContext(
            estimator_id=estimator_id,
            target_kind=output.target_kind,
            row_count=row_count,
            independent_group_count=independent_group_count,
            feature_count=(
                len(feature_recipe.features)
                if feature_recipe is not None
                else 0
            ),
            has_categorical_features=(
                any(operation.kind == "one_hot" for operation in feature_recipe.operations)
                if feature_recipe is not None
                else False
            ),
            has_missing_features=has_missing_features,
            has_count_exposure=(
                output.count is not None
                and output.count.exposure_label is not None
            ),
            observed_target_min=observed_target_min,
            observed_targets_are_integers=observed_targets_are_integers,
            target_contract="ready",
            validation_plan=(
                "ready"
                if validation_plan is not None
                else "missing"
            ),
            validation_strategy=(
                validation_plan.strategy
                if validation_plan is not None
                else None
            ),
            feature_recipe=(
                "ready"
                if feature_recipe is not None
                else "missing"
            ),
            missing_policy=missing_policy,
        ),
        available_dependencies=available_dependencies,
    )
