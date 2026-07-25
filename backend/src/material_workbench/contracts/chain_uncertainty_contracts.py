"""Explicit Monte Carlo propagation contracts for a fixed Chain result."""
from __future__ import annotations

from datetime import datetime
import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.chain_contracts import ChainContractModel


class StageSamplingCapability(ChainContractModel):
    schema_version: Literal["stage-sampling-capability/v1"] = (
        "stage-sampling-capability/v1"
    )
    supported: bool
    method: str | None = None
    method_label: str | None = None
    output_dependence: Literal["deterministic", "independent", "joint"] | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def supported_capability_has_a_method(self) -> "StageSamplingCapability":
        if self.supported and (
            not self.method
            or not self.method_label
            or self.output_dependence is None
            or self.reason is not None
        ):
            raise ValueError("supported sampling capability requires only a method")
        if not self.supported and (
            self.method is not None
            or self.method_label is not None
            or self.output_dependence is not None
            or not self.reason
        ):
            raise ValueError("unsupported sampling capability requires only a reason")
        return self


class StageSampleResult(ChainContractModel):
    """Validated output of the optional runtime sample protocol."""

    schema_version: Literal["stage-sample-result/v1"] = "stage-sample-result/v1"
    method: Annotated[str, Field(min_length=1)]
    sample_count: Annotated[int, Field(ge=2, le=4096)]
    outputs: dict[Annotated[str, Field(min_length=1)], tuple[float, ...]]

    @model_validator(mode="after")
    def outputs_are_finite_and_aligned(self) -> "StageSampleResult":
        if not self.outputs:
            raise ValueError("stage sample result requires outputs")
        for key, values in self.outputs.items():
            if len(values) != self.sample_count:
                raise ValueError(
                    f"stage sample output {key!r} length does not match sample_count"
                )
            if any(not math.isfinite(value) for value in values):
                raise ValueError(f"stage sample output {key!r} contains non-finite values")
        return self


class DistributionSummary(ChainContractModel):
    mean: float
    standard_deviation: Annotated[float, Field(ge=0)]
    quantiles: dict[Literal["0.05", "0.50", "0.95"], float]
    sample_count: Annotated[int, Field(ge=2)]


class ChainStageSamplingCapability(ChainContractModel):
    stage_id: Annotated[str, Field(min_length=1)]
    package_manifest_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    capability: StageSamplingCapability


class ChainDistributionCapability(ChainContractModel):
    schema_version: Literal["chain-distribution-capability/v1"] = (
        "chain-distribution-capability/v1"
    )
    chain_revision_id: Annotated[str, Field(min_length=1)]
    chain_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    explicit_run_available: bool
    full_propagation_supported: bool
    stages: Annotated[
        tuple[ChainStageSamplingCapability, ...], Field(min_length=1)
    ]


class ChainStageUncertainty(ChainContractModel):
    stage_id: Annotated[str, Field(min_length=1)]
    capability: StageSamplingCapability
    package_manifest_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    point_estimates: dict[str, float]
    # Model/residual uncertainty of this stage at fixed point inputs.
    stage_uncertainty: dict[str, DistributionSummary] = Field(default_factory=dict)
    # The same output after all sample-capable upstream stages are propagated.
    propagated_uncertainty: dict[str, DistributionSummary] = Field(default_factory=dict)
    seed: int | None = None

    @model_validator(mode="after")
    def unsupported_stage_has_no_distribution(self) -> "ChainStageUncertainty":
        if not self.capability.supported and (
            self.stage_uncertainty or self.propagated_uncertainty or self.seed is not None
        ):
            raise ValueError("unsupported stage cannot contain sampled distributions")
        return self


class ChainDistributionProvenance(ChainContractModel):
    algorithm: Literal["forward-monte-carlo/v1"] = "forward-monte-carlo/v1"
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)]
    sample_count: Annotated[int, Field(ge=32, le=4096)]
    chain_revision_id: Annotated[str, Field(min_length=1)]
    chain_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    candidate_id: Annotated[str, Field(min_length=1)]
    candidate_revision: Annotated[int, Field(ge=1)]
    point_execution_request_id: Annotated[str, Field(min_length=1)]


class ChainDistributionRun(ChainContractModel):
    schema_version: Literal["chain-distribution-run/v1"] = (
        "chain-distribution-run/v1"
    )
    run_id: Annotated[str, Field(min_length=1)]
    project_id: Annotated[str, Field(min_length=1)]
    status: Literal["completed", "unsupported"]
    provenance: ChainDistributionProvenance
    stages: Annotated[tuple[ChainStageUncertainty, ...], Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def status_matches_stage_capabilities(self) -> "ChainDistributionRun":
        all_supported = all(
            stage.capability.supported or not stage.stage_uncertainty
            for stage in self.stages
        )
        if not all_supported:
            raise ValueError("unsupported stages cannot carry distributions")
        has_unsupported_predictive = any(
            not stage.capability.supported and stage.point_estimates
            for stage in self.stages
        )
        if (self.status == "unsupported") != has_unsupported_predictive:
            raise ValueError("run status must report unsupported predictive stages")
        return self
