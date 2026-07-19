from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


COMPOSITION_ELEMENTS = {
    "C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca"
}
PREDICTION_TARGETS = {"TS", "YS", "EL", "lambda"}


class HeatPoint(BaseModel):
    time_s: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    temperature_c: Annotated[float, Field(ge=-273.15, le=1800)]
    segment_start: bool = False


class CandidateInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80)] = "候補"
    composition: dict[str, float] = Field(default_factory=dict)
    thickness_mm: Annotated[float, Field(gt=0, le=100)] = 1.4
    line_speed_m_min: Annotated[float, Field(gt=0, le=2000)] = 103.0
    coating: Literal["なし", "GI", "GA"] = "なし"
    heat_pattern: list[HeatPoint] = Field(min_length=2, max_length=30)

    @field_validator("composition")
    @classmethod
    def composition_is_known_and_physical(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - COMPOSITION_ELEMENTS)
        if unknown:
            raise ValueError(f"未対応の組成元素です: {', '.join(unknown)}")
        invalid = sorted(name for name, amount in value.items() if not math.isfinite(amount) or amount < 0 or amount > 100)
        if invalid:
            raise ValueError(f"組成は0〜100の有限値にしてください: {', '.join(invalid)}")
        return value

    @model_validator(mode="after")
    def heat_pattern_is_a_real_route(self) -> "CandidateInput":
        times = [point.time_s for point in self.heat_pattern]
        if any(later <= earlier for earlier, later in zip(times, times[1:])):
            raise ValueError("ヒートパターンの時刻は厳密な昇順にしてください")
        return self


class Candidate(CandidateInput):
    id: str
    project_id: str
    created_at: datetime
    updated_at: datetime


class ProjectInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] = "焼鈍条件の候補検討"
    description: str = ""
    purpose: str = ""
    task_id: str = "annealed-properties-v1"
    target_values: dict[str, float] = Field(default_factory=dict)
    notes: str = ""

    @field_validator("target_values")
    @classmethod
    def targets_are_supported_and_finite(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - PREDICTION_TARGETS)
        if unknown:
            raise ValueError(f"未対応の目標特性です: {', '.join(unknown)}")
        if any(not math.isfinite(target) for target in value.values()):
            raise ValueError("目標値は有限の数値にしてください")
        return value


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
    base_candidate_id: str | None = None
    variables: Annotated[dict[str, ScreeningVariable], Field(min_length=1)]
    samples: Annotated[int, Field(ge=48, le=128)] = 64
    target: Literal["TS", "YS", "EL", "lambda"] = "TS"
    target_value: float

    @model_validator(mode="after")
    def variables_match_their_fields(self) -> "ScreeningRequest":
        composition_fields = {"composition.C", "composition.Si", "composition.Mn", "composition.P", "composition.S", "composition.Cr", "composition.Mo", "composition.Ni", "composition.Al", "composition.Ti", "composition.B", "composition.N", "composition.O", "composition.Ca"}
        numeric_fields = composition_fields | {"thickness_mm", "line_speed_m_min", "max_temperature_c"}
        allowed = numeric_fields | {"coating"}
        unknown = sorted(set(self.variables) - allowed)
        if unknown:
            raise ValueError(f"スクリーニング対象外の変数です: {', '.join(unknown)}")
        if not math.isfinite(self.target_value):
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
    candidate_id: str
    mode: Literal["preview", "detailed"]
    predictions: dict[str, Prediction]
    support: Support
    warnings: list[str]
    model_meta: dict[str, object]
    canonical_input: dict[str, object]
    similar: list[dict[str, object]]
    heat_pattern: list[HeatPoint]
    response_curve: list[dict[str, float]] | None = None


class SnapshotResponse(BaseModel):
    id: str
    candidate_id: str
    created_at: datetime
    payload: dict[str, object]


class ApiError(BaseModel):
    detail: str


class DataQualityIssue(BaseModel):
    issue_id: str
    issue_type: Literal["missing_key", "orphan_entity", "duplicate_key", "invalid_reference"]
    source_sheet: str
    entity_key: str
    detail: str


class QualityResponse(BaseModel):
    # Legacy scenario fields remain until the UI is switched to detected issues.
    total: int
    by_category: dict[str, int]
    issues: list[dict[str, Any]]
    reference_scenarios: list[dict[str, Any]]
    detected_total: int
    detected_by_type: dict[str, int]
    detected_issues: list[DataQualityIssue]


class PropertySummary(BaseModel):
    count: int
    min: float
    median: float
    max: float


class LineageNodeDetail(BaseModel):
    key: str
    entity_type: str
    source_sheet: str
    source_row: dict[str, str | float | int | bool | None]
    primary_conditions: dict[str, str | float | int | bool | None]
    composition: dict[str, float]
    heat_pattern: list[HeatPoint]
    connected_observation_count: int
    property_summary: dict[str, PropertySummary]
    related_entities: dict[str, list[str]]


class LineageResponse(BaseModel):
    # key/relations/quality_issues are the existing renderer contract.
    key: str
    relations: dict[str, list[str]]
    quality_issues: list[dict[str, Any]]
    node: LineageNodeDetail


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
