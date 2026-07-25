"""Execution and immutable evidence contracts for a fixed Chain Revision."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.chain_contracts import (
    ChainContractModel,
    ChainSnapshotIdentity,
)


ChainStageFreshness = Literal["latest", "running", "stale", "failed"]


class ChainStageExecution(ChainContractModel):
    stage_id: Annotated[str, Field(min_length=1)]
    status: ChainStageFreshness
    requested_input_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    result_input_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ] | None = None
    contract_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    package_manifest_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    canonical_input: dict[str, Any]
    result: dict[str, Any] | None = None
    cache_hit: bool = False
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def state_has_coherent_evidence(self) -> "ChainStageExecution":
        if self.status == "latest":
            if self.result is None or self.result_input_digest != self.requested_input_digest:
                raise ValueError("latest Chain stage must contain the requested result")
            if self.error is not None:
                raise ValueError("latest Chain stage cannot contain an error")
        if self.status == "running" and self.completed_at is not None:
            raise ValueError("running Chain stage cannot be completed")
        if self.status == "failed" and not self.error:
            raise ValueError("failed Chain stage requires an error")
        if self.result is None and self.result_input_digest is not None:
            raise ValueError("result input digest requires a retained result")
        return self


class ChainExecution(ChainContractModel):
    schema_version: Literal["chain-execution/v1"] = "chain-execution/v1"
    request_id: Annotated[str, Field(min_length=1)]
    project_id: Annotated[str, Field(min_length=1)]
    candidate_id: Annotated[str, Field(min_length=1)]
    candidate_revision: Annotated[int, Field(ge=1)]
    chain_revision_id: Annotated[str, Field(min_length=1)]
    chain_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    status: Literal["running", "latest", "failed", "superseded"]
    stages: Annotated[tuple[ChainStageExecution, ...], Field(min_length=1)]
    created_at: datetime
    updated_at: datetime


class ChainSnapshot(ChainContractModel):
    schema_version: Literal["chain-snapshot/v1"] = "chain-snapshot/v1"
    snapshot_id: Annotated[str, Field(min_length=1)]
    identity: ChainSnapshotIdentity
    request_id: Annotated[str, Field(min_length=1)]
    external_input: dict[str, Any]
    stages: Annotated[tuple[ChainStageExecution, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def snapshot_contains_only_successful_stage_results(self) -> "ChainSnapshot":
        if any(stage.status != "latest" for stage in self.stages):
            raise ValueError("immutable Chain snapshot requires latest results for every stage")
        return self
