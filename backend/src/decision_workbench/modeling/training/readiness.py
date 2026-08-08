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
from decision_workbench.modeling.training.capacity import (
    ExactGpCapacityContext,
    ExactGpCapacityPolicy,
    ExactGpCapacityResolution,
    exact_gp_capacity_policy,
    resolve_exact_gp_capacity,
)
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
    "ready_expensive",
    "unavailable_missing_dependency",
    "needs_feature_recipe",
    "needs_validation_plan",
    "needs_target_contract",
    "external_verified_package_only",
    "out_of_scope",
    "capacity_exceeded",
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
    max_smooth_terms: Annotated[int, Field(ge=1)] | None = None
    max_basis_columns: Annotated[int, Field(ge=1)] | None = None
    max_categorical_levels: Annotated[int, Field(ge=1)] | None = None

    @model_validator(mode="after")
    def limits_are_ordered(self) -> "EstimatorLimits":
        if self.max_rows < self.min_rows:
            raise ValueError("max_rows must be greater than or equal to min_rows")
        return self


class StandardEstimatorEntry(ContractModel):
    estimator_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    target_kinds: Annotated[tuple[TargetKind, ...], Field(min_length=1)]
    role: Literal[
        "interpretable_baseline",
        "interpretable_nonlinear_candidate",
        "nonlinear_candidate",
        "distribution_candidate",
        "specialized_path",
    ]
    adoption_status: Literal[
        "production",
        "experimental",
        "no_adopt",
    ] = "production"
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
    fixed_parameters: dict[
        str,
        int | float | str | bool | tuple[str, ...] | tuple[float, ...],
    ]
    training_cost: Literal["light", "moderate", "high"] | None = None
    known_limitations: tuple[str, ...] = ()
    capacity_policy: ExactGpCapacityPolicy | None = None

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
    smooth_term_count: Annotated[int, Field(ge=0)] | None = None
    total_basis_columns: Annotated[int, Field(ge=0)] | None = None
    maximum_categorical_levels: Annotated[int, Field(ge=0)] | None = None
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
    capacity: ExactGpCapacityContext | None = None

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
    capacity: ExactGpCapacityResolution | None = None
    alternative_baseline_ids: tuple[str, ...] = ()
    starts_build: Literal[False] = False
    promotes_package: Literal[False] = False


def compatible_standard_estimator_ids(
    outputs: tuple[OutputDefinition, ...],
) -> tuple[str, ...]:
    """Return shipped recipes whose target semantics cover every Task output.

    This is deliberately a contract-only candidate resolver. Row, group,
    feature, validation, dependency, and capacity checks remain owned by
    ``resolve_estimator_contract_readiness`` once a concrete Training Snapshot
    is selected.
    """

    target_kinds = {output.target_kind for output in outputs}
    return tuple(
        entry.estimator_id
        for entry in _CATALOG.entries
        if entry.builder_status == "standard_builder"
        and entry.adoption_status == "production"
        and target_kinds.issubset(set(entry.target_kinds))
    )


def buildable_standard_estimator_ids(
    outputs: tuple[OutputDefinition, ...],
) -> tuple[str, ...]:
    """Return explicit-build candidates, including reviewed experiments.

    The production resolver above intentionally remains production-only.  An
    experimental recipe may be built for a comparison report, but it must not
    become a UI or resolver default merely because a trainer exists.
    """

    target_kinds = {output.target_kind for output in outputs}
    return tuple(
        entry.estimator_id
        for entry in _CATALOG.entries
        if entry.builder_status == "standard_builder"
        and entry.adoption_status in {"production", "experimental"}
        and target_kinds.issubset(set(entry.target_kinds))
    )


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
            capacity_policy=exact_gp_capacity_policy(),
        ),
        StandardEstimatorEntry(
            estimator_id="bayesian-additive-spline.v1",
            label="Bayesian additive spline",
            target_kinds=("continuous",),
            role="interpretable_nonlinear_candidate",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields(
                "bayesian-additive-spline.v1"
            )["runtime_type"],
            artifact_status="ready",
            artifact_format=_implementation_fields(
                "bayesian-additive-spline.v1"
            )["artifact_format"],
            limits=EstimatorLimits(
                min_rows=6,
                max_rows=20_000,
                min_independent_groups=4,
                max_features=48,
                max_smooth_terms=48,
                max_basis_columns=288,
                max_categorical_levels=48,
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
            fixed_parameters=_fixed_parameters(
                "bayesian-additive-spline.v1"
            ),
            training_cost="moderate",
            known_limitations=(
                "P0 learns univariate main effects only; it does not model "
                "interactions.",
                "Correlated inputs can leave individual term shapes unstable "
                "even when prediction remains useful.",
                "Intervals are a conditional empirical-Bayes approximation "
                "with fixed basis and smoothing and plug-in observation noise.",
                "Term contributions are associational model explanations, not "
                "causal or independent intervention effects.",
            ),
        ),
        StandardEstimatorEntry(
            estimator_id="quantile-linear-regression.v1",
            label="Linear quantile regression",
            target_kinds=("continuous",),
            role="distribution_candidate",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields(
                "quantile-linear-regression.v1"
            )["runtime_type"],
            artifact_status="ready",
            artifact_format=_implementation_fields(
                "quantile-linear-regression.v1"
            )["artifact_format"],
            limits=EstimatorLimits(
                min_rows=6,
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
            quality_metrics=(
                "mae",
                "rmse",
                "pinball_loss_by_quantile",
                "interval_coverage_90",
                "mean_interval_width",
                "quantile_crossing_count",
            ),
            fixed_parameters=_fixed_parameters(
                "quantile-linear-regression.v1"
            ),
            training_cost="moderate",
            known_limitations=(
                "q05, q50, and q95 are fitted independently and can cross; "
                "crossing is reported and prediction rejects it instead of sorting.",
                "q05-q95 is a conditional quantile interval, not a normal "
                "distribution or a guarantee of 90 percent observed coverage.",
                "Mean, standard deviation, CDF, joint samples, and goal "
                "probability are unavailable.",
                "Additive quantile regression remains a separate unimplemented "
                "candidate.",
            ),
        ),
        StandardEstimatorEntry(
            estimator_id="student-t-linear-regression.v1",
            label="Robust Student-t linear regression",
            target_kinds=("continuous",),
            role="distribution_candidate",
            adoption_status="production",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields(
                "student-t-linear-regression.v1"
            )["runtime_type"],
            artifact_status="ready",
            artifact_format=_implementation_fields(
                "student-t-linear-regression.v1"
            )["artifact_format"],
            required_dependency="numpyro",
            limits=EstimatorLimits(
                min_rows=6,
                max_rows=5_000,
                min_independent_groups=4,
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
            quality_metrics=(
                "mae",
                "rmse",
                "median_absolute_error",
                "mean_log_predictive_density",
                "interval_coverage_90",
                "mean_interval_width",
                "extreme_residual_mae",
                "posterior_convergence",
            ),
            fixed_parameters=_fixed_parameters(
                "student-t-linear-regression.v1"
            ),
            training_cost="high",
            known_limitations=(
                "Student-t likelihood does not accept or repair unit mismatches, "
                "impossible values, parsing errors, duplicate identity conflicts, "
                "or input mistakes; those remain Data Quality failures.",
                "The location function is linear; heavy tails do not repair missing "
                "nonlinear structure.",
                "Degrees of freedom are conditional on the fixed bounded 2.1 to 30 "
                "policy and are not an unrestricted tail search.",
                "q05-q95 is a posterior predictive interval for a new observation, "
                "not a latent-mean credible interval or a coverage guarantee.",
                "Target posteriors are fitted separately and do not provide "
                "cross-target joint samples.",
                "Posterior draws remain inside the safe Package/runtime; raw "
                "samples and decomposed uncertainty components are not exposed.",
                "Production adoption means explicit availability as a distribution "
                "candidate; it is not an automatic winner over Ridge.",
            ),
        ),
        StandardEstimatorEntry(
            estimator_id="bayesian-ridge.v1",
            label="Bayesian ridge shrinkage",
            target_kinds=("continuous",),
            role="distribution_candidate",
            adoption_status="experimental",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields("bayesian-ridge.v1")[
                "runtime_type"
            ],
            artifact_status="ready",
            artifact_format=_implementation_fields("bayesian-ridge.v1")[
                "artifact_format"
            ],
            required_dependency="numpyro",
            limits=EstimatorLimits(
                min_rows=6,
                max_rows=5_000,
                min_independent_groups=4,
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
            quality_metrics=(
                "mae",
                "rmse",
                "mean_log_predictive_density",
                "interval_coverage_90",
                "mean_interval_width",
                "posterior_convergence",
                "coefficient_sign_and_rope",
            ),
            fixed_parameters=_fixed_parameters("bayesian-ridge.v1"),
            training_cost="high",
            known_limitations=(
                "Bayesian ridge is an experimental shrinkage candidate and is "
                "never an automatic replacement for Ridge.",
                "Coefficient sign and ROPE evidence is associational and can be "
                "unstable under correlated inputs; do not read it as an intervention "
                "claim or a ranking of explanatory value.",
                "No feature is deleted from the recipe because a posterior "
                "coefficient is near zero.",
                "Prediction uncertainty is posterior-predictive uncertainty and "
                "must not be read as coefficient credible intervals.",
                "NUTS dependency, sampling failures, divergence, and insufficient "
                "ESS produce typed unavailable or quality findings with no Ridge "
                "fallback.",
            ),
        ),
        StandardEstimatorEntry(
            estimator_id="horseshoe-linear.v1",
            label="Fixed Student-t capped horseshoe linear shrinkage",
            target_kinds=("continuous",),
            role="distribution_candidate",
            adoption_status="experimental",
            builder_status="standard_builder",
            runtime_status="ready",
            runtime_type=_implementation_fields("horseshoe-linear.v1")[
                "runtime_type"
            ],
            artifact_status="ready",
            artifact_format=_implementation_fields("horseshoe-linear.v1")[
                "artifact_format"
            ],
            required_dependency="numpyro",
            limits=EstimatorLimits(
                min_rows=6,
                max_rows=5_000,
                min_independent_groups=4,
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
            quality_metrics=(
                "mae",
                "rmse",
                "mean_log_predictive_density",
                "interval_coverage_90",
                "mean_interval_width",
                "posterior_convergence",
                "coefficient_sign_and_rope",
                "local_scale_summary",
            ),
            fixed_parameters=_fixed_parameters("horseshoe-linear.v1"),
            training_cost="high",
            known_limitations=(
                "This is an experimental fixed Student-t capped horseshoe variant, "
                "not the canonical regularized horseshoe prior, and is never an "
                "automatic replacement for Ridge.",
                "Global and local shrinkage remains associational evidence; correlated "
                "inputs can share or trade off posterior mass, so it is not an "
                "intervention claim or a ranking of explanatory value.",
                "No feature is deleted from the recipe because a posterior "
                "coefficient is near zero.",
                "Prediction uncertainty is posterior-predictive uncertainty and "
                "must not be read as coefficient credible intervals.",
                "NUTS dependency, sampling failures, divergence, and insufficient "
                "ESS produce typed unavailable or quality findings with no Ridge "
                "fallback.",
            ),
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
    *,
    capacity: ExactGpCapacityResolution | None = None,
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
        capacity=capacity,
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
    capacity_resolution = (
        resolve_exact_gp_capacity(context.capacity)
        if entry.estimator_id == "exact-gp-rbf.v1" and context.capacity is not None
        else None
    )
    limit_reasons: list[str] = []
    if capacity_resolution is None and not limits.min_rows <= context.row_count <= limits.max_rows:
        limit_reasons.append(
            f"row count {context.row_count} is outside "
            f"{limits.min_rows}..{limits.max_rows}"
        )
    if context.independent_group_count < limits.min_independent_groups:
        limit_reasons.append(
            f"independent groups {context.independent_group_count} are below "
            f"{limits.min_independent_groups}"
        )
    if capacity_resolution is None and context.feature_count > limits.max_features:
        limit_reasons.append(
            f"feature count {context.feature_count} exceeds {limits.max_features}"
        )
    if (
        limits.max_smooth_terms is not None
        and context.smooth_term_count is not None
        and context.smooth_term_count > limits.max_smooth_terms
    ):
        limit_reasons.append(
            f"smooth terms {context.smooth_term_count} exceed "
            f"{limits.max_smooth_terms}"
        )
    if (
        limits.max_basis_columns is not None
        and context.total_basis_columns is not None
        and context.total_basis_columns > limits.max_basis_columns
    ):
        limit_reasons.append(
            f"basis columns {context.total_basis_columns} exceed "
            f"{limits.max_basis_columns}"
        )
    if (
        limits.max_categorical_levels is not None
        and context.maximum_categorical_levels is not None
        and context.maximum_categorical_levels
        > limits.max_categorical_levels
    ):
        limit_reasons.append(
            f"categorical levels {context.maximum_categorical_levels} exceed "
            f"{limits.max_categorical_levels}"
        )
    if limit_reasons:
        return _resolution(
            context,
            entry,
            "out_of_scope",
            limit_reasons,
            capacity=capacity_resolution,
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
    if capacity_resolution is not None:
        if capacity_resolution.decision == "approximate_required":
            return _resolution(
                context,
                entry,
                "capacity_exceeded",
                list(capacity_resolution.reasons),
                capacity=capacity_resolution,
            )
        if capacity_resolution.decision == "exact_expensive":
            return _resolution(
                context,
                entry,
                "ready_expensive",
                list(capacity_resolution.reasons),
                capacity=capacity_resolution,
            )
        return _resolution(
            context,
            entry,
            "ready",
            list(capacity_resolution.reasons),
            capacity=capacity_resolution,
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
    canonical_feature_count: int | None = None,
    smooth_term_count: int | None = None,
    total_basis_columns: int | None = None,
    maximum_categorical_levels: int | None = None,
    has_categorical_features: bool | None = None,
    row_count: int,
    independent_group_count: int,
    has_missing_features: bool = False,
    missing_policy: ContractStatus = "ready",
    observed_target_min: float | None = None,
    observed_targets_are_integers: bool | None = None,
    capacity: ExactGpCapacityContext | None = None,
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
                else canonical_feature_count or 0
            ),
            smooth_term_count=smooth_term_count,
            total_basis_columns=total_basis_columns,
            maximum_categorical_levels=maximum_categorical_levels,
            has_categorical_features=(
                any(operation.kind == "one_hot" for operation in feature_recipe.operations)
                if feature_recipe is not None
                else bool(has_categorical_features)
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
                if (
                    feature_recipe is not None
                    or canonical_feature_count is not None
                )
                else "missing"
            ),
            missing_policy=missing_policy,
            capacity=capacity,
        ),
        available_dependencies=available_dependencies,
    )
