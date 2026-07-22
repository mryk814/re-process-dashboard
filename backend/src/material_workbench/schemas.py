from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .task_contracts import CandidateProvenance, DirectSourceRef, ResolvedTaskDefinition


COMPOSITION_ELEMENTS = {
    "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N"
}


class DataAssetCreateInput(BaseModel):
    original_filename: Annotated[str, Field(min_length=1, max_length=255)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    media_type: Annotated[str, Field(min_length=1, max_length=120)]
    locator_kind: Literal["managed", "bundled"]
    locator: Annotated[str, Field(min_length=1)]


class DataAssetUpdateInput(BaseModel):
    archived: bool


class DataAsset(DataAssetCreateInput):
    id: str
    created_at: datetime
    archived_at: datetime | None = None


class ProfileRevisionCreateInput(BaseModel):
    profile_id: Annotated[str, Field(min_length=1, max_length=120)]
    revision: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    profile_digest: Annotated[str, Field(min_length=1)]
    canonical_contract_digest: Annotated[str, Field(min_length=1)]
    effective_profile_json: dict[str, Any]


class ProfileRevisionUpdateInput(BaseModel):
    archived: bool


class ProfileRevision(ProfileRevisionCreateInput):
    id: str
    created_at: datetime
    archived_at: datetime | None = None


class DatasetRevisionCreateInput(BaseModel):
    data_asset_id: Annotated[str, Field(min_length=1)]
    profile_revision_id: Annotated[str, Field(min_length=1)]
    canonicalization_contract_digest: Annotated[str, Field(min_length=1)]


class DatasetRevisionUpdateInput(BaseModel):
    archived: bool


class DatasetRevision(DatasetRevisionCreateInput):
    id: str
    dataset_digest: Annotated[str, Field(min_length=1)]
    created_at: datetime
    archived_at: datetime | None = None


class DatasetViewMemberInput(BaseModel):
    dataset_revision_id: Annotated[str, Field(min_length=1)]
    ordinal: Annotated[int, Field(ge=0)]
    cohort_key: Annotated[str, Field(max_length=120)] = ""
    cohort_label: Annotated[str, Field(max_length=160)] = ""
    provenance_json: dict[str, Any] = Field(default_factory=dict)


class DatasetViewMember(DatasetViewMemberInput):
    dataset_view_revision_id: str


class DatasetViewRevisionCreateInput(BaseModel):
    view_id: Annotated[str, Field(min_length=1, max_length=120)]
    revision: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    kind: Literal["single", "cohort_comparison"]
    members: Annotated[list[DatasetViewMemberInput], Field(min_length=1)]

    @model_validator(mode="after")
    def members_match_view_kind(self) -> "DatasetViewRevisionCreateInput":
        if self.kind == "single" and len(self.members) != 1:
            raise ValueError("single Dataset ViewにはDataset Revisionを1件だけ指定してください")
        if self.kind == "cohort_comparison" and len(self.members) < 2:
            raise ValueError("cohort comparisonにはDataset Revisionを2件以上指定してください")
        revision_ids = [member.dataset_revision_id for member in self.members]
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("Dataset View内で同じDataset Revisionを重複指定できません")
        ordinals = [member.ordinal for member in self.members]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("Dataset View内のordinalは重複できません")
        cohort_keys = [member.cohort_key for member in self.members]
        if len(cohort_keys) != len(set(cohort_keys)):
            raise ValueError("Dataset View内のcohort_keyは重複できません")
        return self


class DatasetViewRevisionUpdateInput(BaseModel):
    archived: bool


class DatasetViewRevision(BaseModel):
    id: str
    view_id: str
    revision: Annotated[int, Field(ge=1)]
    name: str
    kind: Literal["single", "cohort_comparison"]
    view_digest: Annotated[str, Field(min_length=1)]
    members: list[DatasetViewMember]
    created_at: datetime
    archived_at: datetime | None = None


class ModelPackageRefCreateInput(BaseModel):
    package_id: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Annotated[str, Field(min_length=1)]
    manifest_digest: Annotated[str, Field(min_length=1)]
    locator: Annotated[str, Field(min_length=1)]
    manifest_json: dict[str, Any]


class ModelPackageRefUpdateInput(BaseModel):
    archived: bool


class ModelPackageRef(ModelPackageRefCreateInput):
    id: str
    created_at: datetime
    archived_at: datetime | None = None


class ProjectSeriesCreateInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    description: str = ""


class ProjectSeriesUpdateInput(ProjectSeriesCreateInput):
    archived: bool = False


class ProjectSeries(ProjectSeriesCreateInput):
    id: str
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DataLibraryDataset(BaseModel):
    dataset_revision: DatasetRevision
    data_asset: DataAsset
    profile_revision: ProfileRevision
    supported_task_ids: list[str]
    dataset_views: list[DatasetViewRevision] = Field(default_factory=list)


class ProjectCreationOptions(BaseModel):
    datasets: list[DataLibraryDataset]
    dataset_views: list[DatasetViewRevision]
    model_packages: list[ModelPackageRef]
    project_series: list[ProjectSeries]


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
    categorical: dict[str, str] = Field(default_factory=dict)
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
    provenance: CandidateProvenance = Field(default_factory=lambda: DirectSourceRef(source_kind="direct"))


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
    task_id: Annotated[str, Field(min_length=1)] = "annealed-properties-v1"
    target_values: dict[str, float] = Field(default_factory=dict)
    input_ranges: dict[str, InputRange] = Field(default_factory=dict)
    response_curve_ranges: dict[str, dict[str, InputRange]] = Field(default_factory=dict)
    heat_stage_positions_m: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=dict)
    display_decimals: dict[str, Annotated[int, Field(ge=0, le=8)]] = Field(default_factory=dict)
    notes: str = ""
    decision_candidate_id: Annotated[str, Field(max_length=80)] = ""
    decision_snapshot_id: Annotated[str, Field(max_length=80)] = ""
    decision_note: Annotated[str, Field(max_length=500)] = ""

    @field_validator("target_values")
    @classmethod
    def targets_are_supported_and_finite(cls, value: dict[str, float]) -> dict[str, float]:
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


class ProjectCreateInput(ProjectInput):
    initial_candidate: CandidateInput | None = None
    dataset_view_revision_id: str | None = None
    task_contract_digest: str = ""
    model_package_ref_id: str | None = None
    model_package_manifest_digest: str = ""
    project_series_id: str | None = None
    predecessor_project_id: str | None = None
    continuation_reason: str = ""


class ProjectUpdateInput(BaseModel):
    """Fields that may change without changing a Project's scientific identity."""

    name: Annotated[str, Field(min_length=1, max_length=120)] = "焼鈍条件の候補検討"
    description: str = ""
    purpose: str = ""
    target_values: dict[str, float] = Field(default_factory=dict)
    input_ranges: dict[str, InputRange] = Field(default_factory=dict)
    response_curve_ranges: dict[str, dict[str, InputRange]] = Field(default_factory=dict)
    heat_stage_positions_m: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=dict)
    display_decimals: dict[str, Annotated[int, Field(ge=0, le=8)]] = Field(default_factory=dict)
    notes: str = ""
    decision_candidate_id: Annotated[str, Field(max_length=80)] = ""
    decision_snapshot_id: Annotated[str, Field(max_length=80)] = ""
    decision_note: Annotated[str, Field(max_length=500)] = ""
    # Accepted only to detect stale/full-object clients. The service verifies
    # these values and never persists them through the update path.
    task_id: str | None = None
    dataset_view_revision_id: str | None = None
    task_contract_digest: str | None = None
    model_package_ref_id: str | None = None
    model_package_manifest_digest: str | None = None
    project_series_id: str | None = None
    predecessor_project_id: str | None = None

    @field_validator("target_values")
    @classmethod
    def targets_are_finite(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(target) for target in value.values()):
            raise ValueError("目標値は有限の数値にしてください")
        return value

    @model_validator(mode="after")
    def decision_note_requires_candidate(self) -> "ProjectUpdateInput":
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
    dataset_view_revision_id: str | None = None
    task_contract_digest: str = ""
    model_package_ref_id: str | None = None
    model_package_manifest_digest: str = ""
    project_series_id: str | None = None
    predecessor_project_id: str | None = None
    continuation_reason: str = ""
    binding_provenance: Literal["explicit", "assumed_current_at_upgrade", "unbound_legacy"] = "unbound_legacy"
    binding_migrated_at: datetime | None = None
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
    base_inputs: CandidateInputs
    variables: Annotated[dict[str, ScreeningVariable], Field(min_length=1)]
    samples: Annotated[int, Field(ge=48, le=128)] = 64
    target: Annotated[str, Field(min_length=1)] = "TS"
    target_value: float | None = None
    secondary_targets: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def variables_match_their_fields(self) -> "ScreeningRequest":
        if self.target_value is not None and not math.isfinite(self.target_value):
            raise ValueError("target_valueは有限の数値にしてください")
        if any(not math.isfinite(value) for value in self.secondary_targets.values()):
            raise ValueError("secondary_targetsは有限の数値にしてください")
        return self


class ActualMeasurementInput(BaseModel):
    property: Literal["TS", "YS", "EL", "lambda", "VB_mean", "VB_max"]
    mean: Annotated[float, Field(allow_inf_nan=False)]
    std: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0
    replicates: Annotated[int, Field(ge=1, le=999)] = 1
    unit: Literal["MPa", "%", "µm"]
    experiment_no: str = ""
    measured_at: date | None = None
    note: str = ""

    @model_validator(mode="after")
    def unit_matches_property(self) -> "ActualMeasurementInput":
        expected = {"TS": "MPa", "YS": "MPa", "EL": "%", "lambda": "%", "VB_mean": "µm", "VB_max": "µm"}[self.property]
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
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    point_statistic: Literal["mean", "median", "probability", "rate", "expected_category"]
    predictive_family: str
    quantiles: dict[str, float]
    categories: list[str] = Field(default_factory=list)
    goal_value: float | None = None
    goal_probability: Annotated[float | None, Field(ge=0, le=1)] = None
    goal_direction: Literal["at_least", "at_most"] | None = None
    uncertainty_components: dict[str, float] | None = None

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_snapshot_semantics(cls, value: Any) -> Any:
        """Read immutable v1 snapshots without pretending they were re-predicted."""
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        migrated.setdefault("target_kind", "continuous")
        migrated.setdefault("point_statistic", "mean")
        migrated.setdefault("predictive_family", "empirical_quantiles")
        if "quantiles" not in migrated:
            quantiles: dict[str, float] = {}
            if "lower" in migrated:
                quantiles["0.05"] = migrated["lower"]
            if "upper" in migrated:
                quantiles["0.95"] = migrated["upper"]
            migrated["quantiles"] = quantiles
        return migrated


class RepeatSummary(BaseModel):
    mean: float
    std: float
    n: int


class SimilarObservation(BaseModel):
    observation_id: str = ""
    observation_ids: list[str] = Field(default_factory=list)
    parent_key: str
    source: str = ""
    layer: Literal["training", "historical"] | None = None
    source_scope: Literal["model_training_data", "project_reference_data"] | None = None
    distance: float
    components: dict[str, float] = Field(default_factory=dict)
    outputs: dict[str, float] = Field(default_factory=dict)
    repeat_summary: dict[str, RepeatSummary] = Field(default_factory=dict)
    melt_key: str | None = None
    process_key: str | None = None
    process_label: str = "工程履歴"


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
    model_support: dict[str, Support] = Field(default_factory=dict)
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


class ProjectDecisionHistory(BaseModel):
    candidate_id: str
    snapshot_id: str
    note: str


class CandidateCurrentHistory(BaseModel):
    revision: int
    updated_at: datetime


class TaskCatalogItem(BaseModel):
    definition: ResolvedTaskDefinition
    starter_candidate: CandidateInput


class SnapshotHistoryItem(BaseModel):
    id: str
    candidate_id: str
    created_at: datetime
    candidate_revision: int | None = None
    prediction_summary: dict[str, Prediction]
    model_ref: ModelMetadata | None = None


class CandidateHistoryItem(BaseModel):
    candidate: Candidate
    current: CandidateCurrentHistory
    snapshots: list[SnapshotHistoryItem]
    actuals: list[ActualMeasurement]
    decision: ProjectDecisionHistory | None = None


class ProjectHistoryResponse(BaseModel):
    project: Project
    candidates: list[CandidateHistoryItem]


class CurvePoint(BaseModel):
    x: float
    value: float
    lower: float
    upper: float
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    point_statistic: Literal["mean", "median", "probability", "rate", "expected_category"]
    predictive_family: str
    quantiles: dict[str, float]
    categories: list[str] = Field(default_factory=list)


class CurveVariable(BaseModel):
    id: str
    label: str
    unit: str
    min: float
    max: float
    current: float
    training_range: InputRange | None = None


class ResponseCurveResponse(BaseModel):
    target: str
    variable: CurveVariable
    points: list[CurvePoint]
    output_range: InputRange | None = None
    point_count: int
    policy_id: str


class CurveFamilySeries(BaseModel):
    level: float | str | None = None
    label: str
    points: list[CurvePoint]


class CurveVariableCategorical(BaseModel):
    id: str
    label: str
    choices: list[str]
    current: str


class CurveFamilyResponse(BaseModel):
    target: str
    axis: CurveVariable
    vary: CurveVariable | None = None
    vary_categorical: CurveVariableCategorical | None = None
    series: list[CurveFamilySeries]
    output_range: InputRange | None = None
    point_count: int
    policy_id: str


class DurationDiagnostic(BaseModel):
    total: float
    last: float
    max: float
    average: float


class OperationDiagnostic(BaseModel):
    runtime_types: list[str]
    hits: int
    misses: int
    coalesced: int
    computations: int
    computation_duration_ms: DurationDiagnostic
    total_duration_ms: DurationDiagnostic


class InferenceDiagnosticsResponse(BaseModel):
    max_entries: int
    cached_entries: int
    operations: dict[str, OperationDiagnostic]


class LineageIndexItem(BaseModel):
    key: str
    entity_type: str
    has_issue: bool
    family: str | None = None
    project: str | None = None
    route: str | None = None
    peak_temperature_c: float | None = None
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
    predictions: dict[str, Prediction] = Field(default_factory=dict)
    color_value: float
    support: Support
    warnings: list[str] = Field(default_factory=list)
    similar: list[SimilarObservation] = Field(default_factory=list)
    score: float | None
    goal_evaluation: "ScreeningGoalEvaluation"
    secondary_goal_evaluations: dict[str, "ScreeningGoalEvaluation"] = Field(default_factory=dict)


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
    schema_version: Literal["screening-run/v1", "screening-run/v2"] = "screening-run/v1"
    id: str
    project_id: str
    created_at: datetime
    seed: int
    base_candidate_id: str
    base_inputs: CandidateInputs | None = None
    base_canonical_input: dict[str, object]
    model_provenance: ModelMetadata
    target: str
    target_value: float | None
    secondary_targets: dict[str, float] = Field(default_factory=dict)
    score_contract: ScreeningScoreContract
    samples: int
    variables: dict[str, ScreeningVariable]
    points: list[ScreeningPoint]
    representative_points: list[ScreeningPoint]


class ScreeningCandidateBatchRequest(BaseModel):
    point_indices: Annotated[list[int], Field(min_length=1, max_length=10)]


class ScreeningCandidateBatchResponse(BaseModel):
    candidates: list[Candidate]
    skipped_point_indices: list[int] = Field(default_factory=list)


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
        "candidate_provenance_immutable",
        "project_task_locked",
        "protected_project",
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
    focus_entity_key: str | None
    related_entity_keys: list[str]
    missing_reference_key: str | None
    suggested_view: Literal["lineage", "source_sheet"]


class QualityScenario(BaseModel):
    model_config = ConfigDict(extra="allow")
    scenario_id: str
    category: str = Field(alias="分類")
    target_key: str = Field(alias="対象キー")
    target_sheet: str = Field(alias="対象シート")
    expected_insight: str = Field(alias="期待する気づき")


class DatasetIdentity(BaseModel):
    task_id: str
    source_path: str
    source_sha256: str
    profile_id: str
    profile_path: str


class QualityResponse(BaseModel):
    # Legacy scenario fields remain until the UI is switched to detected issues.
    total: int
    by_category: dict[str, int]
    issues: list[QualityScenario]
    reference_scenarios: list[QualityScenario]
    detected_total: int
    detected_by_type: dict[str, int]
    detected_issues: list[DataQualityIssue]
    dataset: DatasetIdentity


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
    visible_node_count: int
    total_node_count: int
    node_limit: int
    has_more: bool
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
