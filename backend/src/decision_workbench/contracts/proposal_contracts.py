"""Allow-listed proposal strategy contracts."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import ContractModel
from decision_workbench.contracts.model_capability_contracts import CapabilityRequirement


AcquisitionRepresentation = Literal[
    "normal_mean_std",
    "posterior_samples",
    "parametric_distribution",
    "unsupported",
]
ProposalGeneratorId = Literal[
    "latin_hypercube",
    "sobol",
    "bounded_simplex_hit_and_run",
]
ProposalDistanceId = Literal[
    "scalar_axis_rms",
    "group_weighted_bounded_clr_rms",
]
ProposalSelectionPolicyId = Literal[
    "ranked_top_k_v1",
    "greedy_value_diversity_v1",
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
    proposal_count: Annotated[int, Field(ge=1, le=10)] = 5
    selection_policy: ProposalSelectionPolicyId = "ranked_top_k_v1"
    diversity_weight: Annotated[
        float, Field(ge=0, le=10, allow_inf_nan=False)
    ] = 0.75


class ProposalStrategyDefinition(ContractModel):
    strategy_id: str
    version: str
    label: str
    generator_id: ProposalGeneratorId
    generator_version: str
    generator_parameters: dict[str, float | str | bool] = Field(default_factory=dict)
    distance_id: ProposalDistanceId = "scalar_axis_rms"
    distance_version: str = "1.0.0"
    distance_parameters: dict[str, float | str | bool] = Field(default_factory=dict)
    distance_usage: Literal["batch_selector_only"] = "batch_selector_only"
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
    # The legacy booleans above remain readable in stored Proposal Runs.  New
    # availability decisions are made only from this shared capability contract.
    required_capabilities: tuple[CapabilityRequirement, ...] = ()
    production_enabled: bool = True
    lifecycle_status: Literal[
        "experimental",
        "production",
        "unavailable",
        "no_adopt",
        "retired",
    ] = "production"

    @model_validator(mode="after")
    def lifecycle_matches_production_gate(self) -> "ProposalStrategyDefinition":
        if self.production_enabled != (self.lifecycle_status == "production"):
            raise ValueError("strategy lifecycleとproduction gateが一致しません")
        return self


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


class ProposalSelectedPoint(ContractModel):
    point_index: Annotated[int, Field(ge=0)]
    pool_index: Annotated[int, Field(ge=0)]
    order: Annotated[int, Field(ge=1)]
    acquisition_component: float = Field(allow_inf_nan=False)
    diversity_component: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    combined_score: float = Field(allow_inf_nan=False)
    canonical_identity_digest: Annotated[str, Field(pattern=r"^sha256:")]


class ProposalSelectionEvidence(ContractModel):
    """Immutable evidence for the shortlist shown as proposed candidates."""

    schema_version: Literal["proposal-selection/v1"] = "proposal-selection/v1"
    requested_count: Annotated[int, Field(ge=1, le=10)]
    actual_count: Annotated[int, Field(ge=0, le=10)]
    eligible_count: Annotated[int, Field(ge=0)]
    unique_count: Annotated[int, Field(ge=0)]
    policy_id: ProposalSelectionPolicyId
    policy_version: Annotated[str, Field(min_length=1)]
    tie_break_rule: Literal[
        "combined_score_desc_then_pool_index_asc"
    ]
    value_component_identity: Literal["acquisition_rank_utility"]
    candidate_pool_digest: Annotated[str, Field(pattern=r"^sha256:")]
    distance_id: ProposalDistanceId
    distance_version: Annotated[str, Field(min_length=1)]
    distance_parameters: dict[str, float | str | bool] = Field(default_factory=dict)
    requested_diversity_weight: Annotated[
        float, Field(ge=0, le=10, allow_inf_nan=False)
    ]
    effective_diversity_weight: Annotated[
        float, Field(ge=0, le=10, allow_inf_nan=False)
    ]
    near_duplicate_threshold: Annotated[
        float, Field(ge=0, le=1, allow_inf_nan=False)
    ]
    selected: tuple[ProposalSelectedPoint, ...]
    shortfall_reason: str | None = None

    @model_validator(mode="after")
    def count_matches_selection(self) -> "ProposalSelectionEvidence":
        if self.actual_count != len(self.selected):
            raise ValueError("proposal actual_countがselected件数と一致しません")
        if self.actual_count > self.requested_count:
            raise ValueError("proposal actual_countがrequested_countを超えています")
        if self.unique_count > self.eligible_count:
            raise ValueError("proposal unique_countがeligible_countを超えています")
        if self.actual_count > self.unique_count:
            raise ValueError("proposal actual_countがunique_countを超えています")
        if self.actual_count < self.requested_count and not self.shortfall_reason:
            raise ValueError("proposal不足時は理由を保存してください")
        if self.actual_count == self.requested_count and self.shortfall_reason:
            raise ValueError("proposal件数を満たす場合は不足理由を保存しません")
        if [item.order for item in self.selected] != list(
            range(1, self.actual_count + 1)
        ):
            raise ValueError("proposal selection orderが連続していません")
        if (
            self.policy_id == "ranked_top_k_v1"
            and self.effective_diversity_weight != 0
        ):
            raise ValueError("上位順ではdiversity weightを適用しません")
        if (
            self.policy_id == "greedy_value_diversity_v1"
            and self.effective_diversity_weight
            != self.requested_diversity_weight
        ):
            raise ValueError("多様性policyのrequested/effective weightが一致しません")
        return self
