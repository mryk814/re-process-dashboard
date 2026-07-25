"""Execution and immutable evidence contracts for a fixed Chain Revision."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator, model_validator

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
    status: Literal["running", "latest", "stale", "failed", "superseded"]
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


class IntermediateActualRecord(ChainContractModel):
    """One traceable source record contributing measured Stage B values."""

    actual_id: Annotated[str, Field(min_length=1)]
    values: Annotated[dict[str, float], Field(min_length=1)]

    @field_validator("values")
    @classmethod
    def values_are_finite(cls, values: dict[str, float]) -> dict[str, float]:
        invalid = sorted(key for key, value in values.items() if not math.isfinite(value))
        if invalid:
            raise ValueError(f"実測値は有限値にしてください: {', '.join(invalid)}")
        return values


class ActualConditionedVariantIdentity(ChainContractModel):
    schema_version: Literal["actual-conditioned-variant-identity/v1"] = (
        "actual-conditioned-variant-identity/v1"
    )
    base_chain_revision_id: Annotated[str, Field(min_length=1)]
    base_chain_revision_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    base_candidate_id: Annotated[str, Field(min_length=1)]
    base_candidate_revision: Annotated[int, Field(ge=1)]
    comparison_snapshot_id: Annotated[str, Field(min_length=1)]
    actual_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    measurement_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    coverage: Annotated[tuple[str, ...], Field(min_length=1)]
    stage_c_package_manifest_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]


class ActualConditionedVariant(ChainContractModel):
    """Immutable C-only analysis. It never replaces normal A -> B -> C output."""

    schema_version: Literal["actual-conditioned-variant/v1"] = (
        "actual-conditioned-variant/v1"
    )
    variant_id: Annotated[str, Field(min_length=1)]
    project_id: Annotated[str, Field(min_length=1)]
    identity: ActualConditionedVariantIdentity
    source: Literal["actual"] = "actual"
    measured_stage_b: dict[str, float]
    stage_c_input: dict[str, Any]
    stage_c_result: dict[str, Any]
    created_at: datetime
