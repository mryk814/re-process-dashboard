"""Contracts for selecting an explainable experiment batch from a proposal pool."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.task_contracts import ContractModel


BatchCandidateRole = Literal[
    "performance",
    "exploration",
    "boundary_check",
    "diversity",
    "coverage",
    "control",
    "replicate",
]
BatchSelectorId = Literal[
    "ranked_top_k_v1",
    "greedy_value_diversity_v1",
    "cluster_representative_v1",
    "local_penalization_v1",
    "batch_thompson_v1",
    "joint_q_acquisition_v1",
]


class BatchCategoryQuota(ContractModel):
    path: Annotated[str, Field(min_length=1)]
    value: str
    min_count: Annotated[int, Field(ge=0, le=32)] = 0
    max_count: Annotated[int | None, Field(ge=1, le=32)] = None

    @model_validator(mode="after")
    def bounds_are_consistent(self) -> "BatchCategoryQuota":
        if self.max_count is not None and self.min_count > self.max_count:
            raise ValueError("category quotaはmin_count <= max_countにします")
        return self


class BatchControlRequirement(ContractModel):
    candidate_id: Annotated[str, Field(min_length=1)]
    replicates: Annotated[int, Field(ge=1, le=8)] = 1


class BatchResourceCostRule(ContractModel):
    path: Annotated[str, Field(min_length=1)]
    value: str
    candidate_cost: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class BatchResourceConstraint(ContractModel):
    default_candidate_cost: Annotated[
        float, Field(ge=0, allow_inf_nan=False)
    ] = 1.0
    max_total_cost: Annotated[
        float | None, Field(gt=0, allow_inf_nan=False)
    ] = None
    setup_group_path: str | None = None
    max_setup_groups: Annotated[int | None, Field(ge=1, le=32)] = None
    setup_change_penalty: Annotated[
        float, Field(ge=0, allow_inf_nan=False)
    ] = 0.0
    cost_rules: tuple[BatchResourceCostRule, ...] = ()

    @model_validator(mode="after")
    def setup_contract_is_complete(self) -> "BatchResourceConstraint":
        if self.max_setup_groups is not None and not self.setup_group_path:
            raise ValueError("max_setup_groupsにはsetup_group_pathが必要です")
        return self


class BatchProposalDefinition(ContractModel):
    schema_version: Literal["batch-proposal-definition/v1"] = (
        "batch-proposal-definition/v1"
    )
    selector_id: BatchSelectorId = "greedy_value_diversity_v1"
    batch_size: Annotated[int, Field(ge=1, le=32)] = 8
    diversity_weight: Annotated[
        float, Field(ge=0, le=10, allow_inf_nan=False)
    ] = 0.75
    near_duplicate_threshold: Annotated[
        float, Field(ge=0, le=1, allow_inf_nan=False)
    ] = 0.05
    pending_candidate_ids: tuple[str, ...] = ()
    pending_policy: Literal["avoid", "penalize", "allow"] = "avoid"
    pending_penalty: Annotated[
        float, Field(ge=0, le=10, allow_inf_nan=False)
    ] = 1.0
    controls: tuple[BatchControlRequirement, ...] = ()
    category_quotas: tuple[BatchCategoryQuota, ...] = ()
    resources: BatchResourceConstraint = BatchResourceConstraint()

    @model_validator(mode="after")
    def references_are_unique_and_fit(self) -> "BatchProposalDefinition":
        if len(self.pending_candidate_ids) != len(set(self.pending_candidate_ids)):
            raise ValueError("pending candidateは重複できません")
        control_ids = [item.candidate_id for item in self.controls]
        if len(control_ids) != len(set(control_ids)):
            raise ValueError("control candidateは重複できません")
        if sum(item.replicates for item in self.controls) > self.batch_size:
            raise ValueError("control/replicate数がbatch sizeを超えています")
        quota_keys = [(item.path, item.value) for item in self.category_quotas]
        if len(quota_keys) != len(set(quota_keys)):
            raise ValueError("同じcategory quotaは重複できません")
        cost_keys = [
            (item.path, item.value) for item in self.resources.cost_rules
        ]
        if len(cost_keys) != len(set(cost_keys)):
            raise ValueError("同じresource cost ruleは重複できません")
        for quota in self.category_quotas:
            if quota.min_count > self.batch_size:
                raise ValueError("category quotaのmin_countがbatch sizeを超えています")
        return self


class BatchSelectedPoint(ContractModel):
    point_index: Annotated[int, Field(ge=0)]
    pool_index: Annotated[int, Field(ge=0)]
    order: Annotated[int, Field(ge=1)]
    role: BatchCandidateRole
    reason: Annotated[str, Field(min_length=1)]
    acquisition_component: float
    diversity_component: float
    pending_penalty: float
    resource_penalty: float
    combined_score: float
    estimated_cost: Annotated[float, Field(ge=0)]
    setup_group: str | None = None


class BatchExcludedPoint(ContractModel):
    pool_index: Annotated[int, Field(ge=0)]
    reason: Annotated[str, Field(min_length=1)]


class BatchProposalSummary(ContractModel):
    batch_size: Annotated[int, Field(ge=1)]
    min_pairwise_distance: Annotated[float, Field(ge=0)]
    mean_pairwise_distance: Annotated[float, Field(ge=0)]
    estimated_total_cost: Annotated[float, Field(ge=0)]
    setup_group_count: Annotated[int, Field(ge=0)]
    category_counts: dict[str, int]
    pending_reference_count: Annotated[int, Field(ge=0)]


class BatchProposalRun(ContractModel):
    schema_version: Literal["batch-proposal-run/v1"] = "batch-proposal-run/v1"
    selector_id: BatchSelectorId
    selector_version: Literal["1.0.0"] = "1.0.0"
    seed: Annotated[int, Field(ge=0)]
    tie_break_rule: Literal["combined_score_desc_then_pool_index_asc"] = (
        "combined_score_desc_then_pool_index_asc"
    )
    definition: BatchProposalDefinition
    selected: tuple[BatchSelectedPoint, ...]
    excluded: tuple[BatchExcludedPoint, ...]
    summary: BatchProposalSummary

    @model_validator(mode="after")
    def selected_count_matches_definition(self) -> "BatchProposalRun":
        if len(self.selected) != self.definition.batch_size:
            raise ValueError("batch selected数がdefinitionと一致しません")
        if [item.order for item in self.selected] != list(
            range(1, len(self.selected) + 1)
        ):
            raise ValueError("batch selection orderが連続していません")
        return self


class BatchSelectorDefinition(ContractModel):
    selector_id: BatchSelectorId
    version: str
    label: str
    production_enabled: bool
    requires_samples: bool = False
    requires_joint_samples: bool = False


class BatchSelectorAvailability(ContractModel):
    definition: BatchSelectorDefinition
    available: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reasons_match_availability(self) -> "BatchSelectorAvailability":
        if self.available == bool(self.reasons):
            raise ValueError("batch selector availabilityと理由が一致しません")
        return self
