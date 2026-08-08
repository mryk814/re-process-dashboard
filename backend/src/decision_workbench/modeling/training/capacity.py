"""Typed capacity policy for the standard exact Gaussian-process path.

The capacity decision is deliberately separate from estimator selection.  It
describes the work that the selected recipe would perform on the *effective*
replicate-context rows and never changes that recipe to make it fit.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import ContractModel
from decision_workbench.modeling.training.validation_plan import ValidationStrategy


CAPACITY_POLICY_SCHEMA_VERSION = "exact-gp-capacity-policy/v1"
CAPACITY_CONTEXT_SCHEMA_VERSION = "exact-gp-capacity-context/v1"
CAPACITY_RESOLUTION_SCHEMA_VERSION = "exact-gp-capacity-resolution/v1"
CAPACITY_EVIDENCE_SCHEMA_VERSION = "exact-gp-capacity-evidence/v1"
CAPACITY_POLICY_ID = "exact-gp-capacity"
CAPACITY_POLICY_VERSION = "exact-gp-capacity/v1"
EXACT_GP_OPTIMIZER_MAX_ITERATIONS = 90

CapacityDecision = Literal["exact", "exact_expensive", "approximate_required"]
CapacityRecommendation = Literal[
    "exact_gp",
    "approximate_gp",
    "alternative_estimator",
    "manual_review",
]


class ExactGpCapacityPolicy(ContractModel):
    """Versioned, machine-readable basis for the current default boundary."""

    schema_version: Literal[CAPACITY_POLICY_SCHEMA_VERSION] = (
        CAPACITY_POLICY_SCHEMA_VERSION
    )
    policy_id: Literal[CAPACITY_POLICY_ID] = CAPACITY_POLICY_ID
    policy_version: Literal[CAPACITY_POLICY_VERSION] = CAPACITY_POLICY_VERSION
    default_effective_row_limit: Annotated[int, Field(ge=1)] = 500
    default_feature_limit: Annotated[int, Field(ge=1)] = 64
    benchmark_effective_rows: tuple[int, ...] = (100, 250, 500, 750, 1000)
    benchmark_features: tuple[int, ...] = (8, 32, 64)
    benchmark_folds: tuple[int, ...] = (3, 5)
    benchmark_restarts: tuple[int, ...] = (1, 3)
    max_exact_total_fit_count: Annotated[int, Field(ge=1)] = 12
    warning_wall_seconds: Annotated[float, Field(gt=0)] = 120.0
    hard_wall_seconds: Annotated[float, Field(gt=0)] = 300.0
    warning_peak_memory_bytes: Annotated[int, Field(gt=0)] = 384 * 1024 * 1024
    hard_peak_memory_bytes: Annotated[int, Field(gt=0)] = 512 * 1024 * 1024
    artifact_bytes_limit: Annotated[int, Field(gt=0)] = 256 * 1024 * 1024
    no_silent_row_reduction: Literal[True] = True
    no_silent_fold_reduction: Literal[True] = True
    approximate_adoption: Literal["no_adopt"] = "no_adopt"

    @model_validator(mode="after")
    def budgets_are_ordered(self) -> "ExactGpCapacityPolicy":
        if self.hard_wall_seconds < self.warning_wall_seconds:
            raise ValueError("hard wall budget must be >= warning wall budget")
        if self.hard_peak_memory_bytes < self.warning_peak_memory_bytes:
            raise ValueError("hard memory budget must be >= warning memory budget")
        if self.default_effective_row_limit not in self.benchmark_effective_rows:
            raise ValueError("default row limit must be represented in benchmark grid")
        if self.default_feature_limit not in self.benchmark_features:
            raise ValueError("default feature limit must be represented in benchmark grid")
        return self


class ExactGpCapacityContext(ContractModel):
    """The immutable load presented to the capacity resolver before a build."""

    schema_version: Literal[CAPACITY_CONTEXT_SCHEMA_VERSION] = (
        CAPACITY_CONTEXT_SCHEMA_VERSION
    )
    estimator_id: Literal["exact-gp-rbf.v1"] = "exact-gp-rbf.v1"
    raw_observation_count: Annotated[int, Field(ge=0)]
    effective_replicate_context_count: Annotated[int, Field(ge=0)]
    effective_training_rows: Annotated[int, Field(ge=0)]
    independent_validation_group_count: Annotated[int, Field(ge=0)]
    feature_count: Annotated[int, Field(ge=0)]
    validation_strategy: ValidationStrategy
    requested_folds: Annotated[int, Field(ge=1)]
    planned_quality_fit_count: Annotated[int, Field(ge=1)]
    final_fit_count: Annotated[int, Field(ge=1)]
    total_fit_count: Annotated[int, Field(ge=1)]
    optimizer_restarts: Annotated[int, Field(ge=1)]
    optimizer_max_iterations: Annotated[int, Field(ge=1)] = (
        EXACT_GP_OPTIMIZER_MAX_ITERATIONS
    )
    recipe_max_rows: Annotated[int, Field(ge=1)]
    seed: int
    cohort_digest: str | None = None
    fold_digest: str | None = None
    validation_plan_digest: str | None = None

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "ExactGpCapacityContext":
        if self.effective_replicate_context_count != self.effective_training_rows:
            raise ValueError(
                "effective replicate-context count must equal effective training rows"
            )
        if self.raw_observation_count < self.effective_training_rows:
            raise ValueError(
                "raw observation count cannot be below effective training rows"
            )
        if self.total_fit_count != self.planned_quality_fit_count + self.final_fit_count:
            raise ValueError("total fit count must equal quality plus final fits")
        return self


class ExactGpCapacityEstimate(ContractModel):
    estimated_wall_seconds: Annotated[float, Field(ge=0)]
    estimated_peak_memory_bytes: Annotated[int, Field(ge=0)]
    estimated_artifact_bytes: Annotated[int, Field(ge=0)]
    estimated_prediction_latency_ms: Annotated[float, Field(ge=0)]
    matrix_elements: Annotated[int, Field(ge=0)]
    optimizer_work_units: Annotated[int, Field(ge=0)]


class CapacityPathRecommendation(ContractModel):
    path_id: Annotated[str, Field(min_length=1)]
    path_kind: Literal["exact", "approximate", "alternative"]
    availability: Literal[
        "production",
        "experimental_no_adopt",
        "not_compatible",
    ]
    recommended: bool
    reason: Annotated[str, Field(min_length=1)]


class ExactGpCapacityResolution(ContractModel):
    """Explicit result; no field permits an implicit estimator switch."""

    schema_version: Literal[CAPACITY_RESOLUTION_SCHEMA_VERSION] = (
        CAPACITY_RESOLUTION_SCHEMA_VERSION
    )
    policy_id: Literal[CAPACITY_POLICY_ID] = CAPACITY_POLICY_ID
    policy_version: Literal[CAPACITY_POLICY_VERSION] = CAPACITY_POLICY_VERSION
    decision: CapacityDecision
    recommended_path: CapacityRecommendation
    reasons: Annotated[tuple[str, ...], Field(min_length=1)]
    context: ExactGpCapacityContext
    estimate: ExactGpCapacityEstimate
    paths: Annotated[tuple[CapacityPathRecommendation, ...], Field(min_length=1)]
    automatic_switch: Literal[False] = False
    row_reduction: Literal["forbidden"] = "forbidden"
    fold_reduction: Literal["forbidden"] = "forbidden"


def exact_gp_capacity_policy() -> ExactGpCapacityPolicy:
    return ExactGpCapacityPolicy()


def capacity_context_from_training_set(data: object, recipe: object) -> ExactGpCapacityContext:
    """Build a context from a compiled Training Set without retaining raw rows."""

    repeat_counts = tuple(int(item) for item in getattr(data, "repeat_counts"))
    effective_rows = int(len(getattr(data, "y")))
    folds = int(getattr(data, "folds"))
    strategy = getattr(getattr(data, "validation_plan"), "strategy")
    planned_quality_fits = 1 if strategy in {"temporal_holdout", "grouped_temporal"} else folds
    final_fits = 1
    return ExactGpCapacityContext(
        raw_observation_count=sum(repeat_counts),
        effective_replicate_context_count=effective_rows,
        effective_training_rows=effective_rows,
        independent_validation_group_count=len(
            set(getattr(data, "validation_groups"))
        ),
        feature_count=int(getattr(data, "x").shape[1]),
        validation_strategy=strategy,
        requested_folds=folds,
        planned_quality_fit_count=planned_quality_fits,
        final_fit_count=final_fits,
        total_fit_count=planned_quality_fits + final_fits,
        optimizer_restarts=int(getattr(recipe, "restarts")),
        optimizer_max_iterations=EXACT_GP_OPTIMIZER_MAX_ITERATIONS,
        recipe_max_rows=int(getattr(recipe, "max_rows")),
        seed=int(getattr(recipe, "seed")),
        cohort_digest=getattr(data, "cohort_digest", None),
        fold_digest=getattr(data, "fold_digest", None),
        validation_plan_digest=getattr(data, "validation_plan_digest", None),
    )


def estimate_exact_gp_capacity(
    context: ExactGpCapacityContext,
    *,
    policy: ExactGpCapacityPolicy | None = None,
) -> ExactGpCapacityEstimate:
    policy = policy or exact_gp_capacity_policy()
    n = context.effective_training_rows
    f = context.feature_count
    work_units = n * n * max(f, 1) * context.total_fit_count * context.optimizer_restarts
    # The exact implementation materializes pairwise scaled distances and a
    # dense covariance/precision matrix.  This is a conservative policy estimate,
    # not an observed measurement and is recorded as such in evidence.
    matrix_elements = n * n
    peak = (
        n * n * max(f, 1) * 8
        + matrix_elements * 8 * 6
        + n * max(f, 1) * 8 * 4
        + 64 * 1024 * 1024
    )
    wall = work_units * 1.0e-6
    artifact = max(1024, matrix_elements * 8 + n * max(f, 1) * 8 * 4)
    prediction_ms = max(0.01, n * max(f, 1) * 0.002)
    return ExactGpCapacityEstimate(
        estimated_wall_seconds=round(wall, 6),
        estimated_peak_memory_bytes=int(peak),
        estimated_artifact_bytes=int(artifact),
        estimated_prediction_latency_ms=round(prediction_ms, 6),
        matrix_elements=matrix_elements,
        optimizer_work_units=work_units,
    )


def resolve_exact_gp_capacity(
    context: ExactGpCapacityContext,
    *,
    policy: ExactGpCapacityPolicy | None = None,
) -> ExactGpCapacityResolution:
    policy = policy or exact_gp_capacity_policy()
    estimate = estimate_exact_gp_capacity(context, policy=policy)
    reasons = [
        f"raw observations={context.raw_observation_count}; effective replicate contexts={context.effective_training_rows}",
        f"features={context.feature_count}; validation strategy={context.validation_strategy}; requested folds={context.requested_folds}",
        f"planned fits={context.planned_quality_fit_count} quality + {context.final_fit_count} final = {context.total_fit_count}; restarts={context.optimizer_restarts}; maxiter={context.optimizer_max_iterations}",
        f"estimated wall={estimate.estimated_wall_seconds:.3f}s; peak memory={estimate.estimated_peak_memory_bytes} bytes; artifact={estimate.estimated_artifact_bytes} bytes",
    ]
    hard_reasons: list[str] = []
    expensive_reasons: list[str] = []
    if context.effective_training_rows > context.recipe_max_rows:
        hard_reasons.append(
            f"effective training rows {context.effective_training_rows} exceed recipe max_rows is "
            f"{context.recipe_max_rows}; rows are never truncated or subsampled"
        )
    elif context.effective_training_rows > policy.default_effective_row_limit:
        hard_reasons.append(
            f"effective training rows {context.effective_training_rows} exceed the exact GP boundary "
            f"{policy.default_effective_row_limit}; rows are never truncated or subsampled"
        )
    if context.feature_count > policy.default_feature_limit:
        hard_reasons.append(
            f"feature count {context.feature_count} exceeds the exact GP boundary {policy.default_feature_limit}"
        )
    if context.total_fit_count > policy.max_exact_total_fit_count:
        hard_reasons.append(
            f"planned fit count {context.total_fit_count} exceeds the exact GP budget {policy.max_exact_total_fit_count}"
        )
    if estimate.estimated_peak_memory_bytes > policy.hard_peak_memory_bytes:
        hard_reasons.append(
            f"estimated peak memory {estimate.estimated_peak_memory_bytes} exceeds hard budget {policy.hard_peak_memory_bytes}"
        )
    if estimate.estimated_wall_seconds > policy.hard_wall_seconds:
        hard_reasons.append(
            f"estimated wall {estimate.estimated_wall_seconds:.3f}s exceeds hard budget {policy.hard_wall_seconds:.3f}s"
        )
    if estimate.estimated_artifact_bytes > policy.artifact_bytes_limit:
        hard_reasons.append(
            f"estimated artifact {estimate.estimated_artifact_bytes} exceeds package artifact budget {policy.artifact_bytes_limit}"
        )
    if context.requested_folds not in policy.benchmark_folds:
        expensive_reasons.append(
            f"requested folds {context.requested_folds} are outside the benchmark basis {policy.benchmark_folds}"
        )
    if context.optimizer_restarts not in policy.benchmark_restarts:
        expensive_reasons.append(
            f"requested restarts {context.optimizer_restarts} are outside the benchmark basis {policy.benchmark_restarts}"
        )
    if estimate.estimated_peak_memory_bytes > policy.warning_peak_memory_bytes:
        expensive_reasons.append("estimated peak memory is above the warning budget")
    if estimate.estimated_wall_seconds > policy.warning_wall_seconds:
        expensive_reasons.append("estimated wall is above the warning budget")

    if hard_reasons:
        decision: CapacityDecision = "approximate_required"
        recommended = "alternative_estimator"
    elif expensive_reasons:
        decision = "exact_expensive"
        recommended = "exact_gp"
    else:
        decision = "exact"
        recommended = "exact_gp"
    reasons.extend(hard_reasons or expensive_reasons or [
        "the exact GP fits within the versioned default capacity envelope"
    ])

    ridge_compatible = (
        context.independent_validation_group_count >= 4
        and context.feature_count <= 512
    )
    paths = (
        CapacityPathRecommendation(
            path_id="exact-gp-rbf.v1",
            path_kind="exact",
            availability="production",
            recommended=recommended == "exact_gp",
            reason=(
                "selected exact path with all recipe parameters fixed"
                if recommended == "exact_gp"
                else "outside the exact capacity envelope; no implicit retry"
            ),
        ),
        CapacityPathRecommendation(
            path_id="fixed-random-feature-gp-spike.v1",
            path_kind="approximate",
            availability="experimental_no_adopt",
            recommended=False,
            reason=(
                "same-cohort benchmark candidate; no production Package/runtime adapter and adoption is explicitly no_adopt"
            ),
        ),
        CapacityPathRecommendation(
            path_id="ridge.v1",
            path_kind="alternative",
            availability="production" if ridge_compatible else "not_compatible",
            recommended=recommended == "alternative_estimator" and ridge_compatible,
            reason=(
                "compatible production baseline for the same cohort"
                if ridge_compatible
                else "requires at least four independent validation groups and at most 512 features"
            ),
        ),
    )
    if recommended == "alternative_estimator" and not ridge_compatible:
        recommended = "manual_review"
        reasons.append("no compatible production baseline is available for this context")
    return ExactGpCapacityResolution(
        decision=decision,
        recommended_path=recommended,
        reasons=tuple(reasons),
        context=context,
        estimate=estimate,
        paths=paths,
    )
