"""Allow-listed proposal strategy contracts."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.task_contracts import ContractModel


AcquisitionRepresentation = Literal[
    "normal_mean_std",
    "posterior_samples",
    "parametric_distribution",
    "unsupported",
]


class ProposalStrategyRequest(ContractModel):
    strategy_id: str = "latin_hypercube_v1"
    exploration_parameter: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 2.0
    pool_multiplier: Annotated[int, Field(ge=2, le=16)] = 4
    support_policy: Literal[
        "supported_first",
        "exclude_extrapolated",
        "allow_with_warning",
    ] = "supported_first"
    fallback_policy: Literal["reject", "deterministic_goal"] = "reject"
    incumbent_value: float | None = Field(default=None, allow_inf_nan=False)


class ProposalStrategyDefinition(ContractModel):
    strategy_id: str
    version: str
    label: str
    generator_id: Literal["latin_hypercube", "sobol"]
    generator_version: str
    acquisition_id: Literal[
        "goal_achievement",
        "upper_confidence_bound",
        "expected_improvement",
        "thompson_sampling",
        "uncertainty_sampling",
        "support_boundary_sampling",
    ]
    acquisition_version: str
    selector_id: Literal["ranked_top_k"]
    selector_version: str
    requires_standard_deviation: bool = False
    requires_samples: bool = False
    requires_joint_samples: bool = False
    requires_incumbent: bool = False
    requires_acquisition_representation: AcquisitionRepresentation | None = None
    production_enabled: bool = True


class ProposalStrategyAvailability(ContractModel):
    definition: ProposalStrategyDefinition
    target_acquisition_representations: tuple[AcquisitionRepresentation, ...]
    available: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reasons_match_availability(self) -> "ProposalStrategyAvailability":
        if self.available == bool(self.reasons):
            raise ValueError("strategy availabilityと理由が一致しません")
        return self


class ProposalCandidateEvaluation(ContractModel):
    pool_index: Annotated[int, Field(ge=0)]
    inputs: dict[str, float | str]
    acquisition_score: float
    acquisition_components: dict[str, float | str | bool | None]
    support_status: Literal["supported", "caution", "extrapolated"]
    selected_rank: int | None = None
    exclusion_reason: str | None = None


class ProposalRejectedCandidate(ContractModel):
    pool_index: Annotated[int, Field(ge=0)]
    inputs: dict[str, float | str]
    reason: Annotated[str, Field(min_length=1)]
