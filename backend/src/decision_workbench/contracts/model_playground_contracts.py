"""Persisted contracts for bounded standard-model comparison Runs."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.inference_policy_contracts import (
    InferenceIdentity,
)
from decision_workbench.contracts.task_contracts import ContractModel
from decision_workbench.modeling.packages.contracts import (
    SourceLifecycleProvenance,
)
from decision_workbench.modeling.training.validation_plan import ValidationPlan


Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
ComputeBudgetPreset = Literal["quick", "standard", "research"]
RecipeLifecycle = Literal[
    "production",
    "candidate",
    "experimental",
    "unavailable",
    "no_adopt",
    "specialized",
]
RecipeAvailability = Literal[
    "ready",
    "ready_expensive",
    "unavailable_missing_dependency",
    "needs_feature_recipe",
    "needs_validation_plan",
    "needs_target_contract",
    "capacity_exceeded",
    "specialized_only",
    "out_of_scope",
]


def semantic_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class ModelPlaygroundContract(ContractModel):
    pass


class ModelExplorationTargetContext(ModelPlaygroundContract):
    target_key: Annotated[str, Field(min_length=1)]
    row_count: Annotated[int, Field(ge=1)]
    training_snapshot_cohort_digest: Digest
    cohort_digest: Digest
    split_digest: Digest
    fold_digest: Digest
    validation_plan: ValidationPlan
    validation_plan_digest: Digest

    @model_validator(mode="after")
    def validation_digest_matches(self) -> "ModelExplorationTargetContext":
        expected = semantic_digest(self.validation_plan.model_dump(mode="json"))
        if self.validation_plan_digest != expected:
            raise ValueError("validation plan digest does not match")
        return self


class ModelExplorationFeatureIdentity(ModelPlaygroundContract):
    schema_version: Literal["model-exploration-feature-identity/v1"] = (
        "model-exploration-feature-identity/v1"
    )
    source: Literal["canonical_task_pipeline", "feature_recipe"]
    feature_recipe_id: str | None = None
    feature_recipe_version: str | None = None
    feature_recipe_digest: Digest | None = None
    feature_state_digest: Digest

    @model_validator(mode="after")
    def recipe_identity_is_complete(self) -> "ModelExplorationFeatureIdentity":
        values = (
            self.feature_recipe_id,
            self.feature_recipe_version,
            self.feature_recipe_digest,
        )
        if self.source == "feature_recipe" and any(value is None for value in values):
            raise ValueError("feature recipe identity is incomplete")
        if self.source == "canonical_task_pipeline" and any(
            value is not None for value in values
        ):
            raise ValueError("canonical Task pipeline cannot claim a Feature Recipe")
        return self


class ModelHypothesisIdentity(ModelPlaygroundContract):
    card_id: Annotated[str, Field(min_length=1)]
    card_version: Annotated[str, Field(min_length=1)]
    card_digest: Digest


class ModelExplorationTargetReadiness(ModelPlaygroundContract):
    target_key: Annotated[str, Field(min_length=1)]
    target_kind: Annotated[str, Field(min_length=1)]
    status: RecipeAvailability
    reasons: Annotated[tuple[str, ...], Field(min_length=1)]
    row_count: Annotated[int, Field(ge=0)]
    independent_group_count: Annotated[int, Field(ge=0)]
    feature_count: Annotated[int, Field(ge=0)]


class ModelExplorationRecipeSelection(ModelPlaygroundContract):
    recipe_id: Annotated[str, Field(min_length=1)]
    recipe_version: Annotated[str, Field(min_length=1)]
    recipe_digest: Digest
    label: Annotated[str, Field(min_length=1)]
    lifecycle: RecipeLifecycle
    availability: RecipeAvailability
    reasons: Annotated[tuple[str, ...], Field(min_length=1)]
    comparison_role: Literal["baseline", "candidate", "specialized"]
    required_dependency: str | None = None
    training_cost: Literal["light", "moderate", "high"]
    predictive_capabilities: tuple[str, ...]
    target_readiness: Annotated[
        tuple[ModelExplorationTargetReadiness, ...],
        Field(min_length=1),
    ]
    task_structure: Literal[
        "standard_independent_targets",
        "task_specific_specialized",
    ]
    effective_parameters: dict[str, Any]
    hypothesis: ModelHypothesisIdentity | None = None
    inference_identity: InferenceIdentity | None = None
    inference_unavailable_reason: str | None = None

    @model_validator(mode="after")
    def identity_and_availability_are_honest(
        self,
    ) -> "ModelExplorationRecipeSelection":
        expected = semantic_digest(
            {
                "recipe_id": self.recipe_id,
                "recipe_version": self.recipe_version,
                "effective_parameters": self.effective_parameters,
            }
        )
        if self.recipe_digest != expected:
            raise ValueError("recipe digest does not match effective parameters")
        if (
            self.inference_identity is None
            and not self.inference_unavailable_reason
        ):
            raise ValueError(
                "a recipe without Inference Identity requires an explicit reason"
            )
        if (
            self.availability not in {
                "ready",
                "ready_expensive",
                "specialized_only",
            }
            and self.lifecycle != "unavailable"
        ):
            raise ValueError("unavailable recipe must use unavailable lifecycle")
        target_keys = [item.target_key for item in self.target_readiness]
        if len(target_keys) != len(set(target_keys)):
            raise ValueError("target readiness identities must be unique")
        return self


class ModelExplorationContext(ModelPlaygroundContract):
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Digest
    profile_revision_id: Annotated[str, Field(min_length=1)]
    profile_digest: Digest
    training_snapshot_id: Annotated[str, Field(min_length=1)]
    training_snapshot_digest: Digest
    canonical_dataset_revision_id: Annotated[str, Field(min_length=1)]
    canonical_dataset_digest: Digest
    materialized_training_sha256: Annotated[
        str,
        Field(pattern=r"^[0-9a-f]{64}$"),
    ]
    source_lifecycle: SourceLifecycleProvenance
    feature_identity: ModelExplorationFeatureIdentity
    targets: Annotated[
        tuple[ModelExplorationTargetContext, ...],
        Field(min_length=1),
    ]

    @model_validator(mode="after")
    def lifecycle_matches_context(self) -> "ModelExplorationContext":
        lifecycle = self.source_lifecycle
        expected = (
            (self.profile_revision_id, lifecycle.profile_revision_id),
            (self.profile_digest, lifecycle.profile_digest),
            (
                self.training_snapshot_id,
                lifecycle.training_snapshot_id,
            ),
            (
                self.training_snapshot_digest,
                lifecycle.training_snapshot_digest,
            ),
            (
                self.canonical_dataset_revision_id,
                lifecycle.canonical_dataset_revision_id,
            ),
            (
                self.canonical_dataset_digest,
                lifecycle.canonical_dataset_digest,
            ),
            (
                self.materialized_training_sha256,
                lifecycle.materialized_training_sha256,
            ),
        )
        if any(left != right for left, right in expected):
            raise ValueError("Source Lifecycle provenance disagrees with Run context")
        return self


class ModelExplorationRunDefinition(ModelPlaygroundContract):
    context: ModelExplorationContext
    selected_recipes: Annotated[
        tuple[ModelExplorationRecipeSelection, ...],
        Field(min_length=1),
    ]
    compute_budget: ComputeBudgetPreset
    compute_budget_version: Literal["model-playground-budget/v1"] = (
        "model-playground-budget/v1"
    )
    seed_policy: Literal["recipe_fixed_and_persisted"] = (
        "recipe_fixed_and_persisted"
    )
    environment: "ModelExplorationEnvironment"
    context_digest: Digest
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def immutable_context_digest_matches(self) -> "ModelExplorationRunDefinition":
        expected = semantic_digest(
            self.model_dump(mode="json", exclude={"context_digest", "warnings"})
        )
        if self.context_digest != expected:
            raise ValueError("model exploration context digest does not match")
        recipe_ids = [item.recipe_id for item in self.selected_recipes]
        if len(recipe_ids) != len(set(recipe_ids)):
            raise ValueError("selected recipe identities must be unique")
        if any(
            item.availability not in {"ready", "ready_expensive"}
            for item in self.selected_recipes
        ):
            raise ValueError("a Run can only select executable recipes")
        return self


class ModelExplorationOptionalDependency(ModelPlaygroundContract):
    package: Annotated[str, Field(min_length=1)]
    available: bool
    version: str | None = None


class ModelExplorationEnvironment(ModelPlaygroundContract):
    schema_version: Literal["model-playground-environment/v1"] = (
        "model-playground-environment/v1"
    )
    python_version: Annotated[str, Field(min_length=1)]
    platform: Annotated[str, Field(min_length=1)]
    optional_dependencies: tuple[ModelExplorationOptionalDependency, ...]


class ModelExplorationTargetResult(ModelPlaygroundContract):
    target_key: Annotated[str, Field(min_length=1)]
    cohort_digest: Digest
    fold_digest: Digest
    validation_plan_digest: Digest
    metrics: dict[str, float | int | str | None]
    inference_identity: InferenceIdentity | None = None
    inference_unavailable_reason: str | None = None

    @model_validator(mode="after")
    def inference_evidence_is_explicit(self) -> "ModelExplorationTargetResult":
        if (
            self.inference_identity is None
            and not self.inference_unavailable_reason
        ):
            raise ValueError(
                "target result without Inference Identity requires a reason"
            )
        if (
            self.inference_identity is not None
            and self.inference_unavailable_reason is not None
        ):
            raise ValueError(
                "target result cannot claim both inference identity and absence"
            )
        return self


class ModelExplorationAttemptResult(ModelPlaygroundContract):
    package_id: Annotated[str, Field(min_length=1)]
    package_path: Annotated[str, Field(min_length=1)]
    manifest_digest: Digest
    build_seconds: Annotated[float, Field(ge=0)]
    peak_memory_bytes: Annotated[int, Field(ge=0)]
    artifact_size_bytes: Annotated[int, Field(ge=0)]
    prediction_latency_ms: Annotated[float | None, Field(ge=0)] = None
    capabilities: tuple[str, ...]
    targets: Annotated[
        tuple[ModelExplorationTargetResult, ...],
        Field(min_length=1),
    ]
    build_receipt_digest: Digest


class ModelExplorationAttemptFailure(ModelPlaygroundContract):
    code: Annotated[str, Field(min_length=1)]
    message: Annotated[str, Field(min_length=1)]
    recovery_hint: Annotated[str, Field(min_length=1)]


class ModelExplorationRegistrationReceipt(ModelPlaygroundContract):
    registered_at: datetime
    reference_id: Annotated[str, Field(min_length=1)]
    manifest_digest: Digest
    storage_scope: Literal["personal"] = "personal"
    active_package_changed: Literal[False] = False


class ModelExplorationRecipeAttempt(ModelPlaygroundContract):
    attempt_id: Annotated[str, Field(min_length=1)]
    recipe_id: Annotated[str, Field(min_length=1)]
    sequence: Annotated[int, Field(ge=1)]
    status: Literal["running", "completed", "failed", "interrupted"]
    recipe_digest: Digest
    hypothesis: ModelHypothesisIdentity | None = None
    inference_identity: InferenceIdentity | None = None
    execution_instance_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    result: ModelExplorationAttemptResult | None = None
    failure: ModelExplorationAttemptFailure | None = None
    registration: ModelExplorationRegistrationReceipt | None = None

    @model_validator(mode="after")
    def terminal_payload_matches_status(self) -> "ModelExplorationRecipeAttempt":
        if self.status == "running":
            if any(
                value is not None
                for value in (self.finished_at, self.result, self.failure)
            ):
                raise ValueError("running attempt cannot have terminal evidence")
        elif self.status == "completed":
            if (
                self.finished_at is None
                or self.result is None
                or self.failure is not None
            ):
                raise ValueError("completed attempt requires one result")
        else:
            if (
                self.finished_at is None
                or self.failure is None
                or self.result is not None
            ):
                raise ValueError("failed attempt requires one failure")
        if self.registration is not None and self.status != "completed":
            raise ValueError("only completed attempts may be registered")
        return self


class ModelExplorationAdoptionMemo(ModelPlaygroundContract):
    adopted_recipe_id: str | None = None
    decision: Literal["adopt", "no_adopt", "continue_research"]
    rationale: Annotated[str, Field(min_length=1, max_length=4000)]
    recorded_at: datetime


class ModelExplorationRun(ModelPlaygroundContract):
    schema_version: Literal["model-exploration-run/v1"] = (
        "model-exploration-run/v1"
    )
    run_id: Annotated[str, Field(min_length=1)]
    definition: ModelExplorationRunDefinition
    attempts: tuple[ModelExplorationRecipeAttempt, ...] = ()
    adoption_memo: ModelExplorationAdoptionMemo | None = None
    execution_revision: Annotated[int, Field(ge=1)] = 1
    execution_payload_digest: Digest
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def execution_payload_digest_matches(self) -> "ModelExplorationRun":
        expected = semantic_digest(
            self.model_dump(
                mode="json",
                exclude={"execution_payload_digest"},
            )
        )
        if self.execution_payload_digest != expected:
            raise ValueError("model exploration execution payload digest does not match")
        attempt_ids = [item.attempt_id for item in self.attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("attempt identities must be unique")
        for recipe_id in {item.recipe_id for item in self.attempts}:
            sequences = sorted(
                item.sequence
                for item in self.attempts
                if item.recipe_id == recipe_id
            )
            if sequences != list(range(1, len(sequences) + 1)):
                raise ValueError("attempt sequence must be append-only and contiguous")
        return self


class ModelPlaygroundContextPreview(ModelPlaygroundContract):
    context: ModelExplorationContext
    recipes: tuple[ModelExplorationRecipeSelection, ...]


class ModelExplorationRunCreateRequest(ModelPlaygroundContract):
    task_id: Annotated[str, Field(min_length=1)]
    profile_revision_id: Annotated[str, Field(min_length=1)]
    training_snapshot_id: Annotated[str, Field(min_length=1)]
    selected_recipe_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    compute_budget: ComputeBudgetPreset = "standard"


class ModelExplorationAdoptionMemoRequest(ModelPlaygroundContract):
    expected_revision: Annotated[int, Field(ge=1)]
    adopted_recipe_id: str | None = None
    decision: Literal["adopt", "no_adopt", "continue_research"]
    rationale: Annotated[str, Field(min_length=1, max_length=4000)]


class ModelExplorationAttemptRequest(ModelPlaygroundContract):
    expected_revision: Annotated[int, Field(ge=1)]


class ModelExplorationRegistrationRequest(ModelPlaygroundContract):
    expected_revision: Annotated[int, Field(ge=1)]
