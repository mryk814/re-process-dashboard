"""Typed contracts for decision-oriented analysis activities."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.schemas import ModelMetadata, Prediction, Support
from material_workbench.contracts.task_contracts import ContractModel


ActivityOperation = Literal["preview"]
ActivityResource = Literal["candidate"]


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


class DecisionActivityRunRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=1)]
    parameters: RobustnessParameters


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
    parameters: RobustnessParameters
    provenance: DecisionActivityProvenance
    result: RobustnessSummary


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

DECISION_ACTIVITY_REGISTRY = (ROBUSTNESS_ACTIVITY,)
