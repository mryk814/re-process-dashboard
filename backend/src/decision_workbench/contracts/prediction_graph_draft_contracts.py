"""Mutable Prediction Graph authoring contracts.

Drafts deliberately do not reuse ``PredictionGraphDefinition`` validation:
an author must be able to save an incomplete graph before it is publishable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from decision_workbench.contracts.chain_contracts import (
    ChainBinding,
    ChainPort,
    ChainStage,
    DecisionOutputEvidence,
    GraphInputSource,
)


class PredictionGraphDraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PredictionGraphDraftInput(PredictionGraphDraftModel):
    input_id: str = Field(min_length=1)
    label: str
    port: ChainPort
    role: Literal["design_variable", "scenario_context", "fixed_parameter"]
    value_source: GraphInputSource
    required: bool = True
    default_presentation_group: str


class PredictionGraphDraftDecisionOutput(PredictionGraphDraftModel):
    output_id: str = Field(min_length=1)
    source_stage_id: str = Field(min_length=1)
    source_output_key: str = Field(min_length=1)
    label: str
    group: str
    role: Literal[
        "primary_objective",
        "hard_constraint",
        "secondary_outcome",
        "diagnostic",
    ]
    required_for_complete_result: bool
    evidence: DecisionOutputEvidence | None = None


class PredictionGraphDraftDefinition(PredictionGraphDraftModel):
    """Syntactically typed graph content without publish-time completeness checks."""

    schema_version: Literal["prediction-graph-definition/v1"] = (
        "prediction-graph-definition/v1"
    )
    graph_id: str
    label: str
    stages: tuple[ChainStage, ...] = ()
    inputs: tuple[PredictionGraphDraftInput, ...] = ()
    bindings: tuple[ChainBinding, ...] = ()
    decision_outputs: tuple[PredictionGraphDraftDecisionOutput, ...] = ()


class PredictionGraphDraftContent(PredictionGraphDraftModel):
    schema_version: Literal["prediction-graph-draft-content/v1"] = (
        "prediction-graph-draft-content/v1"
    )
    definition: PredictionGraphDraftDefinition
    project_name: str = ""


class PredictionGraphDraftDocument(PredictionGraphDraftModel):
    schema_version: Literal["prediction-graph-draft/v1"] = (
        "prediction-graph-draft/v1"
    )
    draft_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    content: PredictionGraphDraftContent
    created_at: datetime
    updated_at: datetime


class PredictionGraphDraftCreateRequest(PredictionGraphDraftModel):
    content: PredictionGraphDraftContent


class PredictionGraphDraftUpdateRequest(PredictionGraphDraftModel):
    expected_version: int = Field(ge=1)
    content: PredictionGraphDraftContent


class PredictionGraphDraftConflictResponse(PredictionGraphDraftModel):
    code: Literal["revision_conflict"] = "revision_conflict"
    message: str
    current: PredictionGraphDraftDocument
