"""Transport-neutral request and response contracts for Chain use cases."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from decision_workbench.contracts.blend_contracts import (
    RevisionRef,
    SparseBlendDesignSpace,
)
from decision_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainStageLock,
    GraphDefinitionRef,
    GraphRevisionRef,
    PredictionGraphDefinition,
    PredictionGraphProjection,
    PredictionGraphRevision,
    StageContractSurface,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ChainCandidateInputDefinition,
    IntermediateActualRecord,
)
from decision_workbench.contracts.candidate_project_contracts import (
    CandidateInput,
    ProjectCreateInput,
)


class ChainApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChainTemplateItem(ChainApiModel):
    definition_id: str
    definition: GraphDefinitionRef
    revisions: tuple[GraphRevisionRef, ...]
    is_default: bool
    default_revision_id: str | None = None
    latest_revision_id: str | None = None


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
    definition: GraphDefinitionRef
    revision: GraphRevisionRef
    prediction_graph: PredictionGraphProjection
    stage_contracts: tuple[ChainGraphStageContract, ...]


class ChainExecutionRequest(ChainApiModel):
    candidate_revision: int = Field(ge=1)
    request_id: str | None = None
    debounce_ms: int = Field(default=250, ge=0, le=1000)


class PredictionGraphProjectCreateRequest(ChainApiModel):
    project: ProjectCreateInput
    graph_revision_id: str = Field(min_length=1)
    graph_revision_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    project_binding_revision: int = Field(default=1, ge=1)
    project_binding_values: dict[str, float | str] = Field(default_factory=dict)


class PredictionGraphStageCatalogItem(ChainApiModel):
    stage_kind: Literal["task", "deterministic_transform"]
    contract_id: str
    label: str
    status: Literal["available", "unavailable"]
    reason: str | None = None
    surface: StageContractSurface | None = None
    stage_lock: ChainStageLock | None = None


class PredictionGraphCatalogResponse(ChainApiModel):
    candidate_adapter_ids: tuple[Literal["scalar/v1", "sparse_blend/v1"], ...]
    stages: tuple[PredictionGraphStageCatalogItem, ...]


class PredictionGraphValidationTarget(ChainApiModel):
    target_kind: Literal[
        "graph",
        "stage",
        "input",
        "binding",
        "decision_output",
    ]
    target_id: str
    port_path: str | None = None


class PredictionGraphValidationFinding(ChainApiModel):
    code: Literal[
        "unbound_required_input",
        "port_mismatch",
        "unknown_stage_contract",
        "unavailable_stage_contract",
        "unsupported_candidate_adapter",
        "cycle",
        "terminal_output_missing",
        "fixed_parameter_missing",
        "invalid_graph",
    ]
    message: str
    target: PredictionGraphValidationTarget
    suggested_action: str


class PredictionGraphDraftValidationRequest(ChainApiModel):
    """Raw draft payload so invalid topology can return structured findings."""

    definition: dict[str, Any]


class PredictionGraphDraftValidation(ChainApiModel):
    valid: bool
    definition_digest: str
    candidate_adapter_id: Literal["scalar/v1", "sparse_blend/v1"] | None = None
    findings: tuple[PredictionGraphValidationFinding, ...] = ()


class PredictionGraphPublishRequest(ChainApiModel):
    definition: PredictionGraphDefinition


class PredictionGraphPublishResponse(ChainApiModel):
    definition: PredictionGraphDefinition
    graph_revision_id: Annotated[str, Field(min_length=1)]
    revision: PredictionGraphRevision


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
