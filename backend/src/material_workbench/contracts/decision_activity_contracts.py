"""Typed contracts for decision-oriented analysis activities.

Parameters and results are discriminated unions keyed on ``schema_version``.
Adding an activity means adding one parameters model, one result model, and one
registry entry; it must not require a branch inside an existing activity.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.schemas import ModelMetadata, Prediction, Support
from material_workbench.contracts.task_contracts import ContractModel


ActivityOperation = Literal["preview"]
ActivityResource = Literal["candidate", "comparison_candidate"]


class DecisionActivityDefinition(ContractModel):
    activity_id: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    question: Annotated[str, Field(min_length=1)]
    required_operations: tuple[ActivityOperation, ...]
    required_resources: tuple[ActivityResource, ...]
    result_kind: Annotated[str, Field(min_length=1)]
    execution_policy: Literal["explicit"]


class DecisionActivityAvailability(ContractModel):
    definition: DecisionActivityDefinition
    available: bool
    reasons: tuple[str, ...] = ()

    @model_validator(mode="after")
    def reasons_match_availability(self) -> "DecisionActivityAvailability":
        if self.available and self.reasons:
            raise ValueError("available activity cannot have unavailable reasons")
        if not self.available and not self.reasons:
            raise ValueError("unavailable activity requires a reason")
        return self


class AbsoluteTolerance(ContractModel):
    kind: Literal["absolute"]
    amount: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class RelativeTolerance(ContractModel):
    kind: Literal["relative"]
    fraction: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]


class BoundedUniformTolerance(ContractModel):
    kind: Literal["bounded_uniform"]
    lower: Annotated[float, Field(allow_inf_nan=False)]
    upper: Annotated[float, Field(allow_inf_nan=False)]

    @model_validator(mode="after")
    def ordered(self) -> "BoundedUniformTolerance":
        if self.lower >= self.upper:
            raise ValueError("bounded uniform requires lower < upper")
        return self


class TruncatedNormalTolerance(ContractModel):
    kind: Literal["truncated_normal"]
    standard_deviation: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    lower: Annotated[float, Field(allow_inf_nan=False)]
    upper: Annotated[float, Field(allow_inf_nan=False)]

    @model_validator(mode="after")
    def ordered(self) -> "TruncatedNormalTolerance":
        if self.lower >= self.upper:
            raise ValueError("truncated normal requires lower < upper")
        return self


ToleranceSpec = Annotated[
    AbsoluteTolerance
    | RelativeTolerance
    | BoundedUniformTolerance
    | TruncatedNormalTolerance,
    Field(discriminator="kind"),
]


class ToleranceProfile(ContractModel):
    fields: Annotated[dict[str, ToleranceSpec], Field(min_length=1, max_length=12)]


class RobustnessParameters(ContractModel):
    schema_version: Literal["robustness-parameters/v1"] = "robustness-parameters/v1"
    sample_count: Annotated[int, Field(ge=8, le=500)] = 64
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)] = 0
    tolerance_profile: ToleranceProfile


class CandidateDifferenceParameters(ContractModel):
    schema_version: Literal["candidate-difference-parameters/v1"] = (
        "candidate-difference-parameters/v1"
    )
    comparison_candidate_id: Annotated[str, Field(min_length=1)]
    comparison_revision: Annotated[int, Field(ge=1)]


DecisionActivityParameters = Annotated[
    RobustnessParameters | CandidateDifferenceParameters,
    Field(discriminator="schema_version"),
]


class DecisionActivityRunRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=1)]
    parameters: DecisionActivityParameters


class InputVariationInterval(ContractModel):
    median: float
    lower: float
    upper: float
    coverage: Literal["central_90_percent"] = "central_90_percent"


class ModelUncertaintyInterval(ContractModel):
    lower: float
    upper: float
    semantics: Literal["runtime_predictive_interval"] = "runtime_predictive_interval"


class RobustnessTargetSummary(ContractModel):
    target: str
    unit: str
    base_prediction: Prediction
    input_variation: InputVariationInterval
    model_uncertainty: ModelUncertaintyInterval
    goal_achievement_rate: Annotated[float | None, Field(ge=0, le=1)] = None
    worst_observed: float


class CriticalInput(ContractModel):
    path: str
    target: str
    absolute_correlation: Annotated[float, Field(ge=0, le=1)]
    direction: Literal["increases_output", "decreases_output", "unclear"]


class RobustnessFailureExample(ContractModel):
    sample_index: int
    varied_inputs: dict[str, float]
    outputs: dict[str, float]
    failed_targets: tuple[str, ...]
    support: Support


class RobustnessSummary(ContractModel):
    schema_version: Literal["robustness-summary/v1"] = "robustness-summary/v1"
    requested_samples: int
    accepted_samples: int
    rejected_samples: int
    target_summaries: tuple[RobustnessTargetSummary, ...]
    critical_inputs: tuple[CriticalInput, ...]
    failure_examples: tuple[RobustnessFailureExample, ...]
    extrapolated_rate: Annotated[float, Field(ge=0, le=1)]
    caution_rate: Annotated[float, Field(ge=0, le=1)]
    warnings: tuple[str, ...]


class DifferenceTargetSummary(ContractModel):
    """One target's prediction gap, kept separate from model uncertainty."""

    target: str
    unit: str
    base_prediction: Prediction
    comparison_prediction: Prediction
    difference: float
    attributed_difference: float
    unexplained_difference: float


class DifferenceInputChange(ContractModel):
    path: str
    label: str
    unit: str | None = None
    base_value: float | str | None
    comparison_value: float | str | None
    difference: float | None = None


class DifferenceContribution(ContractModel):
    """Effect of substituting one input, measured from the comparison candidate."""

    path: str
    target: str
    contribution: float
    direction: Literal["increases_output", "decreases_output", "no_change"]


class CandidateDifferenceSummary(ContractModel):
    schema_version: Literal["candidate-difference-summary/v1"] = (
        "candidate-difference-summary/v1"
    )
    comparison_candidate_id: str
    comparison_candidate_revision: int
    changed_input_count: Annotated[int, Field(ge=1)]
    target_summaries: tuple[DifferenceTargetSummary, ...]
    input_changes: tuple[DifferenceInputChange, ...]
    contributions: tuple[DifferenceContribution, ...]
    base_support: Support
    comparison_support: Support
    warnings: tuple[str, ...]


DecisionActivityResult = Annotated[
    RobustnessSummary | CandidateDifferenceSummary,
    Field(discriminator="schema_version"),
]


class DecisionActivityProvenance(ContractModel):
    task_id: str
    task_contract_digest: str
    candidate_id: str
    candidate_revision: int
    canonical_input_digest: str
    model_package_digest: str
    feature_pipeline_digest: str
    activity_id: str
    activity_version: str
    parameters_digest: str
    model: ModelMetadata


class DecisionActivityRun(ContractModel):
    id: str
    semantic_identity: str
    project_id: str
    created_at: datetime
    definition: DecisionActivityDefinition
    parameters: DecisionActivityParameters
    provenance: DecisionActivityProvenance
    result: DecisionActivityResult

    @model_validator(mode="after")
    def result_matches_its_definition(self) -> "DecisionActivityRun":
        if self.result.schema_version != self.definition.result_kind:
            raise ValueError(
                "stored result kind does not match the activity definition: "
                f"{self.result.schema_version} != {self.definition.result_kind}"
            )
        if self.provenance.activity_id != self.definition.activity_id:
            raise ValueError("provenance activity does not match the definition")
        if self.provenance.activity_version != self.definition.version:
            raise ValueError("provenance activity version does not match the definition")
        return self


ROBUSTNESS_ACTIVITY = DecisionActivityDefinition(
    activity_id="robustness-analysis-v1",
    version="1.0.0",
    label="ロバストネス／公差解析",
    question="製造ばらつきがあっても目標を安定して満たすか",
    required_operations=("preview",),
    required_resources=("candidate",),
    result_kind="robustness-summary/v1",
    execution_policy="explicit",
)

CANDIDATE_DIFFERENCE_ACTIVITY = DecisionActivityDefinition(
    activity_id="candidate-difference-v1",
    version="1.0.0",
    label="候補差分の要因分解",
    question="この候補と比較候補で予測が違うのは、どの入力が効いているのか",
    required_operations=("preview",),
    required_resources=("candidate", "comparison_candidate"),
    result_kind="candidate-difference-summary/v1",
    execution_policy="explicit",
)
