from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from decision_workbench.contracts.batch_proposal_contracts import BatchProposalDefinition
from decision_workbench.contracts.candidate_project_contracts import CandidateInputs, HeatPoint
from decision_workbench.contracts.objective_contracts import ObjectiveDefinition
from decision_workbench.contracts.proposal_contracts import ProposalStrategyRequest
from decision_workbench.contracts.missingness_contracts import (
    InputMissingnessEvidence,
)
from decision_workbench.contracts.sampling_identity_contracts import SamplingEvidence

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


DEFAULT_SCREENING_SEED = 20260719
SCREENING_POOL_MULTIPLIER = 4


class ScreeningGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["at_least", "at_most", "between"]
    lower: float | None = None
    upper: float | None = None

    @model_validator(mode="after")
    def complete_goal(self) -> "ScreeningGoal":
        values = [value for value in (self.lower, self.upper) if value is not None]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("選別基準は有限の数値にしてください")
        if self.direction == "at_least" and (self.lower is None or self.upper is not None):
            raise ValueError("at_leastにはlowerだけを指定してください")
        if self.direction == "at_most" and (self.upper is None or self.lower is not None):
            raise ValueError("at_mostにはupperだけを指定してください")
        if self.direction == "between" and (
            self.lower is None or self.upper is None or self.lower >= self.upper
        ):
            raise ValueError("betweenにはlower < upperを指定してください")
        return self


class ScreeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal[
        "design_space_map", "goal_search", "experiment_batch"
    ]
    source_run_id: Annotated[str, Field(min_length=1)] | None = None
    base_candidate_id: Annotated[str, Field(min_length=1)]
    base_inputs: CandidateInputs
    variables: Annotated[dict[str, ScreeningVariable], Field(min_length=1)]
    samples: Annotated[int, Field(ge=48, le=128)] = 64
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)] = DEFAULT_SCREENING_SEED
    target: Annotated[str, Field(min_length=1)] = "TS"
    target_goal: ScreeningGoal | None = None
    secondary_goals: dict[str, ScreeningGoal] = Field(default_factory=dict)
    objective_definition: ObjectiveDefinition | None = None
    proposal: ProposalStrategyRequest = ProposalStrategyRequest()
    batch_definition: BatchProposalDefinition | None = None

    @model_validator(mode="after")
    def target_is_not_a_secondary_goal(self) -> "ScreeningRequest":
        if self.target in self.secondary_goals:
            raise ValueError("主目標を副条件にも指定することはできません")
        if (
            self.batch_definition is not None
            and self.batch_definition.batch_size > self.samples
        ):
            raise ValueError("batch sizeは評価点数以下にしてください")
        if (
            self.batch_definition is not None
            and "candidate_pool_size" in self.batch_definition.model_fields_set
            and self.batch_definition.candidate_pool_size > self.samples
        ):
            raise ValueError("batch candidate poolは評価点数以下にしてください")
        if self.batch_definition is not None and any(
            item.candidate_revision is None
            for item in self.batch_definition.controls
        ):
            raise ValueError("exact Controlには選択時点のcandidate revisionが必要です")
        if self.purpose == "design_space_map":
            if self.target_goal is not None or self.secondary_goals:
                raise ValueError("領域を見るRunには目標を指定しません")
            if self.objective_definition is not None:
                raise ValueError("領域を見るRunにはObjectiveを指定しません")
            if self.batch_definition is not None or self.source_run_id is not None:
                raise ValueError("領域を見るRunにはbatch元を指定しません")
            if self.proposal.support_policy != "allow_with_warning":
                raise ValueError("領域を見るRunは学習範囲外も地図へ含めてください")
        elif self.purpose == "goal_search":
            if self.batch_definition is not None or self.source_run_id is not None:
                raise ValueError("有望候補Runにはbatch元を指定しません")
        elif self.batch_definition is None or self.source_run_id is None:
            raise ValueError("実験バッチには元の有望候補Runとbatch定義が必要です")
        return self


class ActualMeasurementInput(BaseModel):
    property: Annotated[str, Field(min_length=1)]
    # `mean` remains the canonical numeric representation used by existing
    # continuous snapshots.  `value` carries an observed event/category when a
    # task has non-numeric output semantics; RecordService resolves it against
    # the TaskDefinition before persistence.
    mean: Annotated[float | None, Field(default=None, allow_inf_nan=False)] = None
    value: float | str | bool | None = None
    value_label: str | None = None
    std: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0
    replicates: Annotated[int, Field(ge=1, le=999)] = 1
    unit: Annotated[str, Field(min_length=1)]
    experiment_no: str = ""
    measured_at: date | None = None
    note: str = ""

    @model_validator(mode="after")
    def has_one_observed_value(self) -> "ActualMeasurementInput":
        if (self.mean is None) == (self.value is None):
            raise ValueError("実測にはmeanまたは意味付きvalueを一つ指定します")
        if self.value_label is not None and type(self) is ActualMeasurementInput:
            raise ValueError("value_labelはTask契約から決まるため指定できません")
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
    interval_method: Literal["conformal", "quantile", "parametric", "bayesian"] | None = None
    interval_coverage_level: float | None = Field(default=None, gt=0, lt=1)
    interval_calibration_dataset_digest: Annotated[
        str | None, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ] = None
    interval_calibration_sample_count: int | None = Field(default=None, ge=1)
    interval_wrapper_id: Annotated[str | None, Field(min_length=1)] = None
    interval_wrapper_version: Annotated[str | None, Field(min_length=1)] = None
    interval_wrapper_manifest_digest: Annotated[
        str | None, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ] = None
    interval_calibration_score_artifact_digest: Annotated[
        str | None, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ] = None
    categories: list[str] = Field(default_factory=list)
    goal_value: float | None = None
    goal_lower: float | None = None
    goal_upper: float | None = None
    goal_probability: Annotated[float | None, Field(ge=0, le=1)] = None
    goal_direction: Literal["at_least", "at_most", "between"] | None = None
    uncertainty_components: dict[str, float] | None = None
    sampling_identity: SamplingEvidence | None = None

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

    @model_validator(mode="after")
    def conformal_identity_is_complete_and_exclusive(self) -> "Prediction":
        conformal_evidence = (
            self.interval_calibration_dataset_digest,
            self.interval_calibration_sample_count,
            self.interval_wrapper_id,
            self.interval_wrapper_version,
            self.interval_wrapper_manifest_digest,
            self.interval_calibration_score_artifact_digest,
        )
        if self.interval_method == "conformal":
            if self.interval_coverage_level is None or any(item is None for item in conformal_evidence):
                raise ValueError("conformal interval requires complete calibration and wrapper identity")
            if self.goal_probability is not None:
                raise ValueError("conformal interval must not manufacture goal probability")
        elif any(item is not None for item in conformal_evidence):
            raise ValueError("only conformal intervals carry calibration or wrapper identity")
        return self


class RepeatSummary(BaseModel):
    mean: float
    std: float
    n: int


class CandidateOriginEvidence(BaseModel):
    candidate_id: str
    task_id: str
    process_key: str
    composition_key: str | None = None
    relation_context_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    repeat_summary: dict[str, RepeatSummary] = Field(default_factory=dict)


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
    relation_context_ids: list[str] = Field(default_factory=list)


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
    predictor_runtime_types: dict[str, str] = Field(default_factory=dict)


class FeaturePipelineIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = ""
    version: str = ""
    digest: str = ""
    input_schema_version: str = ""
    features: list[str] = Field(default_factory=list)


class TrainingDataIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_path: str = ""
    source_sha256: str = ""
    training_data_id: str = ""
    feature_dataset_id: str = ""
    training_code_revision: str = ""
    records: dict[str, int] = Field(default_factory=dict)


class SourceLifecycleIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: str
    connector_configuration_digest: str
    source_adapter_id: str
    source_adapter_version: str
    raw_snapshot_id: str
    raw_snapshot_digest: str
    recipe_id: str
    recipe_digest: str
    curation_run_id: str
    curation_digest: str
    profile_revision_id: str
    profile_digest: str
    canonical_dataset_revision_id: str
    canonical_dataset_digest: str
    training_snapshot_id: str
    training_snapshot_digest: str
    training_selection_policy_digest: str
    materialization_adapter_id: str
    materialization_adapter_version: str
    materialized_training_sha256: str
    row_count: int


class PredictionIntervalIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    method: str = ""
    coverage: str | float | dict[str, float] | None = None
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
    source_lifecycle: SourceLifecycleIdentity | None = None
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
    input_completeness: Literal[
        "complete", "imputed", "native_missing", "blocked"
    ] = "complete"
    prediction_status: Literal["final", "provisional", "blocked"] = "final"
    input_missingness: InputMissingnessEvidence | None = None

    @model_validator(mode="after")
    def missingness_status_has_one_authority(self) -> "PredictionResponse":
        if self.input_missingness is None:
            if (
                self.input_completeness != "complete"
                or self.prediction_status != "final"
            ):
                raise ValueError(
                    "non-final prediction status requires input missingness evidence"
                )
            return self
        if (
            self.input_completeness
            != self.input_missingness.input_completeness
            or self.prediction_status
            != self.input_missingness.prediction_status
        ):
            raise ValueError(
                "prediction status must match input missingness evidence"
            )
        return self


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
    interval_coverage_method: str | None = None
    interval_coverage_observations: int | None = None


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
    classification_diagnostics: dict[str, dict[str, float]] = Field(default_factory=dict)


class TrainingDataColumn(BaseModel):
    key: str
    label: str
    unit: str | None = None
    group: Literal["識別", "原値", "正規化", "判定", "入力", "特徴量", "実測"]


class TrainingDataRow(BaseModel):
    observation_id: str
    parent_key: str
    values: dict[str, str | float | int | bool | None]


class ModelTrainingCurationTargetSummary(BaseModel):
    target: str
    usable_rows: int
    source_groups: int
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)


class ModelTrainingCurationSummary(BaseModel):
    source_rows: int
    input_usable_rows: int
    accepted_rows: int
    warning_rows: int
    quarantined_rows: int
    blocked_rows: int
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)
    targets: list[ModelTrainingCurationTargetSummary]


class ModelTrainingDataPage(BaseModel):
    stage: Literal["curation", "selected", "features"]
    target: str
    target_label: str
    source_data_digest: str
    feature_dataset_digest: str
    feature_pipeline_id: str
    feature_pipeline_version: str
    training_unit: Literal[
        "individual_observation",
        "parent_condition_mean",
        "replicate_context_mean",
        "source_row",
        "independent source row",
        "source_row_grouped_by_parent",
        "wear_measurement_row",
    ]
    stage_counts: "ModelTrainingStageCounts"
    total: int
    parent_conditions: int
    offset: int
    limit: int
    columns: list[TrainingDataColumn]
    rows: list[TrainingDataRow]
    curation_summary: ModelTrainingCurationSummary


class ModelTrainingStageCounts(BaseModel):
    source_rows: int
    selected_rows: int
    model_rows: int


class OutputSpaceObservedValue(BaseModel):
    mean: float
    std: float
    min: float
    max: float
    count: int
    observation_ids: list[str]


class OutputSpaceEvidencePoint(BaseModel):
    context_id: str
    parent_key: str
    process_key: str | None = None
    composition_key: str | None = None
    relation_context_ids: list[str] = Field(default_factory=list)
    pairing_relationship: Literal[
        "same_observations",
        "overlapping_observations",
        "distinct_observations",
    ]
    x: OutputSpaceObservedValue
    y: OutputSpaceObservedValue
    distance: float
    distance_status: Literal["supported", "caution", "extrapolated"]


class OutputSpaceEvidenceResponse(BaseModel):
    x_target: str
    y_target: str
    evidence_context: Literal["training_context", "parent_condition"]
    pairing_unit: Literal["condition_mean"] = "condition_mean"
    source_scope: Literal["model_training_data"] = "model_training_data"
    source_data_digest: str
    candidate_id: str
    candidate_revision: int
    distance_method: str
    distance_version: str
    cohort_digest: str
    supported_threshold: float
    caution_threshold: float
    filter: Literal["supported", "caution", "all"]
    eligible_contexts: int
    sampling_policy: Literal["task_distance"]
    total_contexts: int
    returned_contexts: int
    truncated: bool
    points: list[OutputSpaceEvidencePoint]


class InputSpaceTrainingPoint(BaseModel):
    context_id: str
    parent_key: str
    process_key: str | None = None
    composition_key: str | None = None
    relation_context_ids: list[str] = Field(default_factory=list)
    observation_ids: list[str] = Field(default_factory=list)
    repeat_summary: dict[str, RepeatSummary] = Field(default_factory=dict)
    x: float
    y: float
    landmark: bool


class InputSpaceCandidatePoint(BaseModel):
    candidate_id: str
    candidate_revision: int
    label: str
    x: float
    y: float
    island_distance: float
    island_status: Literal["supported", "caution", "extrapolated"]
    nearest_training_context_id: str
    candidate_novelty: float | None = None
    nearest_candidate_id: str | None = None


class InputSpaceEmbeddingResponse(BaseModel):
    source_scope: Literal["model_training_data"] = "model_training_data"
    source_data_digest: str
    distance_target_key: str
    evidence_context: Literal["training_context", "parent_condition"]
    distance_method: str
    distance_version: str
    cohort_digest: str
    vector_space_digest: str
    supported_threshold: float
    caution_threshold: float
    embedding_method: Literal["landmark-classical-mds-oos"]
    embedding_version: Literal["1.0.0"]
    seed: int
    landmark_count: int
    total_training_contexts: int
    displayed_training_contexts: int
    captured_positive_eigenvalue_ratio: float
    selected_candidate_id: str
    selected_candidate_revision: int
    training_points: list[InputSpaceTrainingPoint]
    candidate_points: list[InputSpaceCandidatePoint]
