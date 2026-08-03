from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    HeatPoint,
    InputRange,
    Project,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ChainSnapshot,
)
from decision_workbench.contracts.chain_uncertainty_contracts import ChainDistributionRun
from decision_workbench.contracts.prediction_catalog_contracts import (
    ActualMeasurement,
    ModelMetadata,
    Prediction,
    PredictionResponse,
    RepeatSummary,
    Support,
)
from decision_workbench.contracts.subsystem_availability import SubsystemAvailability
from decision_workbench.contracts.sampling_identity_contracts import (
    LegacySamplingIdentityUnavailable,
)
from decision_workbench.contracts.task_contracts import ResolvedTaskDefinition

class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    prediction: PredictionResponse | None = None
    provenance: ModelMetadata | None = None
    project_design_space_digest: str | None = None
    project_design_space_binding_provenance: Literal[
        "explicit", "generated_default", "inherited_predecessor", "unbound_legacy"
    ] = "unbound_legacy"
    sampling_identity_status: LegacySamplingIdentityUnavailable | None = None

    @model_validator(mode="after")
    def mark_unrecorded_sample_based_legacy_evidence(self) -> "SnapshotPayload":
        if self.prediction is None or self.provenance is None:
            return self
        runtime_types = (
            self.provenance.package.runtime_types
            if self.provenance.package is not None
            else []
        )
        if "numpyro.dense_posterior.v1" not in runtime_types:
            return self
        target_runtime_types = self.provenance.package.predictor_runtime_types
        unavailable = LegacySamplingIdentityUnavailable()
        if target_runtime_types:
            for target, prediction in self.prediction.predictions.items():
                if (
                    target_runtime_types.get(target)
                    == "numpyro.dense_posterior.v1"
                    and prediction.sampling_identity is None
                ):
                    prediction.sampling_identity = unavailable
        elif set(runtime_types) == {"numpyro.dense_posterior.v1"}:
            for prediction in self.prediction.predictions.values():
                if prediction.sampling_identity is None:
                    prediction.sampling_identity = unavailable
        else:
            self.sampling_identity_status = unavailable
        return self


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
    chain_snapshots: list[ChainSnapshot] = Field(default_factory=list)
    chain_analysis_variants: list[ActualConditionedVariant] = Field(
        default_factory=list
    )
    chain_distribution_runs: list[ChainDistributionRun] = Field(
        default_factory=list
    )
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
    interval_method: Literal["conformal", "quantile", "parametric", "bayesian"] | None = None
    interval_coverage_level: float | None = Field(default=None, gt=0, lt=1)
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


class ResponseContourCell(BaseModel):
    x: float
    y: float
    prediction: Prediction | None = None
    support: Support | None = None
    displayable: bool = False
    invalid_reason: str = ""

    @model_validator(mode="after")
    def valid_cells_have_evidence(self) -> "ResponseContourCell":
        if self.invalid_reason:
            if self.prediction is not None or self.support is not None or self.displayable:
                raise ValueError("invalid contour cells cannot carry prediction evidence")
        elif self.prediction is None or self.support is None:
            raise ValueError("valid contour cells require prediction and support evidence")
        elif self.displayable != (self.support.status != "extrapolated"):
            raise ValueError("contour displayability must follow support status")
        if not math.isfinite(self.x) or not math.isfinite(self.y):
            raise ValueError("response contour coordinates must be finite")
        return self


class ResponseContourResponse(BaseModel):
    task_id: str
    candidate_id: str
    candidate_revision: Annotated[int, Field(ge=1)]
    model_package_manifest_digest: str
    target: str
    x_axis: CurveVariable
    y_axis: CurveVariable
    x_values: list[float]
    y_values: list[float]
    cells: list[ResponseContourCell]
    output_range: InputRange | None = None
    grid_shape: tuple[int, int]
    policy_id: Literal["training-range-supported-grid-v1"]

    @model_validator(mode="after")
    def grid_is_rectangular(self) -> "ResponseContourResponse":
        rows, columns = self.grid_shape
        if rows < 2 or columns < 2:
            raise ValueError("response contour grid requires at least two points per axis")
        if rows != len(self.y_values) or columns != len(self.x_values):
            raise ValueError("response contour grid shape does not match its axes")
        if len(self.cells) != rows * columns:
            raise ValueError("response contour cell count does not match its grid")
        if not all(math.isfinite(value) for value in [*self.x_values, *self.y_values]):
            raise ValueError("response contour axis values must be finite")
        if any(left >= right for left, right in zip(self.x_values, self.x_values[1:])):
            raise ValueError("response contour x values must be strictly increasing")
        if any(left >= right for left, right in zip(self.y_values, self.y_values[1:])):
            raise ValueError("response contour y values must be strictly increasing")
        for index, cell in enumerate(self.cells):
            expected_x = self.x_values[index % columns]
            expected_y = self.y_values[index // columns]
            if not math.isclose(cell.x, expected_x) or not math.isclose(cell.y, expected_y):
                raise ValueError("response contour cell coordinates do not match its axes")
        return self


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
    melt_keys: list[str] = Field(default_factory=list)
    project: str | None = None
    route: str | None = None
    peak_temperature_c: float | None = None
    learning_status: str | None = None
    has_observation: bool | None = None
    observation_summary: dict[str, RepeatSummary] | None = None
    review_status: Literal["noted", "later", "accepted", "needs_fix", "hidden"] | None = None
    review_note: str | None = None


class LineageIndexResponse(BaseModel):
    items: list[LineageIndexItem]
    matched_entities: int
    total_entities: int
    relation_rows: int
    detected_issues: int
    counts_by_type: dict[str, int]
    review_count: int


class LineageNodeReviewInput(BaseModel):
    entity_type: Annotated[str, Field(min_length=1, max_length=120)]
    status: Literal["noted", "later", "accepted", "needs_fix", "hidden"]
    note: Annotated[str, Field(max_length=1000)] = ""


class LineageNodeReview(LineageNodeReviewInput):
    project_id: str
    entity_key: str
    created_at: datetime
    updated_at: datetime


class LineageNodeReviewList(BaseModel):
    items: list[LineageNodeReview]
    counts_by_status: dict[str, int]

class ScreeningCandidateBatchRequest(BaseModel):
    point_indices: Annotated[list[int], Field(min_length=1, max_length=10)]


class ScreeningCandidateBatchResponse(BaseModel):
    candidates: list[Candidate]
    skipped_point_indices: list[int] = Field(default_factory=list)


class PredictionComparison(BaseModel):
    actual: ActualMeasurement
    snapshot_id: str
    snapshot_created_at: datetime
    candidate_revision: int | None = None
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
        "chain_project_requires_chain_candidate_api",
        "project_task_locked",
        "project_group_conflict",
        "protected_project",
        "project_has_successors",
        "project_has_derived_candidates",
        "project_archived",
        "active_project_purge",
        "project_purge_confirmation_mismatch",
        "sample_has_saved_work",
        "screening_run_referenced",
        "data_integrity_error",
        "validation_error",
        "response_curve_not_applicable",
        "response_curve_training_range_unavailable",
        "batch_feasibility_infeasible",
        "batch_greedy_search_exhausted",
        "runtime_unavailable",
        "subsystem_unavailable",
        "task-store-unconfigured",
        "task-store-unavailable",
        "task-id-invalid",
        "model-store-unconfigured",
        "model-store-unavailable",
        "package-id-invalid",
        "task-id-conflict",
    ]
    message: str
    next_action: str | None = None
    field_errors: list[FieldError] = Field(default_factory=list)
    current_candidate: Candidate | None = None
    availability: SubsystemAvailability | None = None
