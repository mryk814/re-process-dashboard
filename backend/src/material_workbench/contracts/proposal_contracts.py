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


class ProposalIncumbentResolution(ContractModel):
    """Immutable evidence for the incumbent value used by an acquisition."""

    schema_version: Literal["proposal-incumbent-resolution/v1"] = (
        "proposal-incumbent-resolution/v1"
    )
    source: Literal[
        "none",
        "request_override",
        "objective_candidate_revision",
        "objective_prediction_snapshot",
        "objective_project_decision",
        "observed_project_actuals",
    ]
    objective_source: Literal[
        "explicit", "project_revision", "legacy_screening"
    ]
    target: str
    unit: str
    direction: Literal["at_least", "at_most", "between"] | None = None
    value: float | None = Field(default=None, allow_inf_nan=False)
    candidate_id: str | None = None
    candidate_revision: int | None = None
    snapshot_id: str | None = None
    actual_id: str | None = None
    filter_digest: str | None = None
    population_digest: str | None = None
    record_count: Annotated[int | None, Field(ge=0)] = None

    @model_validator(mode="after")
    def evidence_matches_source(self) -> "ProposalIncumbentResolution":
        if self.source == "none":
            if self.value is not None:
                raise ValueError("incumbentなしには値を保存できません")
            return self
        if self.value is None:
            raise ValueError("解決済みincumbentには値が必要です")
        if self.source == "objective_candidate_revision" and (
            not self.candidate_id or self.candidate_revision is None
        ):
            raise ValueError("candidate revision由来のincumbent参照が不足しています")
        if self.source in {
            "objective_prediction_snapshot",
            "objective_project_decision",
        } and (not self.candidate_id or not self.snapshot_id):
            raise ValueError("snapshot由来のincumbent参照が不足しています")
        if self.source == "observed_project_actuals" and (
            self.direction is None
            or
            not self.actual_id
            or not self.candidate_id
            or not self.snapshot_id
            or not self.filter_digest
            or not self.population_digest
            or self.record_count is None
            or self.record_count < 1
        ):
            raise ValueError("Project実測由来のincumbent証跡が不足しています")
        return self


class ProposalObjectiveExecution(ContractModel):
    """The production Objective subset actually translated into engine fields."""

    schema_version: Literal["proposal-objective-execution/v1"] = (
        "proposal-objective-execution/v1"
    )
    objective_digest: str
    target: str
    direction: Literal["at_least", "at_most", "between"] | None = None
    hard_constraint_outputs: tuple[str, ...] = ()
    reporting_outputs: tuple[str, ...] = ()


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
