"""Transport-neutral request and response contracts for Chain use cases."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from decision_workbench.contracts.blend_contracts import (
    RevisionRef,
    SparseBlendDesignSpace,
)
from decision_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainRevision,
    ChainStageLock,
    StageContractSurface,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ChainCandidateInputDefinition,
    IntermediateActualRecord,
)
from decision_workbench.contracts.candidate_project_contracts import CandidateInput


class ChainApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChainTemplateItem(ChainApiModel):
    definition_id: str
    definition: ChainDefinition
    revisions: tuple[ChainRevision, ...]


class ChainStudioStageCatalogItem(ChainApiModel):
    """One server-resolved task that may be used by the scalar Chain editor."""

    stage_kind: Literal["task"] = "task"
    contract_id: str
    label: str
    status: Literal["available", "unavailable"]
    reason: str | None = None
    surface: StageContractSurface | None = None
    stage_lock: ChainStageLock | None = None


class ChainStudioCatalogResponse(ChainApiModel):
    adapter_id: Literal["scalar/v1"] = "scalar/v1"
    stages: tuple[ChainStudioStageCatalogItem, ...]


class ChainStudioDraftRequest(ChainApiModel):
    """A complete, still-unpublished Definition. Layout is deliberately absent."""

    definition: ChainDefinition


class ChainStudioDraftValidation(ChainApiModel):
    valid: bool
    definition_digest: str
    message: str


class ChainGraphStageContract(ChainApiModel):
    stage_id: str
    status: Literal["available", "unavailable"]
    reason: str | None = None
    surface: StageContractSurface | None = None


class ChainGraphResponse(ChainApiModel):
    definition: ChainDefinition
    revision: ChainRevision
    stage_contracts: tuple[ChainGraphStageContract, ...]


class ChainExecutionRequest(ChainApiModel):
    candidate_revision: int = Field(ge=1)
    request_id: str | None = None
    debounce_ms: int = Field(default=250, ge=0, le=1000)


class ChainDistributionRequest(ChainApiModel):
    candidate_revision: int = Field(ge=1)
    seed: int = Field(default=20260725, ge=0, le=2_147_483_647)
    sample_count: int = Field(default=512, ge=32, le=4096)


class ActualConditionedVariantRequest(ChainApiModel):
    candidate_revision: int = Field(ge=1)
    comparison_snapshot_id: str = Field(min_length=1)
    actual_records: tuple[IntermediateActualRecord, ...] = Field(min_length=1)


class ChainCandidateContractResponse(ChainApiModel):
    transform_id: str
    scientific_master: RevisionRef
    commercial_catalog: RevisionRef
    design_space: SparseBlendDesignSpace
    design_space_ref: RevisionRef
    external_inputs: tuple[ChainCandidateInputDefinition, ...]
    starter_candidate: CandidateInput
