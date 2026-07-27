from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
JsonObject = dict[str, Any]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Actor(ContractModel):
    actor_id: Identifier
    actor_kind: Literal["human", "ai_agent", "service"]
    label: Annotated[str, Field(min_length=1, max_length=160)]
    workspace_id: Identifier
    capabilities: tuple[str, ...] = ()
    created_at: datetime


class SharedContext(ContractModel):
    mode: Literal["shared"] = "shared"
    workspace_id: Identifier
    actor: Actor
    request_id: Identifier


class ProjectCreate(ContractModel):
    project_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=200)]


class Project(ContractModel):
    project_id: Identifier
    workspace_id: Identifier
    name: str
    created_by: Identifier
    created_at: datetime


class CandidateCreate(ContractModel):
    candidate_id: Identifier
    name: Annotated[str, Field(min_length=1, max_length=200)]
    payload: JsonObject


class CandidateUpdate(ContractModel):
    expected_revision: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    payload: JsonObject


class CandidateRevision(ContractModel):
    project_id: Identifier
    candidate_id: Identifier
    revision: int
    name: str
    payload: JsonObject
    created_by: Identifier
    created_at: datetime


class ActivityRunCreate(ContractModel):
    run_id: Identifier
    candidate_id: Identifier
    candidate_revision: Annotated[int, Field(ge=1)]
    activity_id: Identifier
    payload: JsonObject


class ActivityRun(ContractModel):
    run_id: Identifier
    project_id: Identifier
    candidate_id: Identifier
    candidate_revision: int
    activity_id: Identifier
    payload: JsonObject
    created_by: Identifier
    created_at: datetime


class ArtifactReference(ContractModel):
    artifact_id: Identifier
    project_id: Identifier
    object_key: str
    content_digest: Digest
    content_type: str
    size_bytes: int
    metadata: JsonObject
    created_by: Identifier
    status: Literal["ready"]
    verified_at: datetime
    created_at: datetime


class AuditEvent(ContractModel):
    event_id: Identifier
    workspace_id: Identifier
    actor_id: Identifier
    project_id: Identifier | None
    target_type: str
    target_id: str
    operation: str
    outcome: Literal["succeeded", "conflict", "failed"]
    expected_revision: int | None
    resulting_revision: int | None
    request_id: Identifier
    correlation_id: Identifier
    detail: JsonObject
    created_at: datetime


class ApiError(ContractModel):
    code: Literal[
        "not_found",
        "identity_missing",
        "identity_invalid",
        "capability_denied",
        "resource_conflict",
        "revision_conflict",
        "artifact_unavailable",
        "artifact_digest_mismatch",
        "persistence_unavailable",
        "object_storage_unavailable",
        "validation_error",
    ]
    message: str
    current_candidate: CandidateRevision | None = None
