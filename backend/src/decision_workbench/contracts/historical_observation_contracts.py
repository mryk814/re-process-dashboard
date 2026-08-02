from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from decision_workbench.contracts.candidate_project_contracts import Candidate, CandidateInputs
from decision_workbench.contracts.task_contracts import HistoricalObservationReference


class HistoricalObservationRecord(BaseModel):
    """A selectable flat-table record resolved from the Project's fixed data."""

    observation_id: Annotated[str, Field(min_length=1)]
    parent_key: Annotated[str, Field(min_length=1)]
    source_label: Annotated[str, Field(min_length=1)]
    inputs: CandidateInputs
    actual_outputs: dict[str, float]
    candidate_eligible: bool
    candidate_reason: str | None = None


class HistoricalObservationListResponse(BaseModel):
    dataset_view_revision_id: str
    source_sha256: str
    available: bool
    reason: str | None = None
    records: list[HistoricalObservationRecord] = Field(default_factory=list)


class HistoricalObservationEvidence(BaseModel):
    candidate_id: str
    reference: HistoricalObservationReference
    inputs: CandidateInputs
    actual_outputs: dict[str, float]


class HistoricalObservationCandidateResponse(BaseModel):
    candidate: Candidate
    evidence: HistoricalObservationEvidence
