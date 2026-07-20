from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel, field_validator, model_validator

from .task_contracts import CandidateProvenance, ManualSourceRef


COMPOSITION_ELEMENTS = {
    "C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca"
}
PREDICTION_TARGETS = {"TS", "YS", "EL", "lambda"}


class HeatPoint(BaseModel):
    time_s: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    temperature_c: Annotated[float, Field(ge=-273.15, le=1800)]
    segment_start: bool = False
    set_temperature_c: Annotated[float | None, Field(ge=-273.15, le=1800)] = None
    stage_category: str | None = None
    stage_name: str | None = None
    mapping_status: str | None = None


class CandidateInputs(BaseModel):
    composition: dict[str, float]
    process: dict[str, float]
    categorical: dict[str, str]
    heat_pattern: list[HeatPoint] | None = Field(default=None, max_length=30)

    @field_validator("composition", "process")
    @classmethod
    def numeric_values_are_finite(cls, value: dict[str, float]) -> dict[str, float]:
        invalid = sorted(name for name, amount in value.items() if not math.isfinite(amount))
        if invalid:
            raise ValueError(f"入力値は有限値にしてください: {', '.join(invalid)}")
        return value

    @model_validator(mode="after")
    def heat_pattern_is_a_real_route(self) -> "CandidateInputs":
        if self.heat_pattern is not None:
            if len(self.heat_pattern) < 2:
                raise ValueError("ヒートパターンは2点以上にしてください")
            times = [point.time_s for point in self.heat_pattern]
            if any(later <= earlier for earlier, later in zip(times, times[1:])):
                raise ValueError("ヒートパターンの時刻は厳密な昇順にしてください")
        return self


class CandidateInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80)] = "候補"
    inputs: CandidateInputs
    provenance: CandidateProvenance = Field(default_factory=lambda: ManualSourceRef(source_kind="manual"))


class CandidateUpdate(CandidateInput):
    expected_revision: Annotated[int, Field(ge=1)]


class Candidate(CandidateInput):
    id: str
    project_id: str
    revision: Annotated[int, Field(ge=1)]
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CandidateImportError(BaseModel):
    row: int
    message: str


class CandidateImportResponse(BaseModel):
    created: int
    errors: list[CandidateImportError]
    candidates: list[Candidate]


class InputRange(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def range_is_valid(self) -> "InputRange":
        if not math.isfinite(self.min) or not math.isfinite(self.max):
            raise ValueError("入力許容範囲は有限の数値にしてください")
        if self.min >= self.max:
            raise ValueError("入力許容範囲の最小値は最大値より小さくしてください")
        return self


class ProjectInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] = "焼鈍条件の候補検討"
    description: str = ""
    purpose: str = ""
    task_id: Literal["annealed-properties-v1", "hot-rolled-properties-v1"] = "annealed-properties-v1"
    target_values: dict[str, float] = Field(default_factory=dict)
    input_ranges: dict[str, InputRange] = Field(default_factory=dict)
    notes: str = ""
    decision_candidate_id: Annotated[str, Field(max_length=80)] = ""
    decision_snapshot_id: Annotated[str, Field(max_length=80)] = ""
    decision_note: Annotated[str, Field(max_length=500)] = ""

    @field_validator("target_values")
    @classmethod
    def targets_are_supported_and_finite(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - PREDICTION_TARGETS)
        if unknown:
            raise ValueError(f"未対応の目標特性です: {', '.join(unknown)}")
        if any(not math.isfinite(target) for target in value.values()):
            raise ValueError("目標値は有限の数値にしてください")
        return value

    @model_validator(mode="after")
    def decision_note_requires_candidate(self) -> "ProjectInput":
        if (self.decision_note or self.decision_snapshot_id) and not self.decision_candidate_id:
            raise ValueError("判断理由またはスナップショットを保存する場合は採用候補を指定してください")
        if self.decision_candidate_id and not self.decision_snapshot_id:
            raise ValueError("採用候補には判断時点の予測スナップショットが必要です")
        if self.decision_candidate_id and not self.decision_note:
            raise ValueError("採用候補には判断理由が必要です")
        return self


class ProjectDecisionInput(BaseModel):
    candidate_id: Annotated[str, Field(max_length=80)] = ""
    snapshot_id: Annotated[str, Field(max_length=80)] = ""
    note: Annotated[str, Field(max_length=500)] = ""

    @model_validator(mode="after")
    def complete_or_empty(self) -> "ProjectDecisionInput":
        populated = (self.candidate_id, self.snapshot_id, self.note)
        if any(populated) and not all(populated):
            raise ValueError("採用候補・予測スナップショット・判断理由をすべて指定してください")
        return self


class Project(ProjectInput):
    id: str
    created_at: datetime
    updated_at: datetime


class ScreeningVariable(BaseModel):
    mode: Literal["fixed", "range", "list"]
    value: float | str | None = None
    min: float | None = None
    max: float | None = None
    values: list[float | str] | None = None

    @model_validator(mode="after")
    def complete_specification(self) -> "ScreeningVariable":
        if self.mode == "fixed" and self.value is None:
            raise ValueError("fixedにはvalueが必要です")
        if self.mode == "range" and (self.min is None or self.max is None or self.min >= self.max):
            raise ValueError("rangeにはmin < maxが必要です")
        if self.mode == "list" and not self.values:
            raise ValueError("listにはvaluesが必要です")
        return self


class ScreeningRequest(BaseModel):
    base_candidate_id: Annotated[str, Field(min_length=1)]
    variables: Annotated[dict[str, ScreeningVariable], Field(min_length=1)]
    samples: Annotated[int, Field(ge=48, le=128)] = 64
    target: Annotated[str, Field(min_length=1)] = "TS"
    target_value: float | None = None

    @model_validator(mode="after")
    def variables_match_their_fields(self) -> "ScreeningRequest":
        composition_fields = {"composition.C", "composition.Si", "composition.Mn", "composition.P", "composition.S", "composition.Cr", "composition.Mo", "composition.Ni", "composition.Al", "composition.Ti", "composition.B", "composition.N", "composition.O", "composition.Ca"}
        numeric_fields = composition_fields | {"thickness_mm", "line_speed_m_min", "max_temperature_c"}
        allowed = numeric_fields | {"coating"}
        unknown = sorted(set(self.variables) - allowed)
        if unknown:
            raise ValueError(f"スクリーニング対象外の変数です: {', '.join(unknown)}")
        if self.target_value is not None and not math.isfinite(self.target_value):
            raise ValueError("target_valueは有限の数値にしてください")
        for name, spec in self.variables.items():
            if name == "coating":
                if spec.mode == "range":
                    raise ValueError("coatingにはrangeを指定できません")
                values = [spec.value] if spec.mode == "fixed" else (spec.values or [])
                if any(not isinstance(value, str) or value not in {"なし", "GI", "GA"} for value in values):
                    raise ValueError("coatingは なし / GI / GA のいずれかにしてください")
                continue
            if spec.mode == "fixed":
                if not isinstance(spec.value, (int, float)) or isinstance(spec.value, bool) or not math.isfinite(float(spec.value)):
                    raise ValueError(f"{name}のfixed値は有限の数値にしてください")
            elif spec.mode == "range":
                if not math.isfinite(float(spec.min)) or not math.isfinite(float(spec.max)):
                    raise ValueError(f"{name}のrangeは有限の数値にしてください")
            else:
                values = spec.values or []
                if any(not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)) for value in values):
                    raise ValueError(f"{name}のlist値は有限の数値にしてください")
        return self


class ActualMeasurementInput(BaseModel):
    property: Literal["TS", "YS", "EL", "lambda"]
    mean: Annotated[float, Field(allow_inf_nan=False)]
    std: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0
    replicates: Annotated[int, Field(ge=1, le=999)] = 1
    unit: Literal["MPa", "%"]
    experiment_no: str = ""
    measured_at: date | None = None
    note: str = ""

    @model_validator(mode="after")
    def unit_matches_property(self) -> "ActualMeasurementInput":
        expected = {"TS": "MPa", "YS": "MPa", "EL": "%", "lambda": "%"}[self.property]
        if self.unit != expected:
            raise ValueError(f"{self.property}の単位は{expected}です")
        return self


class ActualMeasurement(ActualMeasurementInput):
    id: str
    candidate_id: str
    snapshot_id: str
    created_at: datetime


class Prediction(BaseModel):
    value: float
    lower: float
    upper: float
    unit: str
    goal_value: float | None = None
    goal_probability: Annotated[float | None, Field(ge=0, le=1)] = None
    goal_direction: Literal["at_least", "at_most"] | None = None
    uncertainty_components: dict[str, float] | None = None


class RepeatSummary(BaseModel):
    mean: float
    std: float
    n: int


class SimilarObservation(BaseModel):
    observation_id: str = ""
    parent_key: str
    source: str = ""
    layer: Literal["training", "historical"] | None = None
    distance: float
    components: dict[str, float] = Field(default_factory=dict)
    outputs: dict[str, float] = Field(default_factory=dict)
    repeat_summary: dict[str, RepeatSummary] = Field(default_factory=dict)


class ModelIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = ""
    version: str = ""
    method: str = ""


class PackageIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = ""
    version: str = ""
    manifest_sha256: str = ""
    runtime_types: list[str] = Field(default_factory=list)


class FeaturePipelineIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = ""
    version: str = ""
    input_schema_version: str = ""
    features: list[str] = Field(default_factory=list)


class TrainingDataIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_path: str = ""
    source_sha256: str = ""
    records: dict[str, int] = Field(default_factory=dict)


class PredictionIntervalIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    method: str = ""
    coverage: str | float | None = None
    grouping: str = ""
    folds: int | dict[str, int] | None = None
    note: str = ""


class SimilarityIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: str = ""
    method: str = ""


class ModelMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    package: PackageIdentity | None = None
    model: ModelIdentity | None = None
    feature_pipeline: FeaturePipelineIdentity | None = None
    training_data: TrainingDataIdentity | None = None
    prediction_interval: PredictionIntervalIdentity | None = None
    similarity: SimilarityIdentity | None = None


class Support(BaseModel):
    status: Literal["supported", "caution", "extrapolated"]
    distance: float
    percentile: float
    message: str
    components: dict[str, float]
    reference_count: int
    supported_threshold: float
    caution_threshold: float


class PredictionResponse(BaseModel):
    task_id: str
    candidate_id: str
    mode: Literal["preview", "detailed"]
    predictions: dict[str, Prediction]
    support: Support
    warnings: list[str]
    model_meta: ModelMetadata
    canonical_input: dict[str, object]
    similar: list[SimilarObservation]
    heat_pattern: list[HeatPoint]
    response_curve: list[dict[str, float]] | None = None


class RuntimeAvailability(BaseModel):
    runtime_type: str
    available: bool


class ModelPackagePredictorStatus(BaseModel):
    target: str
    runtime_type: str
    predictive_family: str


class ModelQualityTarget(BaseModel):
    model_config = ConfigDict(extra="allow")
    target: str
    parent_conditions: int
    mae: float
    rmse: float
    interval_coverage_90: float


class ModelQualityReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    split: str
    targets: list[ModelQualityTarget]


class ModelPackageStatus(BaseModel):
    id: str
    version: str
    task_id: str
    manifest_sha256: str
    active_runtimes: list[str]
    supported_runtimes: list[RuntimeAvailability]
    predictors: list[ModelPackagePredictorStatus]
    quality_report: ModelQualityReport


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    prediction: PredictionResponse | None = None
    provenance: ModelMetadata | None = None


class SnapshotResponse(BaseModel):
    id: str
    candidate_id: str
    created_at: datetime
    payload: SnapshotPayload


class CurvePoint(BaseModel):
    x: float
    value: float
    lower: float
    upper: float


class CurveVariable(BaseModel):
    id: str
    label: str
    unit: str
    min: float
    max: float
    current: float


class ResponseCurvesResponse(BaseModel):
    variable: CurveVariable
    curves: dict[str, list[CurvePoint]]
    output_ranges: dict[str, InputRange]


class ResponseCurvesResult(RootModel[ResponseCurvesResponse | dict[str, list[CurvePoint]]]):
    pass


class LineageIndexItem(BaseModel):
    key: str
    entity_type: str
    has_issue: bool
    family: str | None = None
    project: str | None = None
    route: str | None = None
    peak_temperature_c: float | None = None
    coating: str | None = None
    learning_status: str | None = None
    has_observation: bool | None = None
    observation_summary: dict[str, RepeatSummary] | None = None


class LineageIndexResponse(BaseModel):
    items: list[LineageIndexItem]
    total_entities: int
    relation_rows: int
    detected_issues: int
    counts_by_type: dict[str, int]


class ScreeningPoint(BaseModel):
    index: int
    inputs: dict[str, float | str]
    candidate: CandidateInput
    prediction: Prediction
    color_value: float
    support: Support
    score: float | None
    goal_evaluation: "ScreeningGoalEvaluation"


class ScreeningGoalEvaluation(BaseModel):
    score: float | None
    method: Literal["achievement_probability", "directional_shortfall", "absolute_distance", "support_distance"]
    achieved: bool | None
    achievement_probability: float | None


class ScreeningScoreContract(BaseModel):
    version: Literal["screening-score/v1"]
    preference: Literal["lower_is_better"]
    direction: Literal["at_least", "at_most", "target"] | None
    target_value: float | None
    probability_available: bool
    fallback: Literal["directional_shortfall", "absolute_distance", "support_distance"]
    display_label: str


class ScreeningRunResponse(BaseModel):
    id: str
    project_id: str
    created_at: datetime
    seed: int
    base_candidate_id: str
    base_canonical_input: dict[str, object]
    model_provenance: ModelMetadata
    target: str
    target_value: float | None
    score_contract: ScreeningScoreContract
    samples: int
    variables: dict[str, ScreeningVariable]
    points: list[ScreeningPoint]
    representative_points: list[ScreeningPoint]


class PredictionComparison(BaseModel):
    actual: ActualMeasurement
    snapshot_id: str
    prediction: PredictionResponse
    provenance: ModelMetadata


class PredictionVsActualResponse(BaseModel):
    candidate_id: str
    actuals: list[ActualMeasurement]
    comparisons: list[PredictionComparison]


class DetailedPredictionResponse(BaseModel):
    prediction: PredictionResponse
    snapshot: SnapshotResponse


class FieldError(BaseModel):
    path: str
    message: str


class ApiError(BaseModel):
    code: Literal[
        "not_found",
        "revision_conflict",
        "candidate_limit",
        "adopted_candidate",
        "candidate_archived",
        "project_task_locked",
        "data_integrity_error",
        "validation_error",
        "runtime_unavailable",
    ]
    message: str
    field_errors: list[FieldError] = Field(default_factory=list)
    current_candidate: Candidate | None = None


class DataQualityIssue(BaseModel):
    issue_id: str
    issue_type: Literal["missing_key", "orphan_entity", "duplicate_key", "invalid_reference"]
    source_sheet: str
    entity_key: str
    detail: str


class QualityScenario(BaseModel):
    model_config = ConfigDict(extra="allow")
    scenario_id: str
    category: str = Field(alias="分類")
    target_key: str = Field(alias="対象キー")
    target_sheet: str = Field(alias="対象シート")
    expected_insight: str = Field(alias="期待する気づき")


class QualityResponse(BaseModel):
    # Legacy scenario fields remain until the UI is switched to detected issues.
    total: int
    by_category: dict[str, int]
    issues: list[QualityScenario]
    reference_scenarios: list[QualityScenario]
    detected_total: int
    detected_by_type: dict[str, int]
    detected_issues: list[DataQualityIssue]


class PropertySummary(BaseModel):
    count: int
    min: float
    mean: float
    std: float
    median: float
    max: float


class ConnectedObservation(BaseModel):
    id: str
    source: str
    parent_key: str
    outputs: dict[str, float]


class ObservationGroup(BaseModel):
    stage: str
    test_type: str
    property: str
    count: int
    min: float
    mean: float
    std: float
    median: float
    max: float
    observations: list[ConnectedObservation]


class LineageGraphNode(BaseModel):
    key: str
    entity_type: str
    source_sheet: str
    exists: bool
    selected: bool
    issue_types: list[str]


class LineageGraphEdge(BaseModel):
    source: str
    target: str
    route_rows: list[int]


class LineageGraph(BaseModel):
    nodes: list[LineageGraphNode]
    edges: list[LineageGraphEdge]
    relation_row_count: int
    omitted_node_count: int


class LineageNodeDetail(BaseModel):
    key: str
    entity_type: str
    source_sheet: str
    source_row: dict[str, str | float | int | bool | None]
    primary_conditions: dict[str, str | float | int | bool | None]
    composition: dict[str, float]
    heat_pattern: list[HeatPoint]
    connected_observation_count: int
    connected_observations: list[ConnectedObservation]
    observation_groups: list[ObservationGroup]
    property_summary: dict[str, PropertySummary]
    related_entities: dict[str, list[str]]
    missing_source: bool = False


class LineageResponse(BaseModel):
    # key/relations/quality_issues are the existing renderer contract.
    key: str
    relations: dict[str, list[str]]
    quality_issues: list[DataQualityIssue]
    node: LineageNodeDetail
    graph: LineageGraph
    candidate_eligible: bool
    candidate_reason: str


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
