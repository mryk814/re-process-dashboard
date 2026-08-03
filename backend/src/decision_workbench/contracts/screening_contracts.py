from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from decision_workbench.contracts.batch_proposal_contracts import BatchProposalRun
from decision_workbench.contracts.candidate_project_contracts import Candidate, CandidateInput, CandidateInputs
from decision_workbench.contracts.objective_contracts import ObjectiveDefinition
from decision_workbench.contracts.prediction_catalog_contracts import (
    ModelMetadata,
    Prediction,
    SCREENING_POOL_MULTIPLIER,
    ScreeningGoal,
    ScreeningVariable,
    SimilarObservation,
    Support,
)
from decision_workbench.contracts.proposal_contracts import (
    ProposalCandidateEvaluation,
    ProposalIncumbentResolution,
    ProposalObjectiveExecution,
    ProposalRejectedCandidate,
    ProposalSelectionEvidence,
)
from decision_workbench.contracts.sampling_identity_contracts import SamplingRequest
from decision_workbench.design_priors.contracts import (
    DesignPriorPackageReference,
    DesignPriorSampleEvidence,
)

class ScreeningPoint(BaseModel):
    index: int
    inputs: dict[str, float | str]
    candidate: CandidateInput
    prediction: Prediction
    predictions: dict[str, Prediction] = Field(default_factory=dict)
    color_value: float
    support: Support
    warnings: list[str] = Field(default_factory=list)
    similar: list[SimilarObservation] = Field(default_factory=list)
    score: float | None
    goal_evaluation: "ScreeningGoalEvaluation"
    secondary_goal_evaluations: dict[str, "ScreeningGoalEvaluation"] = Field(default_factory=dict)
    design_prior_evidence: DesignPriorSampleEvidence | None = None


class ScreeningGoalEvaluation(BaseModel):
    score: float | None
    method: Literal["achievement_probability", "directional_shortfall", "range_shortfall", "absolute_distance", "support_distance"]
    achieved: bool | None
    achievement_probability: float | None


class ScreeningScoreContract(BaseModel):
    version: Literal["screening-score/v1", "screening-score/v2", "screening-score/v3"]
    preference: Literal["lower_is_better"]
    direction: Literal["at_least", "at_most", "between", "target"] | None
    target_value: float | None
    lower: float | None = None
    upper: float | None = None
    probability_available: bool
    probability_semantics: Literal["probability_of_achieving_goal"] | None = None
    ranking_policy: Literal["support_tier_then_secondary_goals_then_score"] | None = None
    fallback: Literal["directional_shortfall", "range_shortfall", "absolute_distance", "support_distance"]
    display_label: str


class ScreeningProposalStrategy(BaseModel):
    id: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    runtime_capability_digest: Annotated[
        str | None, Field(pattern=r"^sha256:")
    ] = None
    lifecycle_status: Literal[
        "experimental",
        "production",
        "unavailable",
        "no_adopt",
        "retired",
    ] = "production"
    required_capabilities: tuple[str, ...] = ()
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)]
    requested_count: Annotated[int, Field(ge=1)]
    pool_multiplier: Annotated[int, Field(ge=1)] = SCREENING_POOL_MULTIPLIER
    generator_id: str = "latin_hypercube"
    generator_version: str = "1.0.0"
    generator_parameters: dict[str, float | str | bool] = Field(default_factory=dict)
    distance_id: str = "scalar_axis_rms"
    distance_version: str = "1.0.0"
    distance_parameters: dict[str, float | str | bool] = Field(default_factory=dict)
    distance_usage: Literal["batch_selector_only"] = "batch_selector_only"
    acquisition_id: str = "goal_achievement"
    acquisition_version: str = "1.0.0"
    selector_id: str = "ranked_top_k"
    selector_version: str = "1.0.0"
    exploration_parameter: float | None = None
    parameter_role: Literal["confidence_multiplier", "improvement_margin"] | None = None
    acquisition_representation: Literal["normal_mean_std"] | None = None
    standard_deviation_methods: tuple[str, ...] = ()
    support_policy: Literal[
        "supported_first", "exclude_extrapolated", "allow_with_warning"
    ] = "supported_first"
    fallback_from: str | None = None
    fallback_policy: Literal["reject", "deterministic_goal"] = "reject"
    incumbent_value: float | None = None
    incumbent_resolution: ProposalIncumbentResolution | None = None
    constraint_treatment: Literal[
        "feasibility_first_then_rank"
    ] = "feasibility_first_then_rank"
    uncertainty_treatment: Literal["predictive_standard_deviation"] | None = None


class ProposalCoverageEvidence(BaseModel):
    observed_min: float = Field(allow_inf_nan=False)
    observed_max: float = Field(allow_inf_nan=False)
    observed_mean: float = Field(allow_inf_nan=False)
    normalized_span: float = Field(ge=0, le=1, allow_inf_nan=False)


class ScreeningProposalDiagnostics(BaseModel):
    generated_count: Annotated[int, Field(ge=1)]
    valid_count: Annotated[int, Field(ge=0)]
    evaluated_count: Annotated[int, Field(ge=0)]
    rejected_count: Annotated[int, Field(ge=0)]
    rejection_rate: Annotated[float, Field(ge=0, le=1)]
    rejected_by_reason: dict[str, Annotated[int, Field(ge=1)]] = Field(default_factory=dict)
    selected_count: Annotated[int, Field(ge=0)] = 0
    displayed_count: Annotated[int | None, Field(ge=0)] = None
    proposed_count: Annotated[int | None, Field(ge=0)] = None
    model_call_count: Annotated[int | None, Field(ge=0)] = None
    runtime_ms: Annotated[
        float | None, Field(ge=0, allow_inf_nan=False)
    ] = None
    memory_peak_bytes: Annotated[int | None, Field(ge=0)] = None
    coverage_by_path: dict[str, ProposalCoverageEvidence] = Field(default_factory=dict)

    @model_validator(mode="after")
    def counts_share_one_denominator(self) -> "ScreeningProposalDiagnostics":
        if self.valid_count + self.rejected_count != self.generated_count:
            raise ValueError("valid_count + rejected_count must equal generated_count")
        if self.evaluated_count > self.valid_count:
            raise ValueError("evaluated_count must not exceed valid_count")
        if self.selected_count > self.evaluated_count:
            raise ValueError("selected_count must not exceed evaluated_count")
        if sum(self.rejected_by_reason.values()) != self.rejected_count:
            raise ValueError("rejected_by_reason must sum to rejected_count")
        expected_rate = self.rejected_count / self.generated_count
        if not math.isclose(self.rejection_rate, expected_rate, rel_tol=0, abs_tol=1e-12):
            raise ValueError("rejection_rate must use generated_count as its denominator")
        return self


class ScreeningRunResponse(BaseModel):
    schema_version: Literal["screening-run/v1", "screening-run/v2", "screening-run/v3", "screening-run/v4", "screening-run/v5", "screening-run/v6", "screening-run/v7", "screening-run/v8"] = "screening-run/v1"
    id: str
    project_id: str
    created_at: datetime
    purpose: Literal[
        "design_space_map", "goal_search", "experiment_batch"
    ] | None = None
    source_run_id: str | None = None
    seed: int
    prediction_sampling_request: SamplingRequest | None = None
    base_candidate_id: str
    base_inputs: CandidateInputs | None = None
    base_canonical_input: dict[str, object]
    model_provenance: ModelMetadata
    target: str
    target_goal: ScreeningGoal | None = None
    secondary_goals: dict[str, ScreeningGoal] = Field(default_factory=dict)
    target_value: float | None = Field(
        default=None,
        deprecated="screening-run/v1-v3 compatibility; use target_goal",
    )
    secondary_targets: dict[str, float] = Field(
        default_factory=dict,
        deprecated="screening-run/v1-v3 compatibility; use secondary_goals",
    )
    score_contract: ScreeningScoreContract
    samples: int
    variables: dict[str, ScreeningVariable]
    design_space: dict[str, Any] | None = None
    design_space_digest: str | None = None
    project_design_space_digest: str | None = None
    project_design_space_binding_provenance: Literal[
        "explicit", "generated_default", "inherited_predecessor", "unbound_legacy"
    ] = "unbound_legacy"
    objective_definition: ObjectiveDefinition | None = None
    objective_definition_digest: str | None = None
    objective_binding_provenance: Literal[
        "explicit", "project_revision", "legacy_screening"
    ] = "legacy_screening"
    objective_execution: ProposalObjectiveExecution | None = None
    proposal_strategy: ScreeningProposalStrategy | None = None
    design_prior: DesignPriorPackageReference | None = None
    proposal_diagnostics: ScreeningProposalDiagnostics | None = None
    proposal_pool: list[ProposalCandidateEvaluation] = Field(default_factory=list)
    proposal_rejections: list[ProposalRejectedCandidate] = Field(default_factory=list)
    proposal_selection: ProposalSelectionEvidence | None = None
    batch_proposal: BatchProposalRun | None = None
    rejection_summary: dict[str, int] | None = Field(
        default=None,
        deprecated="screening-run/v1-v2 compatibility; use proposal_diagnostics",
    )
    points: list[ScreeningPoint]
    representative_points: list[ScreeningPoint]

    @model_validator(mode="after")
    def proposal_identity_is_internally_consistent(self) -> "ScreeningRunResponse":
        if self.schema_version != "screening-run/v8" and self.proposal_selection is not None:
            raise ValueError("legacy screening run must not contain proposal selection")
        if self.schema_version not in {"screening-run/v3", "screening-run/v4", "screening-run/v5", "screening-run/v6", "screening-run/v7", "screening-run/v8"}:
            return self
        if (
            self.design_space is None
            or self.design_space_digest is None
            or self.proposal_strategy is None
            or self.proposal_diagnostics is None
        ):
            raise ValueError("screening-run/v3 requires design-space, strategy, and diagnostics")
        if self.proposal_strategy.seed != self.seed:
            raise ValueError("proposal strategy seed must match screening run seed")
        if self.proposal_strategy.requested_count != self.samples:
            raise ValueError("proposal strategy requested_count must match samples")
        expected_generated = self.samples * self.proposal_strategy.pool_multiplier
        if self.proposal_diagnostics.generated_count != expected_generated:
            raise ValueError("proposal diagnostics must cover the complete generated pool")
        if self.schema_version in {"screening-run/v6", "screening-run/v7", "screening-run/v8"}:
            if self.proposal_diagnostics.evaluated_count != self.proposal_diagnostics.valid_count:
                raise ValueError("screening-run/v6 must evaluate the complete valid pool")
            if self.proposal_diagnostics.selected_count != self.samples:
                raise ValueError("screening-run/v6 selected_count must match samples")
            if len(self.proposal_pool) != self.proposal_diagnostics.valid_count:
                raise ValueError("screening-run/v6 proposal_pool must preserve every evaluated point")
            if len(self.proposal_rejections) != self.proposal_diagnostics.rejected_count:
                raise ValueError("screening-run/v6 must preserve every rejected generated point")
            pool_indices = {
                item.pool_index
                for item in (*self.proposal_pool, *self.proposal_rejections)
            }
            if len(pool_indices) != self.proposal_diagnostics.generated_count:
                raise ValueError("screening-run/v6 generated pool indices must be complete")
            if sum(item.selected_rank is not None for item in self.proposal_pool) != self.samples:
                raise ValueError("screening-run/v6 proposal_pool must identify every selected point")
            if self.batch_proposal is not None:
                if (
                    self.batch_proposal.distance_id
                    != self.proposal_strategy.distance_id
                    or self.batch_proposal.distance_version
                    != self.proposal_strategy.distance_version
                    or self.batch_proposal.distance_parameters
                    != self.proposal_strategy.distance_parameters
                ):
                    raise ValueError(
                        "batch proposal distance must match proposal strategy evidence"
                    )
                point_by_index = {item.index: item for item in self.points}
                pool_by_index = {
                    item.pool_index: item for item in self.proposal_pool
                }
                if any(
                    (
                        item.source == "acquisition_ranked"
                        and (
                            item.point_index not in point_by_index
                            or item.pool_index not in pool_by_index
                        )
                    )
                    or (
                        item.source == "exact_control"
                        and (
                            item.point_index is not None
                            or item.candidate_id is None
                            or item.candidate_revision is None
                        )
                    )
                    for item in self.batch_proposal.selected
                ):
                    raise ValueError(
                        "batch proposal must reference the saved screening shortlist"
                    )
        elif self.proposal_diagnostics.evaluated_count != self.samples:
            raise ValueError("proposal diagnostics evaluated_count must match samples")
        if self.schema_version in {"screening-run/v4", "screening-run/v5", "screening-run/v6", "screening-run/v7", "screening-run/v8"}:
            if self.__dict__["target_value"] is not None or self.__dict__["secondary_targets"]:
                raise ValueError("screening-run/v4 must use target_goal and secondary_goals")
            if self.target in self.secondary_goals:
                raise ValueError("target must not also appear in secondary_goals")
            expected_direction = self.target_goal.direction if self.target_goal else None
            if self.score_contract.direction != expected_direction:
                raise ValueError("score contract direction must match target_goal")
        if self.schema_version in {"screening-run/v5", "screening-run/v6", "screening-run/v7", "screening-run/v8"}:
            if self.objective_definition is None or self.objective_definition_digest is None:
                raise ValueError("screening-run/v5 requires an Objective Definition")
            if self.objective_definition.digest != self.objective_definition_digest:
                raise ValueError("Objective Definition digest does not match its payload")
            expected_target = (
                self.target_goal.lower
                if self.target_goal and self.target_goal.direction == "at_least"
                else self.target_goal.upper
                if self.target_goal and self.target_goal.direction == "at_most"
                else None
            )
            expected_lower = self.target_goal.lower if self.target_goal else None
            expected_upper = self.target_goal.upper if self.target_goal else None
            if (
                self.score_contract.target_value != expected_target
                or self.score_contract.lower != expected_lower
                or self.score_contract.upper != expected_upper
            ):
                raise ValueError("score contract bounds must match target_goal")
        if self.schema_version in {"screening-run/v7", "screening-run/v8"}:
            if self.purpose is None:
                raise ValueError("screening-run/v7 requires purpose")
            if self.purpose == "design_space_map":
                if (
                    self.source_run_id is not None
                    or self.target_goal is not None
                    or self.secondary_goals
                    or self.objective_execution is not None
                    or self.batch_proposal is not None
                ):
                    raise ValueError("design-space map must not contain goal or batch execution")
                if self.score_contract.fallback != "support_distance":
                    raise ValueError("design-space map must use support-distance evidence")
            elif self.purpose == "goal_search":
                if self.source_run_id is not None or self.batch_proposal is not None:
                    raise ValueError("goal search must not contain batch evidence")
                if self.objective_execution is None:
                    raise ValueError("goal search requires objective execution")
            elif (
                self.source_run_id is None
                or self.batch_proposal is None
                or self.objective_execution is None
            ):
                raise ValueError("experiment batch requires its source and objective evidence")
        if self.schema_version == "screening-run/v8":
            assert self.proposal_diagnostics is not None
            assert self.proposal_strategy is not None
            if self.proposal_strategy.generator_id == "design_prior":
                if self.design_prior is None:
                    raise ValueError("Design Prior strategy requires pinned prior evidence")
            elif self.design_prior is not None:
                raise ValueError("non-prior strategy must not claim Design Prior evidence")
            if (
                self.proposal_diagnostics.displayed_count is None
                or self.proposal_diagnostics.proposed_count is None
            ):
                raise ValueError("screening-run/v8 requires displayed/proposed counts")
            if self.proposal_diagnostics.displayed_count != len(self.points):
                raise ValueError("displayed_count must match saved point count")
            if self.proposal_diagnostics.selected_count != len(self.points):
                raise ValueError("legacy selected_count must match saved point count")
            if self.purpose == "goal_search":
                if self.proposal_selection is None:
                    raise ValueError("goal search requires proposal selection evidence")
                if (
                    self.proposal_diagnostics.proposed_count
                    != self.proposal_selection.actual_count
                ):
                    raise ValueError("proposed_count must match proposal selection")
                if (
                    self.proposal_strategy is None
                    or self.proposal_selection.distance_id
                    != self.proposal_strategy.distance_id
                    or self.proposal_selection.distance_version
                    != self.proposal_strategy.distance_version
                    or self.proposal_selection.distance_parameters
                    != self.proposal_strategy.distance_parameters
                ):
                    raise ValueError(
                        "proposal selection distance must match proposal strategy"
                    )
                selected_point_indices = [
                    item.point_index
                    for item in self.proposal_selection.selected
                ]
                selected_pool_indices = [
                    item.pool_index
                    for item in self.proposal_selection.selected
                ]
                if (
                    len(selected_point_indices) != len(set(selected_point_indices))
                    or len(selected_pool_indices) != len(set(selected_pool_indices))
                ):
                    raise ValueError("proposal selection references must be unique")
                pool_by_index = {
                    item.pool_index: item for item in self.proposal_pool
                }
                if any(
                    item.point_index >= len(self.points)
                    or item.pool_index not in pool_by_index
                    or pool_by_index[item.pool_index].selected_rank
                    != item.point_index + 1
                    for item in self.proposal_selection.selected
                ):
                    raise ValueError(
                        "proposal selection must reference saved displayed points"
                    )
            elif self.purpose == "design_space_map":
                if (
                    self.proposal_selection is not None
                    or self.proposal_diagnostics.proposed_count != 0
                ):
                    raise ValueError("design-space map must not contain proposals")
            elif self.purpose == "experiment_batch":
                if self.proposal_selection is not None:
                    raise ValueError(
                        "experiment batch must not reuse source proposal selection"
                    )
                if self.proposal_diagnostics.proposed_count != 0:
                    raise ValueError("experiment batch proposal count must be zero")
        return self
