from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Annotated, Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .task_contracts import TaskDefinition


PROFILE_SCHEMA_VERSION = "dataset-input-profile/v2"

_REQUIRED_TECHNICAL_FIELDS = {
    ("melt", "family"),
    ("annealing", "project"),
    ("quality", "category"),
    ("hot_rolling", "equipment"),
    ("anneal_features", "parent_key"),
    ("anneal_features", "max_temperature_c"),
    ("anneal_features", "hold_time_s"),
    ("anneal_features", "input_points"),
    ("anneal_features", "reheat"),
    ("anneal_features", "alloying"),
    ("anneal_features", "feature_status"),
    ("anneal_features", "standard_route"),
    ("anneal_features", "process_signature"),
    ("anneal_features", "unmapped_stage_count"),
}
_KNOWN_OPTIONAL_TECHNICAL_FIELDS = _REQUIRED_TECHNICAL_FIELDS | {
    ("anneal_history", "parent_key"),
    ("anneal_history", "order"),
    ("anneal_history", "time_s"),
    ("anneal_history", "temperature_c"),
    ("anneal_history", "set_temperature_c"),
    ("anneal_history", "stage_category"),
    ("anneal_history", "stage_name"),
    ("anneal_history", "mapping_status"),
}
_REQUIRED_POLICIES = {
    ("hot_rolling", "learning_flag/v1"),
    ("annealing", "learning_flag/v1"),
    ("anneal_features", "anneal_feature_status/v1"),
    ("hot_tensile", "valid_observation/v1"),
    ("anneal_tensile", "valid_observation/v1"),
    ("anneal_hole", "valid_observation/v1"),
}


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
    ("mm", "mm"): UnitConversion("mm", "mm"),
    ("min", "min"): UnitConversion("min", "min"),
    ("s", "s"): UnitConversion("s", "s"),
    ("秒", "s"): UnitConversion("秒", "s"),
    ("m/min", "m/min"): UnitConversion("m/min", "m/min"),
    ("m/min", "mpm"): UnitConversion("m/min", "mpm"),
    ("mpm", "mpm"): UnitConversion("mpm", "mpm"),
    ("℃", "°C"): UnitConversion("℃", "°C"),
    ("°C", "°C"): UnitConversion("°C", "°C"),
    ("degC", "°C"): UnitConversion("degC", "°C"),
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


class EntityMapping(ProfileModel):
    type: str
    role: str
    key: str


class RelationJoin(ProfileModel):
    path: str
    entity_type: str
    column: str
    cardinality: Literal["zero_or_one", "exactly_one"]
    stage: int = Field(ge=0)
    parent_entity_types: tuple[str, ...] = ()
    edge_parent_entity_types: tuple[str, ...] | None = None
    parent_consistency: Literal["allow_many", "exactly_one"] = "allow_many"


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
            raise KeyError(f"unknown dataset role: {role}")
        return str(sheets[role])

    def stage_category_for(self, raw_name: Any) -> str | None:
        value = str(raw_name or "")
        match = next((item for item in self.stage_mappings if item.raw_name == value), None)
        return match.category if match else None


def load_task_definitions(directory: str | Path | None = None) -> dict[str, TaskDefinition]:
    root = Path(directory) if directory else Path(__file__).with_name("task_definitions")
    definitions: dict[str, TaskDefinition] = {}
    for source in sorted(root.glob("*.json")):
        document = json.loads(source.read_text(encoding="utf-8"))
        definition = TaskDefinition.model_validate(document["task_definition"])
        if definition.id in definitions:
            raise DatasetProfileError([f"duplicate production TaskDefinition id {definition.id!r}"])
        definitions[definition.id] = definition
    if not definitions:
        raise DatasetProfileError([f"no production TaskDefinitions found in {root}"])
    return definitions


def load_dataset_profile(
    path: str | Path | None = None,
    task_definitions: Mapping[str, TaskDefinition] | None = None,
) -> DatasetInputProfile:
    profile_path = Path(path) if path else Path(__file__).with_name("dataset-input-profile-v1.json")
    try:
        raw = _load_profile_document(profile_path)
        if raw.get("schema_version") != PROFILE_SCHEMA_VERSION:
            raise DatasetProfileError([f"unsupported profile schema_version: {raw.get('schema_version')!r}"])
        all_definitions = dict(task_definitions or load_task_definitions())
        selected_task_ids = raw.get("task_definition_ids")
        if selected_task_ids is None:
            definitions = all_definitions
        else:
            if not isinstance(selected_task_ids, list) or not all(isinstance(item, str) for item in selected_task_ids):
                raise DatasetProfileError(["task_definition_ids must be a list of strings"])
            if len(selected_task_ids) != len(set(selected_task_ids)):
                raise DatasetProfileError(["task_definition_ids must be unique"])
            unknown_task_ids = sorted(set(selected_task_ids) - set(all_definitions))
            if unknown_task_ids:
                raise DatasetProfileError(["task_definition_ids reference unknown tasks: " + ", ".join(unknown_task_ids)])
            definitions = {task_id: all_definitions[task_id] for task_id in selected_task_ids}
        tasks = {}
        for task_id, value in raw.get("tasks", {}).items():
            if "task_id" in value:
                raise DatasetProfileError(
                    [f"{task_id}: nested task_id is forbidden; the tasks object key is authoritative"]
                )
            tasks[task_id] = TaskProfile.model_validate({**value, "task_id": task_id})
        unexpected = set(raw) - {
            "schema_version", "id", "extends", "task_definition_ids", "shared", "tasks",
            "source_markers", "stage_mappings",
        }
        if unexpected:
            raise DatasetProfileError([f"unknown profile fields: {', '.join(sorted(unexpected))}"])
        profile = DatasetInputProfile(
            profile_id=str(raw["id"]),
            shared=raw["shared"],
            tasks=tasks,
            task_definitions=definitions,
            source_markers=raw.get("source_markers", ()),
            stage_mappings=raw.get("stage_mappings", ()),
        )
    except DatasetProfileError:
        raise
    except (json.JSONDecodeError, KeyError, TypeError, ValidationError) as exc:
        raise DatasetProfileError([f"invalid dataset profile {profile_path}: {exc}"]) from exc
    validate_profile(profile, definitions)
    return profile


def _deep_merge_profile(base: Any, override: Any) -> Any:
    """Merge profile documents while treating arrays as explicit replacements."""
    if isinstance(base, dict) and isinstance(override, dict):
        merged = dict(base)
        for key, value in override.items():
            merged[key] = _deep_merge_profile(merged[key], value) if key in merged else value
        return merged
    return override


def _load_profile_document(path: Path, seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    resolved = path.resolve()
    if resolved in seen:
        chain = " -> ".join(str(item) for item in (*seen, resolved))
        raise DatasetProfileError([f"dataset profile inheritance cycle: {chain}"])
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise DatasetProfileError([f"invalid dataset profile {resolved}: {exc}"]) from exc
    if not isinstance(raw, dict):
        raise DatasetProfileError([f"dataset profile {resolved} must contain a JSON object"])
    extends = raw.get("extends")
    if not extends:
        return raw
    if not isinstance(extends, str) or not extends.strip():
        raise DatasetProfileError([f"dataset profile {resolved} has an invalid extends value"])
    base_path = (resolved.parent / extends).resolve()
    child = {key: value for key, value in raw.items() if key != "extends"}
    return _deep_merge_profile(_load_profile_document(base_path, (*seen, resolved)), child)


def validate_profile(profile: DatasetInputProfile, task_definitions: Mapping[str, Any] | None = None) -> None:
    errors: list[str] = []
    sheets = profile.shared.sheets
    if len(set(sheets.values())) != len(sheets):
        errors.append("shared sheet roles must map to unique source sheets")
    entity_types: set[str] = set()
    entity_roles: set[str] = set()
    for entity in profile.shared.entities:
        identity = (entity.type, entity.key)
        if not all(identity):
            errors.append("shared entity identity requires both type and key")
        if identity[0] in entity_types:
            errors.append(f"duplicate shared entity type: {identity[0]}")
        entity_types.add(identity[0])
        if entity.role in entity_roles:
            errors.append(f"duplicate shared entity role: {entity.role}")
        entity_roles.add(entity.role)
        if entity.role not in sheets:
            errors.append(f"entity {identity[0]} references unknown role {entity.role!r}")
    for join in profile.shared.relation.joins:
        if join.entity_type not in entity_types:
            errors.append(f"relation join references unknown entity type {join.entity_type!r}")
        for parent_type in join.parent_entity_types:
            if parent_type not in entity_types:
                errors.append(f"relation join {join.path!r} references unknown parent entity type {parent_type!r}")
            if parent_type == join.entity_type:
                errors.append(f"relation join {join.path!r} cannot be its own parent")
        if join.edge_parent_entity_types is not None and not set(join.edge_parent_entity_types) <= set(join.parent_entity_types):
            errors.append(f"relation join {join.path!r} edge parents must be declared parent entity types")
    join_paths = [join.path for join in profile.shared.relation.joins]
    join_columns = [join.column for join in profile.shared.relation.joins]
    join_types = [join.entity_type for join in profile.shared.relation.joins]
    if (
        len(join_paths) != len(set(join_paths))
        or len(join_columns) != len(set(join_columns))
        or len(join_types) != len(set(join_types))
    ):
        errors.append("relation join paths, entity types, and columns must be unique")
    if set(join_types) != entity_types:
        missing = entity_types - set(join_types)
        extra = set(join_types) - entity_types
        errors.append(
            "relation joins must cover every shared entity type exactly once"
            + (f"; missing={sorted(missing)}" if missing else "")
            + (f"; extra={sorted(extra)}" if extra else "")
        )
    technical_keys = [(item.role, item.name) for item in profile.shared.technical]
    if len(technical_keys) != len(set(technical_keys)):
        errors.append("technical field role/name pairs must be unique")
    policy_keys = [(item.role, item.policy) for item in profile.shared.eligibility]
    if len(policy_keys) != len(set(policy_keys)):
        errors.append("eligibility role/policy pairs must be unique")
    optional_technical = {
        tuple(value.split(".", 1))
        for value in profile.shared.optional_technical_fields
        if "." in value
    }
    missing_technical = _REQUIRED_TECHNICAL_FIELDS - optional_technical - set(technical_keys)
    missing_technical = {
        item for item in missing_technical
        if item[0] not in profile.shared.optional_roles
    }
    if missing_technical:
        errors.append(
            "legacy canonical adapter is missing technical mappings: "
            + ", ".join(f"{role}.{name}" for role, name in sorted(missing_technical))
        )
    default_policy_keys = {
        tuple(value.split(".", 1))
        for value in profile.shared.policy_defaults
        if "." in value
    }
    missing_policies = _REQUIRED_POLICIES - set(policy_keys) - default_policy_keys
    if missing_policies:
        errors.append(
            "legacy canonical adapter is missing eligibility mappings: "
            + ", ".join(f"{role}.{policy}" for role, policy in sorted(missing_policies))
        )
    if len(profile.shared.metadata_columns) != len(set(profile.shared.metadata_columns)):
        errors.append("shared metadata columns must be unique")
    marker_keys = [(item.sheet, item.column) for item in profile.source_markers]
    if len(marker_keys) != len(set(marker_keys)):
        errors.append("source markers must identify unique sheet/column pairs")
    stage_names = [item.raw_name for item in profile.stage_mappings]
    if len(stage_names) != len(set(stage_names)):
        errors.append("stage mappings must identify unique raw stage names")
    unknown_defaults = set(profile.shared.policy_defaults) - {
        f"{role}.{policy}" for role, policy in _REQUIRED_POLICIES
    }
    if unknown_defaults:
        errors.append("policy_defaults reference unknown policies: " + ", ".join(sorted(unknown_defaults)))
    unknown_optional_fields = set(profile.shared.optional_technical_fields) - {
        f"{role}.{name}" for role, name in _KNOWN_OPTIONAL_TECHNICAL_FIELDS
    }
    if unknown_optional_fields:
        errors.append("optional_technical_fields reference unknown fields: " + ", ".join(sorted(unknown_optional_fields)))
    for role in [profile.shared.relation.role, *(item.role for item in profile.shared.eligibility), *(item.role for item in profile.shared.technical)]:
        if role not in sheets and role not in profile.shared.optional_roles:
            errors.append(f"shared mapping references unknown role {role!r}")
    missing_tasks = set((task_definitions or profile.task_definitions)) - set(profile.tasks)
    for task_id in sorted(missing_tasks):
        errors.append(f"{task_id}: dataset profile is missing for production TaskDefinition")
    for task_id, task in profile.tasks.items():
        definition = (task_definitions or profile.task_definitions).get(task_id)
        if definition is None:
            errors.append(f"{task_id}: production TaskDefinition is missing")
            continue
        ordered_definition_fields = tuple(
            field
            for group in sorted(definition.input_groups, key=lambda item: item.order)
            for field in sorted(group.fields, key=lambda item: item.order)
        )
        declared = {field.path: field for field in ordered_definition_fields}
        observation_roles = [observation.role for observation in task.observations]
        if len(observation_roles) != len(set(observation_roles)):
            errors.append(f"{task_id}: observation roles must be unique")
        seen: set[str] = set()
        for mapping in task.mappings:
            if mapping.path in seen:
                errors.append(f"{task_id}: canonical field {mapping.path!r} has multiple source mappings")
            seen.add(mapping.path)
            if mapping.path not in declared:
                errors.append(f"{task_id}: unknown canonical field {mapping.path!r}")
            if mapping.role not in sheets:
                errors.append(f"{task_id}: mapping {mapping.path!r} references unknown role {mapping.role!r}")
            if mapping.kind == "ordered_heat_series" and (mapping.normalizer_id != "stage_local_clock/v1" or mapping.series_columns is None):
                errors.append(f"{task_id}: ordered heat series {mapping.path!r} requires series columns and a built-in normalizer id")
            if mapping.kind != "ordered_heat_series" and not mapping.column:
                errors.append(f"{task_id}: mapping {mapping.path!r} requires a source column")
            fallback = mapping.measurement_point_fallback
            if fallback is not None:
                if mapping.kind != "ordered_heat_series":
                    errors.append(
                        f"{task_id}: measurement-point fallback is only valid for an ordered heat series"
                    )
                for role in (fallback.source_role, fallback.master_role):
                    if role not in sheets:
                        errors.append(
                            f"{task_id}: measurement-point fallback references unknown role {role!r}"
                        )
                speed_matches = [
                    item for item in task.mappings
                    if item.path == fallback.line_speed_path
                    and item.kind == "entity_scalar"
                    and item.role == fallback.source_role
                ]
                if len(speed_matches) != 1:
                    errors.append(
                        f"{task_id}: measurement-point fallback line speed {fallback.line_speed_path!r} "
                        f"must have one scalar mapping on role {fallback.source_role!r}"
                    )
                elif speed_matches[0].canonical_unit != "mpm":
                    errors.append(
                        f"{task_id}: measurement-point fallback line speed must use canonical unit 'mpm'"
                    )
                if unit_conversion(
                    fallback.temperature_source_unit,
                    fallback.temperature_canonical_unit,
                ) is None:
                    errors.append(
                        f"{task_id}: measurement-point fallback has an unknown temperature unit conversion"
                    )
                source_entities = [
                    item for item in profile.shared.entities if item.role == fallback.source_role
                ]
                if (
                    len(source_entities) != 1
                    or mapping.parent_entity_type != source_entities[0].type
                ):
                    errors.append(
                        f"{task_id}: measurement-point fallback source role must identify the heat-series parent entity"
                    )
            if mapping.kind == "observation_scoped" and (mapping.normalizer_id != "median_by_parent/v1" or not mapping.parent_entity_type):
                errors.append(f"{task_id}: observation-scoped field {mapping.path!r} requires median_by_parent/v1 and parent entity type")
            if mapping.kind == "observation_scoped" and not mapping.observation_sources:
                errors.append(f"{task_id}: observation-scoped field {mapping.path!r} requires explicit observation sources")
            if mapping.parent_entity_type and mapping.parent_entity_type not in entity_types:
                errors.append(f"{task_id}: mapping {mapping.path!r} references unknown parent entity type")
            for source in mapping.observation_sources:
                if source.role not in observation_roles:
                    errors.append(f"{task_id}: observation source for {mapping.path!r} has no observation mapping")
            if mapping.kind in {"entity_scalar", "categorical"} and mapping.normalizer_id is not None:
                errors.append(f"{task_id}: scalar mapping {mapping.path!r} cannot declare a normalizer")
            expected_kind = {"number": {"entity_scalar", "observation_scoped"}, "categorical": {"categorical"}, "heat_pattern": {"ordered_heat_series"}}[declared[mapping.path].kind] if mapping.path in declared else set()
            if expected_kind and mapping.kind not in expected_kind:
                errors.append(f"{task_id}: mapping kind for {mapping.path!r} does not match TaskDefinition field kind")
            if mapping.series_columns and (
                unit_conversion(mapping.series_columns.time_source_unit, mapping.series_columns.time_canonical_unit) is None
                or unit_conversion(mapping.series_columns.value_source_unit, mapping.series_columns.value_canonical_unit) is None
            ):
                errors.append(f"{task_id}: ordered series {mapping.path!r} has unknown time/value unit conversion")
            if unit_conversion(mapping.source_unit, mapping.canonical_unit) is None:
                errors.append(
                    f"{task_id}: unknown or implicit unit conversion for {mapping.path!r}: "
                    f"{mapping.source_unit!r} -> {mapping.canonical_unit!r}"
                )
        for path, field in declared.items():
            if field.required and path not in seen:
                errors.append(f"{task_id}: required TaskDefinition field {path!r} has no mapping")
            mapping = next((item for item in task.mappings if item.path == path), None)
            if mapping is not None and unit_conversion(mapping.canonical_unit, field.unit) is None:
                errors.append(f"{task_id}: mapping unit for {path!r} does not match TaskDefinition")
        ordered = tuple(field.path for field in ordered_definition_fields)
        mapped_order = tuple(mapping.path for mapping in task.mappings)
        if ordered != mapped_order:
            errors.append(f"{task_id}: TaskDefinition ordered fields do not match profile mapped paths")
        output_units = {output.key: output.unit for output in definition.outputs}
        mapped_outputs: list[str] = []
        for observation in task.observations:
            if observation.role not in sheets:
                errors.append(f"{task_id}: observation references unknown role {observation.role!r}")
            if observation.parent_entity_type not in entity_types:
                errors.append(
                    f"{task_id}: observation {observation.role!r} references unknown parent entity type "
                    f"{observation.parent_entity_type!r}"
                )
            if observation.parent_column is None and observation.role not in entity_roles:
                errors.append(
                    f"{task_id}: relation-resolved observation {observation.role!r} must also be an entity role"
                )
            measurements = (*observation.targets, *observation.auxiliary)
            measurement_keys = [target.key for target in measurements]
            measurement_columns = [column for target in measurements for column in target.source_columns]
            if len(measurement_keys) != len(set(measurement_keys)):
                errors.append(f"{task_id}: observation {observation.role!r} measurement keys must be unique")
            if len(measurement_columns) != len(set(measurement_columns)):
                errors.append(f"{task_id}: observation {observation.role!r} measurement columns must be unique")
            if len(observation.metadata_columns.values()) != len(set(observation.metadata_columns.values())):
                errors.append(f"{task_id}: observation {observation.role!r} metadata columns must be unique")
            for target in measurements:
                if unit_conversion(target.unit, target.unit) is None:
                    errors.append(
                        f"{task_id}: observation {observation.role!r} has unknown unit {target.unit!r} for {target.key!r}"
                    )
            for target in observation.targets:
                mapped_outputs.append(target.key)
                if target.key not in output_units:
                    errors.append(f"{task_id}: observation maps unknown output target {target.key!r}")
                elif target.unit != output_units[target.key]:
                    errors.append(f"{task_id}: output unit mismatch for {target.key!r}: {target.unit!r} != {output_units[target.key]!r}")
        if sorted(mapped_outputs) != sorted(output_units):
            errors.append(f"{task_id}: observation target mappings must cover each TaskDefinition output exactly once")
        declared_measurements = {
            target.key for observation in task.observations
            for target in (*observation.targets, *observation.auxiliary)
        }
        ranges = profile.shared.physical_ranges.get(task_id, {})
        unknown_ranges = set(ranges) - declared_measurements
        if unknown_ranges:
            errors.append(f"{task_id}: physical ranges reference unknown measurements: {', '.join(sorted(unknown_ranges))}")
        for key, bounds in ranges.items():
            if len(bounds) != 2 or bounds[0] >= bounds[1]:
                errors.append(f"{task_id}: physical range for {key!r} must be an increasing pair")
    unknown_range_tasks = set(profile.shared.physical_ranges) - set(profile.tasks)
    if unknown_range_tasks:
        errors.append(f"physical ranges reference unknown tasks: {', '.join(sorted(unknown_range_tasks))}")
    if errors:
        raise DatasetProfileError(errors)


def _headers(sheet: Any) -> tuple[str, ...]:
    row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    return tuple("" if value is None else str(value) for value in row)


def _sheet_records(sheet: Any) -> list[dict[str, Any]]:
    iterator = sheet.iter_rows(values_only=True)
    headers = tuple(
        str(value) if value is not None else f"column_{index}"
        for index, value in enumerate(next(iterator, ()))
    )
    return [
        {headers[index]: value for index, value in enumerate(values) if index < len(headers)}
        for values in iterator
        if any(value is not None for value in values)
    ]


def _measurement_point_fallback_series(
    source_rows: Iterable[Mapping[str, Any]],
    master_rows: Iterable[Mapping[str, Any]],
    *,
    parent_column: str,
    speed_column: str,
    fallback: MeasurementPointSeriesFallback,
    profile: DatasetInputProfile,
) -> dict[str, list[dict[str, Any]]]:
    temperature_conversion = unit_conversion(
        fallback.temperature_source_unit,
        fallback.temperature_canonical_unit,
    )
    if temperature_conversion is None:
        return {}
    master_points = sorted(
        (
            (
                float(row[fallback.order_column]),
                str(row[fallback.stage_column]),
                float(row[fallback.position_column]),
            )
            for row in master_rows
            if str(row.get(fallback.equipment_column) or "") == fallback.equipment_value
            and isinstance(row.get(fallback.order_column), (int, float))
            and math.isfinite(float(row[fallback.order_column]))
            and row.get(fallback.stage_column) is not None
            and str(row.get(fallback.stage_column)).strip()
            and isinstance(row.get(fallback.position_column), (int, float))
            and math.isfinite(float(row[fallback.position_column]))
            and float(row[fallback.position_column]) >= 0
        ),
        key=lambda item: item[0],
    )
    derived: dict[str, list[dict[str, Any]]] = {}
    for row in source_rows:
        parent = row.get(parent_column)
        speed = row.get(speed_column)
        if (
            parent is None
            or not str(parent).strip()
            or not isinstance(speed, (int, float))
            or not math.isfinite(float(speed))
            or speed <= 0
        ):
            continue
        points: list[dict[str, Any]] = []
        for _, stage, position_m in master_points:
            temperature_column = fallback.temperature_column_template.format(stage=stage)
            temperature = row.get(temperature_column)
            if (
                not isinstance(temperature, (int, float))
                or not math.isfinite(float(temperature))
            ):
                continue
            point = {
                "time_s": 60.0 * position_m / float(speed),
                "temperature_c": (
                    float(temperature) * temperature_conversion.scale
                    + temperature_conversion.offset
                ),
                "stage_name": stage,
                "mapping_status": "測定点マスタ補完",
            }
            stage_category = profile.stage_category_for(stage)
            if stage_category:
                point["stage_category"] = stage_category
            points.append(point)
        if len(points) >= 2:
            derived[str(parent)] = points
    return derived


def preflight_workbook(workbook: Any, profile: DatasetInputProfile) -> None:
    errors: list[str] = []
    required: dict[str, set[str]] = {}
    fallback_series_roles = {
        mapping.role
        for task in profile.tasks.values()
        for mapping in task.mappings
        if mapping.kind == "ordered_heat_series"
        and mapping.measurement_point_fallback is not None
    }

    def require(role: str, column: str) -> None:
        required.setdefault(profile.sheet_for_role(role), set()).add(column)

    for entity in profile.shared.entities:
        require(entity.role, entity.key)
    relation = profile.shared.relation
    for join in relation.joins:
        require(relation.role, join.column)
    for item in profile.shared.eligibility:
        require(item.role, item.column)
    for item in profile.shared.technical:
        if item.role not in fallback_series_roles:
            require(item.role, item.column)
    for task in profile.tasks.values():
        for mapping in task.mappings:
            if mapping.column:
                require(mapping.role, mapping.column)
            if mapping.kind == "ordered_heat_series":
                    if mapping.measurement_point_fallback is None:
                        for column in (
                            (mapping.series_columns.parent, mapping.series_columns.order, mapping.series_columns.time, mapping.series_columns.value)
                            if mapping.series_columns else ()
                        ):
                            require(mapping.role, column)
            if mapping.measurement_point_fallback:
                fallback = mapping.measurement_point_fallback
                for column in (
                    fallback.equipment_column,
                    fallback.order_column,
                    fallback.stage_column,
                    fallback.position_column,
                ):
                    require(fallback.master_role, column)
            for source in mapping.observation_sources:
                require(source.role, source.column)
        for observation in task.observations:
            role = observation.role
            require(role, observation.id_column)
            if observation.parent_column:
                require(role, observation.parent_column)
            for target in observation.targets:
                for column in target.source_columns:
                    require(role, column)
            for target in observation.auxiliary:
                for column in target.source_columns:
                    require(role, column)
            for column in observation.metadata_columns.values():
                require(role, str(column))

    for sheet_name, columns in required.items():
        if sheet_name not in workbook.sheetnames:
            errors.append(f"missing required sheet {sheet_name!r}")
            continue
        headers = _headers(workbook[sheet_name])
        counts = {header: headers.count(header) for header in set(headers) if header}
        for header, count in sorted(counts.items()):
            if count > 1:
                errors.append(f"sheet {sheet_name!r} has duplicate header {header!r} ({count} columns)")
        for column in sorted(columns - set(headers)):
            errors.append(f"sheet {sheet_name!r} is missing required column {column!r}")

    # Check every workbook sheet, not only mapped sheets: duplicate headers must
    # never be hidden by dict construction at the raw boundary.
    for sheet_name in workbook.sheetnames:
        if sheet_name in required:
            continue
        headers = _headers(workbook[sheet_name])
        for header in sorted({value for value in headers if value}):
            count = headers.count(header)
            if count > 1:
                errors.append(f"sheet {sheet_name!r} has duplicate header {header!r} ({count} columns)")

    relation_sheet = profile.sheet_for_role(profile.shared.relation.role)
    if relation_sheet in workbook.sheetnames:
        headers = _headers(workbook[relation_sheet])
        for join in profile.shared.relation.joins:
            if join.cardinality != "exactly_one" or join.column not in headers:
                continue
            index = headers.index(join.column)
            missing_count = sum(
                index >= len(row) or row[index] is None or not str(row[index]).strip()
                for row in workbook[relation_sheet].iter_rows(min_row=2, values_only=True)
            )
            if missing_count:
                errors.append(f"relation {join.path!r} violates exactly_one cardinality in {missing_count} rows")
        if all(join.column in headers for join in profile.shared.relation.joins):
            indices = {join.entity_type: headers.index(join.column) for join in profile.shared.relation.joins}
            parent_signatures: dict[tuple[str, str], set[tuple[tuple[str, str], ...]]] = {}
            for row in workbook[relation_sheet].iter_rows(min_row=2, values_only=True):
                for join in profile.shared.relation.joins:
                    if join.parent_consistency != "exactly_one":
                        continue
                    child_value = row[indices[join.entity_type]]
                    if child_value is None or not str(child_value).strip() or not join.parent_entity_types:
                        continue
                    signature = tuple(
                        (parent_type, str(row[indices[parent_type]]))
                        for parent_type in join.parent_entity_types
                        if row[indices[parent_type]] is not None and str(row[indices[parent_type]]).strip()
                    )
                    if signature:
                        parent_signatures.setdefault((join.entity_type, str(child_value)), set()).add(signature)
            for (entity_type, child_value), signatures in parent_signatures.items():
                if len(signatures) > 1:
                    errors.append(
                        f"relation entity {entity_type}:{child_value} connects to conflicting parents: {sorted(signatures)}"
                    )

    for policy in profile.shared.eligibility:
        sheet_name = profile.sheet_for_role(policy.role)
        if sheet_name not in workbook.sheetnames:
            continue
        headers = _headers(workbook[sheet_name])
        if policy.column not in headers:
            continue
        index = headers.index(policy.column)
        if not any(
            index < len(row) and row[index] is not None and str(row[index]) in policy.accepted_values
            for row in workbook[sheet_name].iter_rows(min_row=2, values_only=True)
        ):
            errors.append(
                f"eligibility policy {policy.role}.{policy.policy} has no accepted source values"
            )

    # Semantic checks deliberately happen before canonical records, runtimes,
    # or database state exist. They report all zero-signal mappings together.
    for task_id, task in profile.tasks.items():
        for mapping in task.mappings:
            sheet_name = profile.sheet_for_role(mapping.role)
            if mapping.kind == "ordered_heat_series" and mapping.series_columns:
                columns = mapping.series_columns
                headers = _headers(workbook[sheet_name]) if sheet_name in workbook.sheetnames else ()
                required_series_columns = (columns.parent, columns.order, columns.time, columns.value)
                points_by_parent: dict[str, int] = {}
                if all(column in headers for column in required_series_columns):
                    for column, declared_unit in (
                        (columns.time, columns.time_source_unit),
                        (columns.value, columns.value_source_unit),
                    ):
                        header_unit = _header_unit(column)
                        if header_unit != declared_unit:
                            errors.append(
                                f"{task_id}: ordered series column {column!r} declares {header_unit!r}, expected {declared_unit!r}"
                            )
                    parent_index, order_index, time_index, value_index = (
                        headers.index(column) for column in required_series_columns
                    )
                    invalid_order_count = 0
                    invalid_point_count = 0
                    for row in workbook[sheet_name].iter_rows(min_row=2, values_only=True):
                        parent = row[parent_index]
                        order = row[order_index]
                        time = row[time_index]
                        value = row[value_index]
                        if all(item is None or not str(item).strip() for item in (parent, order, time, value)):
                            continue
                        valid_point = (
                            parent is not None
                            and str(parent).strip()
                            and isinstance(time, (int, float))
                            and math.isfinite(float(time))
                            and isinstance(value, (int, float))
                            and math.isfinite(float(value))
                        )
                        if not valid_point:
                            invalid_point_count += 1
                            continue
                        if (
                            not isinstance(order, (int, float))
                            or not math.isfinite(float(order))
                        ):
                            invalid_order_count += 1
                            continue
                        key = str(parent)
                        points_by_parent[key] = points_by_parent.get(key, 0) + 1
                    if invalid_order_count:
                        errors.append(
                            f"{task_id}: ordered heat series {mapping.path!r} has {invalid_order_count} points with a non-numeric order"
                        )
                    if invalid_point_count:
                        errors.append(
                            f"{task_id}: ordered heat series {mapping.path!r} has {invalid_point_count} incomplete or non-numeric points"
                        )
                fallback_series: dict[str, list[dict[str, Any]]] = {}
                fallback_parent_keys: set[str] = set()
                fallback = mapping.measurement_point_fallback
                if fallback is not None:
                    source_sheet = profile.sheet_for_role(fallback.source_role)
                    master_sheet = profile.sheet_for_role(fallback.master_role)
                    speed_mapping = task.mapping(fallback.line_speed_path)
                    source_entity = next(
                        item for item in profile.shared.entities
                        if item.role == fallback.source_role
                    )
                    if (
                        source_sheet in workbook.sheetnames
                        and master_sheet in workbook.sheetnames
                        and speed_mapping.column
                    ):
                        source_records = _sheet_records(workbook[source_sheet])
                        master_records = _sheet_records(workbook[master_sheet])
                        fallback_parent_keys = {
                            str(row[source_entity.key])
                            for row in source_records
                            if row.get(source_entity.key) is not None
                            and str(row[source_entity.key]).strip()
                        }
                        if _header_unit(fallback.position_column) != fallback.position_unit:
                            errors.append(
                                f"{task_id}: measurement-point position column {fallback.position_column!r} "
                                f"must declare unit {fallback.position_unit!r}"
                            )
                        selected_master = [
                            row for row in master_records
                            if str(row.get(fallback.equipment_column) or "")
                            == fallback.equipment_value
                        ]
                        master_geometry = [
                            (
                                float(row[fallback.order_column]),
                                str(row[fallback.stage_column]),
                                float(row[fallback.position_column]),
                            )
                            for row in selected_master
                            if isinstance(row.get(fallback.order_column), (int, float))
                            and math.isfinite(float(row[fallback.order_column]))
                            and row.get(fallback.stage_column) is not None
                            and str(row.get(fallback.stage_column)).strip()
                            and isinstance(row.get(fallback.position_column), (int, float))
                            and math.isfinite(float(row[fallback.position_column]))
                            and float(row[fallback.position_column]) >= 0
                        ]
                        if len(master_geometry) != len(selected_master) or len(master_geometry) < 2:
                            errors.append(
                                f"{task_id}: measurement-point master {fallback.equipment_value!r} "
                                "requires at least two points with numeric order and non-negative position"
                            )
                        orders = [item[0] for item in master_geometry]
                        stages = [item[1] for item in master_geometry]
                        if len(orders) != len(set(orders)) or len(stages) != len(set(stages)):
                            errors.append(
                                f"{task_id}: measurement-point master {fallback.equipment_value!r} "
                                "requires unique order and stage values"
                            )
                        ordered_geometry = sorted(master_geometry)
                        if any(
                            right[2] <= left[2]
                            for left, right in zip(ordered_geometry, ordered_geometry[1:])
                        ):
                            errors.append(
                                f"{task_id}: measurement-point master {fallback.equipment_value!r} "
                                "positions must increase with point order"
                            )
                        source_headers = set(_headers(workbook[source_sheet]))
                        expected_temperature_columns = {
                            fallback.temperature_column_template.format(stage=stage)
                            for _, stage, _ in master_geometry
                        }
                        missing_temperature_columns = sorted(
                            expected_temperature_columns - source_headers
                        )
                        if missing_temperature_columns:
                            errors.append(
                                f"{task_id}: measurement-point fallback is missing temperature columns: "
                                + ", ".join(missing_temperature_columns)
                            )
                        unit_mismatches = sorted(
                            column for column in expected_temperature_columns & source_headers
                            if _header_unit(column) != fallback.temperature_source_unit
                        )
                        if unit_mismatches:
                            errors.append(
                                f"{task_id}: measurement-point fallback temperature columns have unexpected units: "
                                + ", ".join(unit_mismatches)
                            )
                        fallback_series = _measurement_point_fallback_series(
                            source_records,
                            master_records,
                            parent_column=source_entity.key,
                            speed_column=speed_mapping.column,
                            fallback=fallback,
                            profile=profile,
                        )
                missing_parents = sorted(
                    fallback_parent_keys
                    - {
                        parent for parent, count in points_by_parent.items()
                        if count >= 2
                    }
                    - set(fallback_series)
                )
                if missing_parents:
                    errors.append(
                        f"{task_id}: {len(missing_parents)} parent rows have neither a complete heat history "
                        "nor a derivable measurement-point series"
                    )
                if not any(count >= 2 for count in points_by_parent.values()) and not fallback_series:
                    errors.append(
                        f"{task_id}: required ordered heat series {mapping.path!r} has no parent with at least two numeric "
                        "points and cannot be derived from the measurement-point master"
                    )
            if sheet_name not in workbook.sheetnames or not mapping.column:
                continue
            headers = _headers(workbook[sheet_name])
            if mapping.column not in headers:
                continue
            header_unit = _header_unit(mapping.column)
            if mapping.source_unit is not None and header_unit != mapping.source_unit:
                errors.append(
                    f"{task_id}: source unit mismatch for {mapping.path!r}: "
                    f"header declares {header_unit!r}, profile declares {mapping.source_unit!r}"
                )
            if mapping.kind in {"entity_scalar", "observation_scoped"} and mapping.canonical_unit is not None:
                index = headers.index(mapping.column)
                numeric_count = sum(
                    isinstance(row[index], (int, float))
                    for row in workbook[sheet_name].iter_rows(min_row=2, values_only=True)
                    if index < len(row)
                )
                if numeric_count == 0:
                    errors.append(f"{task_id}: numeric field {mapping.path!r} has no numeric source values")
            definition_field = next(
                field for group in profile.task_definitions[task_id].input_groups for field in group.fields
                if field.path == mapping.path
            )
            if mapping.kind == "categorical":
                index = headers.index(mapping.column)
                unknown = sorted({
                    str(row[index]) for row in workbook[sheet_name].iter_rows(min_row=2, values_only=True)
                    if index < len(row) and row[index] is not None and str(row[index]) not in definition_field.choices
                })
                if unknown:
                    errors.append(f"{task_id}: categorical field {mapping.path!r} contains unknown choices: {', '.join(unknown)}")
        for observation in task.observations:
            sheet_name = profile.sheet_for_role(observation.role)
            if sheet_name not in workbook.sheetnames:
                continue
            headers = _headers(workbook[sheet_name])
            for target in (*observation.targets, *observation.auxiliary):
                for column in target.source_columns:
                    if column not in headers:
                        continue
                    source_unit = _header_unit(column)
                    if unit_conversion(source_unit, target.unit) is None:
                        errors.append(
                            f"{task_id}: observation unit mismatch for {target.key!r}: "
                            f"header {column!r} declares {source_unit!r}, TaskDefinition declares {target.unit!r}"
                        )
            parent_index = headers.index(observation.parent_column) if observation.parent_column in headers else None
            target_indices = [
                headers.index(column)
                for target in observation.targets
                for column in target.source_columns
                if column in headers
            ]
            parent_count = 0 if parent_index is not None else None
            numeric_target_count = 0
            for row in workbook[sheet_name].iter_rows(min_row=2, values_only=True):
                if parent_index is not None and parent_index < len(row) and row[parent_index] is not None and str(row[parent_index]).strip():
                    parent_count += 1
                if any(index < len(row) and isinstance(row[index], (int, float)) for index in target_indices):
                    numeric_target_count += 1
            if parent_count == 0:
                errors.append(f"{task_id}: observation role {observation.role!r} has no recognized parent key")
            if parent_index is None:
                source_entity = next((entity for entity in profile.shared.entities if entity.role == observation.role), None)
                child_join = next((join for join in profile.shared.relation.joins if source_entity and join.entity_type == source_entity.type), None)
                parent_join = next((join for join in profile.shared.relation.joins if join.entity_type == observation.parent_entity_type), None)
                relation_headers = _headers(workbook[relation_sheet]) if relation_sheet in workbook.sheetnames else ()
                if child_join and parent_join and child_join.column in relation_headers and parent_join.column in relation_headers:
                    child_index = relation_headers.index(child_join.column)
                    relation_parent_index = relation_headers.index(parent_join.column)
                    known = {
                        str(row[child_index])
                        for row in workbook[relation_sheet].iter_rows(min_row=2, values_only=True)
                        if child_index < len(row)
                        and relation_parent_index < len(row)
                        and row[child_index] is not None
                        and str(row[child_index]).strip()
                        and row[relation_parent_index] is not None
                        and str(row[relation_parent_index]).strip()
                    }
                    id_index = headers.index(observation.id_column)
                    resolved = sum(
                        1
                        for row in workbook[sheet_name].iter_rows(min_row=2, values_only=True)
                        if id_index < len(row)
                        and row[id_index] is not None
                        and str(row[id_index]) in known
                        and any(index < len(row) and isinstance(row[index], (int, float)) for index in target_indices)
                    )
                    if not resolved:
                        errors.append(
                            f"{task_id}: observation role {observation.role!r} has no numeric row with a relation parent"
                        )
            if numeric_target_count == 0:
                errors.append(f"{task_id}: observation role {observation.role!r} has no recognized numeric target")
    if errors:
        raise DatasetProfileError(errors)


@dataclass(frozen=True)
class CanonicalEntity:
    identity: tuple[str, str]
    values: Mapping[str, Any]
    source_locator: Mapping[str, Any]
    source_metadata: Mapping[str, Any]


@dataclass(frozen=True)
class CanonicalObservation:
    task_id: str
    source_role: str
    id: str
    parent_identity: tuple[str, str]
    targets: Mapping[str, float]
    canonical_measurements: Mapping[str, float]
    metadata: Mapping[str, Any]
    source_locator: Mapping[str, Any]
    policy_results: Mapping[str, bool]

    @property
    def parent_key(self) -> str:
        return self.parent_identity[1]


@dataclass(frozen=True)
class CanonicalDataset:
    profile: DatasetInputProfile
    source_rows: Mapping[str, list[dict[str, Any]]]
    entities: Mapping[tuple[str, str], CanonicalEntity]
    relations: tuple[Mapping[str, tuple[str, str]], ...]
    observations: tuple[CanonicalObservation, ...]
    heat_series: Mapping[tuple[str, str], list[dict[str, Any]]]

    def rows(self, role: str) -> list[dict[str, Any]]:
        return self.source_rows[self.profile.sheet_for_role(role)]

    def value(self, row: Mapping[str, Any], task_id: str, path: str) -> Any:
        mapping = self.profile.tasks[task_id].mapping(path)
        value = row.get(mapping.column)
        conversion = unit_conversion(mapping.source_unit, mapping.canonical_unit)
        if isinstance(value, (int, float)) and conversion is not None:
            return float(value) * conversion.scale + conversion.offset
        return value

    def mapped_values(self, row: Mapping[str, Any], task_id: str, prefixes: tuple[str, ...]) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for mapping in self.profile.tasks[task_id].mappings:
            prefix = next((item for item in prefixes if mapping.path.startswith(item)), None)
            if prefix is None or mapping.kind not in {"entity_scalar", "categorical"}:
                continue
            values[mapping.path.removeprefix(prefix)] = self.value(row, task_id, mapping.path)
        return values

    def technical_value(self, row: Mapping[str, Any], role: str, name: str) -> Any:
        matches = [
            item for item in self.profile.shared.technical
            if item.role == role and item.name == name
        ]
        if len(matches) != 1:
            raise DatasetProfileError([f"technical field {role}.{name} must have exactly one mapping"])
        return row.get(matches[0].column)

    def policy_value(self, row: Mapping[str, Any], role: str, policy: str) -> Any:
        matches = [item for item in self.profile.shared.eligibility if item.role == role and item.policy == policy]
        if len(matches) != 1:
            raise DatasetProfileError([f"eligibility policy {role}.{policy} must have exactly one mapping"])
        return row.get(matches[0].column)

    def policy_allows(self, row: Mapping[str, Any], role: str, policy: str) -> bool:
        matches = [item for item in self.profile.shared.eligibility if item.role == role and item.policy == policy]
        if len(matches) != 1:
            default_key = f"{role}.{policy}"
            if not matches and default_key in self.profile.shared.policy_defaults:
                return bool(self.profile.shared.policy_defaults[default_key])
            raise DatasetProfileError([f"eligibility policy {role}.{policy} must have exactly one mapping"])
        return str(row.get(matches[0].column) or "") in matches[0].accepted_values


def _normalize_heat_series(points: list[dict[str, Any]]) -> None:
    stage_raw_origin = 0.0
    stage_normalized_origin = 0.0
    previous_raw = 0.0
    previous_normalized = -1.0
    for point in points:
        raw = float(point["time_s"])
        if previous_normalized >= 0 and raw < previous_raw:
            stage_raw_origin = raw
            stage_normalized_origin = previous_normalized + 1e-6
            point["segment_start"] = True
        point["time_s"] = max(stage_normalized_origin + raw - stage_raw_origin, previous_normalized + 1e-6)
        previous_raw = raw
        previous_normalized = float(point["time_s"])


def canonicalize_workbook(workbook: Any, profile: DatasetInputProfile) -> CanonicalDataset:
    preflight_workbook(workbook, profile)
    rows: dict[str, list[dict[str, Any]]] = {}
    for name in workbook.sheetnames:
        iterator = workbook[name].iter_rows(values_only=True)
        headers = tuple(str(value) if value is not None else f"column_{index}" for index, value in enumerate(next(iterator, ())))
        records = []
        for values in iterator:
            record = {headers[index]: value for index, value in enumerate(values) if index < len(headers)}
            if any(value is not None for value in record.values()):
                records.append(record)
        rows[name] = records
    entities: dict[tuple[str, str], CanonicalEntity] = {}
    for entity in profile.shared.entities:
        role, entity_type, key_column = entity.role, entity.type, entity.key
        source_sheet = profile.sheet_for_role(role)
        task_mappings = [
            (task_id, mapping) for task_id, task in profile.tasks.items()
            for mapping in task.mappings if mapping.role == role and mapping.column
        ]
        mapped_columns = {mapping.column for _, mapping in task_mappings}
        for row_number, row in enumerate(rows[source_sheet], start=2):
            entity_key = row.get(key_column)
            if entity_key is None or not str(entity_key).strip():
                continue
            values: dict[str, Any] = {}
            for task_id, mapping in task_mappings:
                value = row.get(mapping.column)
                conversion = unit_conversion(mapping.source_unit, mapping.canonical_unit)
                if isinstance(value, (int, float)) and conversion is not None:
                    value = float(value) * conversion.scale + conversion.offset
                values.setdefault(task_id, {})[mapping.path] = value
            identity = (entity_type, str(entity_key))
            entities.setdefault(identity, CanonicalEntity(
                identity=identity,
                values=values,
                source_locator={"sheet": source_sheet, "row": row_number},
                source_metadata={key: value for key, value in row.items() if key not in mapped_columns},
            ))
    relations = tuple(
        {
            join.entity_type: (join.entity_type, str(row[join.column]))
            for join in profile.shared.relation.joins
            if row.get(join.column) is not None and str(row[join.column]).strip()
        }
        for row in rows[profile.sheet_for_role(profile.shared.relation.role)]
    )
    entity_type_by_role = {entity.role: entity.type for entity in profile.shared.entities}
    relation_parents: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    for relation in relations:
        for child_type, child_identity in relation.items():
            for parent_type, parent_identity in relation.items():
                if child_type != parent_type:
                    relation_parents.setdefault(
                        (child_type, child_identity[1], parent_type), set()
                    ).add(parent_identity)

    def observation_value(row: Mapping[str, Any], target: ObservationTarget) -> float | None:
        for column in target.source_columns:
            value = row.get(column)
            if isinstance(value, (int, float)):
                return float(value)
        return None

    observations: list[CanonicalObservation] = []
    for task_id, task in profile.tasks.items():
        for mapping in task.observations:
            sheet_name = profile.sheet_for_role(mapping.role)
            for row_number, row in enumerate(rows[sheet_name], start=2):
                targets = {
                    target.key: value
                    for target in mapping.targets
                    if (value := observation_value(row, target)) is not None
                }
                auxiliary = {
                    target.key: value
                    for target in mapping.auxiliary
                    if (value := observation_value(row, target)) is not None
                }
                if not targets and not auxiliary:
                    continue
                observation_id = str(row.get(mapping.id_column, ""))
                if mapping.parent_column:
                    parent_identity = (mapping.parent_entity_type, str(row.get(mapping.parent_column, "")))
                else:
                    source_entity_type = entity_type_by_role[mapping.role]
                    parents = relation_parents.get(
                        (source_entity_type, observation_id, mapping.parent_entity_type), set()
                    )
                    if len(parents) != 1:
                        parent_identity = (mapping.parent_entity_type, "")
                    else:
                        parent_identity = next(iter(parents))
                policy_results = {
                    policy.policy: str(row.get(policy.column) or "") in policy.accepted_values
                    for policy in profile.shared.eligibility if policy.role == mapping.role
                }
                for default_key, allowed in profile.shared.policy_defaults.items():
                    role, policy = default_key.split(".", 1)
                    if role == mapping.role:
                        policy_results.setdefault(policy, bool(allowed))
                observations.append(CanonicalObservation(
                    task_id=task_id, source_role=mapping.role,
                    id=observation_id,
                    parent_identity=parent_identity,
                    targets=targets,
                    canonical_measurements={**targets, **auxiliary},
                    metadata={name: row.get(column) for name, column in mapping.metadata_columns.items()},
                    source_locator={"sheet": sheet_name, "row": row_number},
                    policy_results=policy_results,
                ))
        for field_mapping in task.mappings:
            if field_mapping.kind != "observation_scoped" or not field_mapping.column or not field_mapping.parent_entity_type:
                continue
            grouped_values: dict[str, list[float]] = {}
            for source in field_mapping.observation_sources:
                observation_mapping = next(item for item in task.observations if item.role == source.role)
                for row in rows[profile.sheet_for_role(source.role)]:
                    value = row.get(source.column)
                    parent = row.get(observation_mapping.parent_column)
                    if isinstance(value, (int, float)) and parent is not None and str(parent).strip():
                        grouped_values.setdefault(str(parent), []).append(float(value))
            for parent, values in grouped_values.items():
                entity = entities.get((field_mapping.parent_entity_type, parent))
                if entity is not None:
                    task_values = entity.values.setdefault(task_id, {})
                    task_values[field_mapping.path] = float(median(values))
    heat_series: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for task in profile.tasks.values():
        for mapping in task.mappings:
            if mapping.kind != "ordered_heat_series" or mapping.series_columns is None:
                continue
            columns = mapping.series_columns
            series_metadata = {
                item.name: item.column for item in profile.shared.technical
                if item.role == mapping.role and item.name in {"set_temperature_c", "stage_category", "stage_name", "mapping_status"}
            }
            grouped: dict[str, list[tuple[Any, dict[str, Any]]]] = {}
            for row in rows.get(profile.sheet_for_role(mapping.role), []):
                if not isinstance(row.get(columns.time), (int, float)) or not isinstance(row.get(columns.value), (int, float)):
                    continue
                parent = str(row.get(columns.parent, ""))
                point = {"time_s": float(row[columns.time]), "temperature_c": float(row[columns.value])}
                for name, column in series_metadata.items():
                    point[name] = row.get(column)
                stage_category = profile.stage_category_for(point.get("stage_name"))
                if stage_category:
                    point["stage_category"] = stage_category
                    point["mapping_status"] = "工程辞書一致"
                grouped.setdefault(parent, []).append((row.get(columns.order, 0), point))
            for parent, ordered in grouped.items():
                points = [point for _, point in sorted(ordered, key=lambda item: item[0])]
                if len(points) < 2:
                    continue
                _normalize_heat_series(points)
                parent_entity_type = mapping.parent_entity_type or "annealing"
                parent_identity = (parent_entity_type, parent)
                heat_series[parent_identity] = points
                entity = entities.get((parent_entity_type, parent))
                if entity is not None:
                    entity.values.setdefault(task.task_id, {})[mapping.path] = points
            fallback = mapping.measurement_point_fallback
            if fallback is None:
                continue
            source_entity = next(
                item for item in profile.shared.entities if item.role == fallback.source_role
            )
            speed_mapping = task.mapping(fallback.line_speed_path)
            if speed_mapping.column is None:
                continue
            derived = _measurement_point_fallback_series(
                rows[profile.sheet_for_role(fallback.source_role)],
                rows[profile.sheet_for_role(fallback.master_role)],
                parent_column=source_entity.key,
                speed_column=speed_mapping.column,
                fallback=fallback,
                profile=profile,
            )
            parent_entity_type = mapping.parent_entity_type or "annealing"
            for parent, points in derived.items():
                parent_identity = (parent_entity_type, parent)
                if parent_identity in heat_series:
                    continue
                heat_series[parent_identity] = points
                entity = entities.get(parent_identity)
                if entity is not None:
                    entity.values.setdefault(task.task_id, {})[mapping.path] = points
    return CanonicalDataset(profile, rows, entities, relations, tuple(observations), heat_series)
