from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

from material_workbench.data.profiles.requirements import task_data_requirements
from material_workbench.data.profiles.schema import (
    DatasetInputProfile,
    DatasetProfileError,
    MeasurementPointSeriesFallback,
    RelationJoin,
    _header_unit,
    unit_conversion,
)

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


def validate_profile(profile: DatasetInputProfile, task_definitions: Mapping[str, Any] | None = None) -> None:
    errors: list[str] = []
    sheets = profile.shared.sheets
    if len(set(sheets.values())) != len(sheets):
        errors.append("shared sheet roles must map to unique source sheets")
    for role, aliases in profile.shared.column_aliases.items():
        if role not in sheets:
            errors.append(f"column aliases reference unknown role {role!r}")
            continue
        canonical_columns = list(aliases)
        source_columns = list(aliases.values())
        if any(not str(value).strip() for value in (*canonical_columns, *source_columns)):
            errors.append(f"column aliases for role {role!r} must use non-empty names")
        if len(source_columns) != len(set(source_columns)):
            errors.append(f"column aliases for role {role!r} must map to unique source columns")
        unchanged = sorted(
            canonical for canonical, source in aliases.items()
            if canonical == source
        )
        if unchanged:
            errors.append(
                f"column aliases for role {role!r} contain redundant mappings: "
                + ", ".join(unchanged)
            )
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
    join_columns = [
        column
        for join in profile.shared.relation.joins
        for column in join.source_columns
    ]
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
            unknown_optional_metadata = set(observation.optional_metadata_keys) - set(observation.metadata_columns)
            if unknown_optional_metadata:
                errors.append(
                    f"{task_id}: observation {observation.role!r} optional metadata keys are unknown: "
                    + ", ".join(sorted(unknown_optional_metadata))
                )
            auxiliary_keys = {target.key for target in observation.auxiliary}
            unknown_optional_auxiliary = set(observation.optional_auxiliary_keys) - auxiliary_keys
            if unknown_optional_auxiliary:
                errors.append(
                    f"{task_id}: observation {observation.role!r} optional auxiliary keys are unknown: "
                    + ", ".join(sorted(unknown_optional_auxiliary))
                )
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


class _ProfileSheet:
    def __init__(self, sheet: Any, aliases: Mapping[str, str]) -> None:
        self._sheet = sheet
        self._canonical_by_source = {
            source: canonical for canonical, source in aliases.items()
        }

    def iter_rows(self, *args: Any, **kwargs: Any) -> Any:
        rows = self._sheet.iter_rows(*args, **kwargs)
        min_row = int(kwargs.get("min_row", 1))
        values_only = bool(kwargs.get("values_only", False))
        if min_row > 1 or not values_only or not self._canonical_by_source:
            return rows

        def mapped_rows() -> Iterable[tuple[Any, ...]]:
            for index, row in enumerate(rows):
                if index == 0:
                    yield tuple(
                        self._canonical_by_source.get(str(value), value)
                        if value is not None else value
                        for value in row
                    )
                else:
                    yield row

        return mapped_rows()


class _ProfileWorkbook:
    def __init__(self, workbook: Any, profile: DatasetInputProfile) -> None:
        self._workbook = workbook
        self.sheetnames = workbook.sheetnames
        self._aliases_by_sheet = {
            profile.sheet_for_role(role): aliases
            for role, aliases in profile.shared.column_aliases.items()
        }

    def __getitem__(self, sheet_name: str) -> Any:
        sheet = self._workbook[sheet_name]
        aliases = self._aliases_by_sheet.get(sheet_name)
        return _ProfileSheet(sheet, aliases) if aliases else sheet


def _profile_workbook(workbook: Any, profile: DatasetInputProfile) -> Any:
    if isinstance(workbook, _ProfileWorkbook) or not profile.shared.column_aliases:
        return workbook
    return _ProfileWorkbook(workbook, profile)


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
        if (
            len(points) >= 2
            and math.isclose(float(points[0]["time_s"]), 0.0, abs_tol=1e-9)
        ):
            derived[str(parent)] = points
    return derived


def preflight_workbook(workbook: Any, profile: DatasetInputProfile) -> None:
    workbook = _profile_workbook(workbook, profile)
    errors: list[str] = []
    task_requirements = task_data_requirements(profile)
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
                if target.key in observation.optional_auxiliary_keys:
                    continue
                for column in target.source_columns:
                    require(role, column)
            for key, column in observation.metadata_columns.items():
                if key in observation.optional_metadata_keys:
                    continue
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
        required_joins = tuple(
            join
            for join in profile.shared.relation.joins
            if task_requirements.requires_relation(join)
        )
        for join in required_joins:
            if not any(column in headers for column in join.source_columns):
                errors.append(
                    f"sheet {relation_sheet!r} is missing every source column for "
                    f"relation {join.path!r}: {', '.join(join.source_columns)}"
                )

        def relation_value(row: tuple[Any, ...], join: RelationJoin) -> Any:
            for column in join.source_columns:
                if column not in headers:
                    continue
                value = row[headers.index(column)]
                if value is not None and str(value).strip():
                    return value
            return None

        for join in required_joins:
            if join.cardinality != "exactly_one":
                continue
            missing_count = sum(
                relation_value(row, join) is None
                for row in workbook[relation_sheet].iter_rows(min_row=2, values_only=True)
            )
            if missing_count:
                errors.append(f"relation {join.path!r} violates exactly_one cardinality in {missing_count} rows")
        if all(
            any(column in headers for column in join.source_columns)
            for join in required_joins
        ):
            joins_by_type = {
                join.entity_type: join
                for join in required_joins
            }
            parent_signatures: dict[tuple[str, str], set[tuple[tuple[str, str], ...]]] = {}
            for row in workbook[relation_sheet].iter_rows(min_row=2, values_only=True):
                for join in profile.shared.relation.joins:
                    if join.parent_consistency != "exactly_one":
                        continue
                    child_value = relation_value(row, join)
                    if child_value is None or not str(child_value).strip() or not join.parent_entity_types:
                        continue
                    signature = tuple(
                        (parent_type, str(parent_value))
                        for parent_type in join.parent_entity_types
                        if (parent_value := relation_value(row, joins_by_type[parent_type])) is not None
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
                invalid_parents: set[str] = set()
                if sheet_name in workbook.sheetnames:
                    required_existing_columns = {
                        *required_series_columns,
                        *(
                            item.column
                            for item in profile.shared.technical
                            if item.role == mapping.role
                        ),
                    }
                    missing_existing_columns = sorted(
                        required_existing_columns - set(headers)
                    )
                    if missing_existing_columns:
                        errors.append(
                            f"{task_id}: existing ordered heat series sheet {sheet_name!r} "
                            "is missing required columns: "
                            + ", ".join(missing_existing_columns)
                        )
                if all(column in headers for column in required_series_columns):
                    for column, declared_unit in (
                        (columns.time, columns.time_source_unit),
                        (columns.value, columns.value_source_unit),
                    ):
                        source_column = profile.source_column_for(mapping.role, column)
                        header_unit = _header_unit(source_column)
                        if header_unit != declared_unit:
                            errors.append(
                                f"{task_id}: ordered series column {column!r} declares {header_unit!r}, expected {declared_unit!r}"
                            )
                    parent_index, order_index, time_index, value_index = (
                        headers.index(column) for column in required_series_columns
                    )
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
                            if parent is not None and str(parent).strip():
                                invalid_parents.add(str(parent))
                            continue
                        if (
                            not isinstance(order, (int, float))
                            or not math.isfinite(float(order))
                        ):
                            invalid_parents.add(str(parent))
                            continue
                        key = str(parent)
                        points_by_parent[key] = points_by_parent.get(key, 0) + 1
                    for parent in invalid_parents:
                        points_by_parent.pop(parent, None)
                fallback_series: dict[str, list[dict[str, Any]]] = {}
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
                        if (
                            ordered_geometry
                            and not math.isclose(ordered_geometry[0][2], 0.0, abs_tol=1e-9)
                        ):
                            errors.append(
                                f"{task_id}: measurement-point master {fallback.equipment_value!r} "
                                "must start at position 0"
                            )
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
                        # The master describes every stage the equipment can
                        # expose, including stages unused by the current
                        # dataset. Only source columns that actually exist
                        # participate in fallback construction.
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
                # Partial source coverage is normal in real workbooks. Parents
                # without either representation remain in the canonical
                # lineage but do not receive a heat series, so downstream
                # feature builders naturally exclude only those rows.
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
            source_column = profile.source_column_for(mapping.role, mapping.column)
            header_unit = _header_unit(source_column)
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
            if mapping.kind == "observation_scoped":
                for observation_source in mapping.observation_sources:
                    physical_column = profile.source_column_for(
                        observation_source.role,
                        observation_source.column,
                    )
                    source_header_unit = _header_unit(physical_column)
                    if source_header_unit != mapping.source_unit:
                        errors.append(
                            f"{task_id}: observation source unit mismatch for "
                            f"{mapping.path!r} on role {observation_source.role!r}: "
                            f"header declares {source_header_unit!r}, "
                            f"profile declares {mapping.source_unit!r}"
                        )
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
                    source_column = profile.source_column_for(observation.role, column)
                    source_unit = _header_unit(source_column)
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
                child_column = next(
                    (column for column in child_join.source_columns if column in relation_headers),
                    None,
                ) if child_join else None
                parent_column = next(
                    (column for column in parent_join.source_columns if column in relation_headers),
                    None,
                ) if parent_join else None
                if child_column and parent_column:
                    child_index = relation_headers.index(child_column)
                    relation_parent_index = relation_headers.index(parent_column)
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
