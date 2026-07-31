from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from decision_workbench.contracts.task_contracts import TaskDefinition

PROFILE_SCHEMA_VERSION = "dataset-input-profile/v2"

class DatasetProfileError(ValueError):
    """A preflight failure.  All independently detectable problems are reported."""

    def __init__(self, errors: Iterable[str]):
        self.errors = tuple(dict.fromkeys(str(error) for error in errors))
        super().__init__("Dataset input preflight failed:\n- " + "\n- ".join(self.errors))


@dataclass(frozen=True)
class UnitConversion:
    source: str
    canonical: str
    scale: float = 1.0
    offset: float = 0.0


_UNIT_REGISTRY = {
    ("mass%", "mass%"): UnitConversion("mass%", "mass%"),
    ("mass%", "%"): UnitConversion("mass%", "%"),
    ("%", "%"): UnitConversion("%", "%"),
    ("MPa", "MPa"): UnitConversion("MPa", "MPa"),
    ("Mpa", "MPa"): UnitConversion("Mpa", "MPa"),
    ("kPa", "MPa"): UnitConversion("kPa", "MPa", scale=0.001),
    ("mm", "mm"): UnitConversion("mm", "mm"),
    ("min", "min"): UnitConversion("min", "min"),
    ("min", "s"): UnitConversion("min", "s", scale=60.0),
    ("s", "s"): UnitConversion("s", "s"),
    ("秒", "s"): UnitConversion("秒", "s"),
    ("m/min", "m/min"): UnitConversion("m/min", "m/min"),
    ("m/min", "mpm"): UnitConversion("m/min", "mpm"),
    ("mpm", "mpm"): UnitConversion("mpm", "mpm"),
    ("℃", "°C"): UnitConversion("℃", "°C"),
    ("°C", "°C"): UnitConversion("°C", "°C"),
    ("degC", "°C"): UnitConversion("degC", "°C"),
    ("K", "°C"): UnitConversion("K", "°C", offset=-273.15),
    ("℃/s", "°C/s"): UnitConversion("℃/s", "°C/s"),
    ("°C/s", "°C/s"): UnitConversion("°C/s", "°C/s"),
    ("degC/s", "°C/s"): UnitConversion("degC/s", "°C/s"),
    ("-", "1"): UnitConversion("-", "1"),
    ("1", "1"): UnitConversion("1", "1"),
    ("-", "-"): UnitConversion("-", "-"),
    ("HV", "HV"): UnitConversion("HV", "HV"),
    ("deg", "deg"): UnitConversion("deg", "deg"),
    ("mm/rev", "mm/rev"): UnitConversion("mm/rev", "mm/rev"),
    ("m", "m"): UnitConversion("m", "m"),
    ("cm", "m"): UnitConversion("cm", "m", scale=0.01),
    ("µm", "µm"): UnitConversion("µm", "µm"),
}
_HEADER_UNIT = re.compile(r"\[([^\[\]]+)\]\s*$")


def _header_unit(column: str) -> str | None:
    match = _HEADER_UNIT.search(column)
    if match:
        return match.group(1)
    if column.endswith("%") and len(column) > 1:
        return "%"
    return None


def unit_conversion(source: str | None, canonical: str | None) -> UnitConversion | None:
    if source is None and canonical is None:
        return UnitConversion("", "")
    if source is None or canonical is None:
        return None
    return _UNIT_REGISTRY.get((source, canonical))


def source_units_for(canonical: str | None) -> tuple[str, ...]:
    """Return the explicit source-unit choices supported for a canonical unit."""

    if canonical is None:
        return ()
    return tuple(sorted(source for source, target in _UNIT_REGISTRY if target == canonical))


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SeriesColumns(ProfileModel):
    parent: str
    order: str
    time: str
    value: str
    time_source_unit: str
    time_canonical_unit: str
    value_source_unit: str
    value_canonical_unit: str


class ObservationSource(ProfileModel):
    role: str
    column: str


class MeasurementPointSeriesFallback(ProfileModel):
    """Build a heat series from line speed and temperatures at known positions."""

    source_role: str
    master_role: str
    line_speed_path: str
    equipment_column: str
    equipment_value: str
    order_column: str
    stage_column: str
    position_column: str
    position_unit: Literal["m"]
    temperature_column_template: str
    temperature_source_unit: str
    temperature_canonical_unit: str

    @field_validator("temperature_column_template")
    @classmethod
    def template_contains_stage(cls, value: str) -> str:
        remainder = value.replace("{stage}", "", 1)
        if value.count("{stage}") != 1 or "{" in remainder or "}" in remainder:
            raise ValueError("temperature_column_template must contain exactly one {stage} placeholder")
        return value


class FieldMapping(ProfileModel):
    path: str
    role: str
    column: str | None = None
    kind: Literal["entity_scalar", "observation_scoped", "categorical", "ordered_heat_series"]
    source_unit: str | None = None
    canonical_unit: str | None = None
    normalizer_id: Literal["median_by_parent/v1", "stage_local_clock/v1"] | None = None
    series_columns: SeriesColumns | None = None
    parent_entity_type: str | None = None
    observation_sources: tuple[ObservationSource, ...] = ()
    measurement_point_fallback: MeasurementPointSeriesFallback | None = None


class ObservationTarget(ProfileModel):
    key: str
    column: str | None = None
    columns: tuple[str, ...] = ()
    unit: str

    @model_validator(mode="after")
    def exactly_one_column_contract(self) -> "ObservationTarget":
        if bool(self.column) == bool(self.columns):
            raise ValueError("observation target requires either column or ordered columns")
        if self.columns and (len(set(self.columns)) != len(self.columns) or any(not value.strip() for value in self.columns)):
            raise ValueError("observation target columns must be unique and non-empty")
        return self

    @property
    def source_columns(self) -> tuple[str, ...]:
        return (self.column,) if self.column else self.columns


class ObservationMapping(ProfileModel):
    role: str
    id_column: str
    parent_column: str | None = None
    parent_entity_type: str
    metadata_columns: Mapping[str, str]
    targets: tuple[ObservationTarget, ...]
    auxiliary: tuple[ObservationTarget, ...]
    optional_metadata_keys: tuple[str, ...] = ()
    optional_auxiliary_keys: tuple[str, ...] = ()


class EntityMapping(ProfileModel):
    type: str
    role: str
    key: str


class RelationJoin(ProfileModel):
    path: str
    entity_type: str
    column: str
    alternate_columns: tuple[str, ...] = ()
    cardinality: Literal["zero_or_one", "exactly_one"]
    stage: int = Field(ge=0)
    parent_entity_types: tuple[str, ...] = ()
    edge_parent_entity_types: tuple[str, ...] | None = None
    parent_consistency: Literal["allow_many", "exactly_one"] = "allow_many"

    @property
    def source_columns(self) -> tuple[str, ...]:
        return (self.column, *self.alternate_columns)


class RelationMapping(ProfileModel):
    role: str
    joins: tuple[RelationJoin, ...]


class PolicyColumnMapping(ProfileModel):
    role: str
    column: str
    policy: Literal["learning_flag/v1", "anneal_feature_status/v1", "valid_observation/v1"]
    accepted_values: Annotated[tuple[str, ...], Field(min_length=1)]

    @field_validator("accepted_values")
    @classmethod
    def accepted_values_are_unique_and_nonempty(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value.strip() for value in values) or len(values) != len(set(values)):
            raise ValueError("policy accepted_values must be unique and non-empty")
        return values


class TechnicalColumnMapping(ProfileModel):
    role: str
    name: str
    column: str


class SourceMarker(ProfileModel):
    """Values that identify a source flow sharing the same sheet contract."""

    sheet: str
    column: str
    values: Annotated[tuple[str, ...], Field(min_length=1)]


class StageMapping(ProfileModel):
    """Map a source-specific stage label to a stable visualization category."""

    raw_name: str
    category: str


class SharedProfile(ProfileModel):
    sheets: Mapping[str, str]
    column_aliases: Mapping[str, Mapping[str, str]] = Field(default_factory=dict)
    entities: tuple[EntityMapping, ...]
    relation: RelationMapping
    eligibility: tuple[PolicyColumnMapping, ...]
    policy_defaults: Mapping[str, bool] = {}
    technical: tuple[TechnicalColumnMapping, ...]
    optional_roles: tuple[str, ...] = ()
    optional_technical_fields: tuple[str, ...] = ()
    metadata_columns: tuple[str, ...]
    physical_ranges: Mapping[str, Mapping[str, tuple[float, float]]]


class TaskProfile(ProfileModel):
    task_id: str
    mappings: tuple[FieldMapping, ...]
    observations: tuple[ObservationMapping, ...]

    def mapping(self, path: str) -> FieldMapping:
        matches = [item for item in self.mappings if item.path == path]
        if len(matches) != 1:
            raise DatasetProfileError([f"{self.task_id}: canonical field {path!r} must have exactly one source mapping"])
        return matches[0]


class DatasetInputProfile(ProfileModel):
    profile_id: str
    shared: SharedProfile
    tasks: Mapping[str, TaskProfile]
    task_definitions: Mapping[str, TaskDefinition]
    source_markers: tuple[SourceMarker, ...] = ()
    stage_mappings: tuple[StageMapping, ...] = ()

    def sheet_for_role(self, role: str) -> str:
        sheets = self.shared.sheets
        if role not in sheets:
            raise DatasetProfileError([f"unknown dataset role: {role}"])
        return str(sheets[role])

    def source_column_for(self, role: str, canonical_column: str) -> str:
        """Return the physical workbook column declared for one canonical column."""

        return str(self.shared.column_aliases.get(role, {}).get(canonical_column, canonical_column))

    def stage_category_for(self, raw_name: Any) -> str | None:
        value = str(raw_name or "")
        match = next((item for item in self.stage_mappings if item.raw_name == value), None)
        return match.category if match else None
