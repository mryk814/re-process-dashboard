"""Immutable contracts for one bounded Prediction Graph goal-search slice."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.chain_contracts import ChainContractModel
from decision_workbench.contracts.task_contracts import NumericRange
from decision_workbench.execution.inference_work_graph import semantic_digest


class GraphGoalTermInput(ChainContractModel):
    output_id: Annotated[str, Field(min_length=1)]
    direction: Literal["at_least", "at_most"]
    threshold: Annotated[float, Field(allow_inf_nan=False)]


class PredictionGraphObjectiveInput(ChainContractModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    primary: GraphGoalTermInput
    hard_constraint: GraphGoalTermInput
    incumbent_candidate_id: Annotated[str, Field(min_length=1)]
    incumbent_candidate_revision: Annotated[int, Field(ge=1)]
    use_context: Literal["production", "demonstration"] = "production"


class PredictionGraphObjectiveTerm(GraphGoalTermInput):
    source_stage_id: Annotated[str, Field(min_length=1)]
    source_output_key: Annotated[str, Field(min_length=1)]
    unit: Annotated[str, Field(min_length=1)]
    evidence_kind: Literal["measured", "synthetic_demonstration", "unverified"]


class PredictionGraphObjective(ChainContractModel):
    schema_version: Literal["prediction-graph-objective/v1"] = (
        "prediction-graph-objective/v1"
    )
    objective_id: Annotated[str, Field(min_length=1)]
    revision: Literal[1] = 1
    project_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    graph_revision_id: Annotated[str, Field(min_length=1)]
    graph_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    project_binding_revision: Annotated[int, Field(ge=1)]
    project_binding_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    primary: PredictionGraphObjectiveTerm
    hard_constraint: PredictionGraphObjectiveTerm
    incumbent_candidate_id: Annotated[str, Field(min_length=1)]
    incumbent_candidate_revision: Annotated[int, Field(ge=1)]
    required_output_policy: Literal["all_required_latest"] = "all_required_latest"
    branch_availability_policy: Literal["objective_outputs_latest"] = (
        "objective_outputs_latest"
    )
    use_context: Literal["production", "demonstration"]
    created_at: datetime

    @property
    def digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def terms_are_distinct(self) -> PredictionGraphObjective:
        if self.primary.output_id == self.hard_constraint.output_id:
            raise ValueError("primaryとhard constraintは別のDecision Outputにします")
        return self


class PredictionGraphDesignVariable(ChainContractModel):
    input_id: Annotated[str, Field(min_length=1)]
    candidate_path: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    kind: Literal["number", "categorical"]
    sampling_policy: Literal["continuous_linear", "categorical_uniform"]
    unit: str | None = None
    numeric_range: NumericRange | None = None
    choices: tuple[str, ...] = ()
    affected_output_ids: tuple[str, ...]

    @model_validator(mode="after")
    def domain_matches_kind(self) -> PredictionGraphDesignVariable:
        if self.kind == "number" and (
            self.sampling_policy != "continuous_linear"
            or self.numeric_range is None
            or self.choices
        ):
            raise ValueError("numeric Graph variable requires only a range")
        if self.kind == "categorical" and (
            self.sampling_policy != "categorical_uniform"
            or not self.choices
            or self.numeric_range is not None
        ):
            raise ValueError("categorical Graph variable requires only choices")
        return self


class PredictionGraphDesignSpace(ChainContractModel):
    schema_version: Literal["prediction-graph-design-space/v1"] = (
        "prediction-graph-design-space/v1"
    )
    graph_revision_id: Annotated[str, Field(min_length=1)]
    graph_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    variables: Annotated[tuple[PredictionGraphDesignVariable, ...], Field(min_length=1)]

    @property
    def digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))


class PredictionGraphGoalSearchRequest(ChainContractModel):
    objective_id: Annotated[str, Field(min_length=1)]
    base_candidate_id: Annotated[str, Field(min_length=1)]
    base_candidate_revision: Annotated[int, Field(ge=1)]
    sample_count: Annotated[int, Field(ge=2, le=32)] = 8
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)] = 20260803


class PredictionGraphGoalSearchOutput(ChainContractModel):
    status: Literal[
        "latest",
        "failed",
        "blocked_by_upstream",
        "unavailable",
    ]
    value: Any | None = None
    prediction: dict[str, Any] | None = None
    support: dict[str, Any] | None = None
    error: str | None = None
    blocked_by_stage_ids: tuple[str, ...] = ()


class PredictionGraphGoalSearchPoint(ChainContractModel):
    point_index: Annotated[int, Field(ge=0)]
    candidate: CandidateInput
    input_values: dict[str, float | str]
    outputs: dict[str, PredictionGraphGoalSearchOutput]
    primary_achieved: bool | None
    hard_constraint_achieved: bool | None
    feasible: bool
    score: float | None
    rejection_reason: str | None = None


class PredictionGraphGoalSearchRun(ChainContractModel):
    schema_version: Literal["prediction-graph-goal-search-run/v1"] = (
        "prediction-graph-goal-search-run/v1"
    )
    run_id: Annotated[str, Field(min_length=1)]
    project_id: Annotated[str, Field(min_length=1)]
    graph_revision_id: Annotated[str, Field(min_length=1)]
    graph_revision_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    project_binding_revision: Annotated[int, Field(ge=1)]
    project_binding_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    base_candidate_id: Annotated[str, Field(min_length=1)]
    base_candidate_revision: Annotated[int, Field(ge=1)]
    base_candidate_digest: Annotated[
        str,
        Field(pattern=r"^sha256:[0-9a-f]{64}$"),
    ]
    objective: PredictionGraphObjective
    objective_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    design_space: PredictionGraphDesignSpace
    design_space_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    package_manifest_digests: Annotated[
        dict[
            str,
            Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")],
        ],
        Field(min_length=1),
    ]
    generator_id: Literal["latin_hypercube"] = "latin_hypercube"
    generator_version: Literal["1.0.0"] = "1.0.0"
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)]
    points: Annotated[
        tuple[PredictionGraphGoalSearchPoint, ...],
        Field(min_length=2),
    ]
    selected_point_indices: tuple[int, ...]
    created_at: datetime

    @model_validator(mode="after")
    def selected_points_are_feasible(self) -> PredictionGraphGoalSearchRun:
        by_index = {item.point_index: item for item in self.points}
        if len(by_index) != len(self.points):
            raise ValueError("goal-search point indices must be unique")
        if set(by_index) != set(range(len(self.points))):
            raise ValueError("goal-search point indices must be contiguous")
        if len(self.selected_point_indices) != len(set(self.selected_point_indices)):
            raise ValueError("selected goal-search point indices must be unique")
        if any(
            index not in by_index or not by_index[index].feasible
            for index in self.selected_point_indices
        ):
            raise ValueError("selected goal-search points must be feasible")
        if (
            self.objective.project_id != self.project_id
            or self.objective.graph_revision_id != self.graph_revision_id
            or self.objective.graph_revision_digest != self.graph_revision_digest
            or self.objective.project_binding_revision != self.project_binding_revision
            or self.objective.project_binding_digest != self.project_binding_digest
        ):
            raise ValueError("goal-search Objective identity must match the Run")
        if (
            self.design_space.graph_revision_id != self.graph_revision_id
            or self.design_space.graph_revision_digest != self.graph_revision_digest
        ):
            raise ValueError("goal-search Design Space identity must match the Run")
        if self.objective_digest != self.objective.digest:
            raise ValueError("goal-search Objective digest does not match")
        if self.design_space_digest != self.design_space.digest:
            raise ValueError("goal-search Design Space digest does not match")
        return self


class PredictionGraphPromotionRequest(ChainContractModel):
    point_index: Annotated[int, Field(ge=0)]
    name: Annotated[str, Field(min_length=1, max_length=80)]
