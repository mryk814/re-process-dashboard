from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Mapping

from material_workbench.data.profiles.schema import (
    DatasetInputProfile,
    DatasetProfileError,
    ObservationTarget,
    RelationJoin,
    _header_unit,
    unit_conversion,
)
from material_workbench.data.profiles.validation import (
    _measurement_point_fallback_series,
    _profile_workbook,
    preflight_workbook,
)


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
        if not matches and f"{role}.{name}" in self.profile.shared.optional_technical_fields:
            return None
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
    workbook = _profile_workbook(workbook, profile)
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
    def relation_value(row: Mapping[str, Any], join: RelationJoin) -> Any:
        return next(
            (
                row.get(column)
                for column in join.source_columns
                if row.get(column) is not None and str(row.get(column)).strip()
            ),
            None,
        )

    relations = tuple(
        {
            join.entity_type: (join.entity_type, str(value))
            for join in profile.shared.relation.joins
            if (value := relation_value(row, join)) is not None
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

    def observation_value(
        row: Mapping[str, Any],
        target: ObservationTarget,
        role: str,
    ) -> float | None:
        for column in target.source_columns:
            value = row.get(column)
            if isinstance(value, (int, float)):
                source_column = profile.source_column_for(role, column)
                conversion = unit_conversion(_header_unit(source_column), target.unit)
                if conversion is not None:
                    return float(value) * conversion.scale + conversion.offset
        return None

    observations: list[CanonicalObservation] = []
    for task_id, task in profile.tasks.items():
        for mapping in task.observations:
            sheet_name = profile.sheet_for_role(mapping.role)
            for row_number, row in enumerate(rows[sheet_name], start=2):
                targets = {
                    target.key: value
                    for target in mapping.targets
                    if (value := observation_value(row, target, mapping.role)) is not None
                }
                auxiliary = {
                    target.key: value
                    for target in mapping.auxiliary
                    if (value := observation_value(row, target, mapping.role)) is not None
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
            conversion = unit_conversion(
                field_mapping.source_unit,
                field_mapping.canonical_unit,
            )
            for source in field_mapping.observation_sources:
                observation_mapping = next(item for item in task.observations if item.role == source.role)
                for row in rows[profile.sheet_for_role(source.role)]:
                    value = row.get(source.column)
                    parent = row.get(observation_mapping.parent_column)
                    if (
                        isinstance(value, (int, float))
                        and conversion is not None
                        and parent is not None
                        and str(parent).strip()
                    ):
                        grouped_values.setdefault(str(parent), []).append(
                            float(value) * conversion.scale + conversion.offset
                        )
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
            time_conversion = unit_conversion(
                columns.time_source_unit,
                columns.time_canonical_unit,
            )
            value_conversion = unit_conversion(
                columns.value_source_unit,
                columns.value_canonical_unit,
            )
            series_metadata = {
                item.name: item.column for item in profile.shared.technical
                if item.role == mapping.role and item.name in {"set_temperature_c", "stage_category", "stage_name", "mapping_status"}
            }
            grouped: dict[str, list[tuple[Any, dict[str, Any]]]] = {}
            invalid_parents: set[str] = set()
            for row in rows.get(profile.sheet_for_role(mapping.role), []):
                parent_value = row.get(columns.parent)
                if parent_value is None or not str(parent_value).strip():
                    continue
                parent = str(parent_value)
                order = row.get(columns.order)
                time = row.get(columns.time)
                value = row.get(columns.value)
                if (
                    not isinstance(order, (int, float))
                    or not math.isfinite(float(order))
                    or not isinstance(time, (int, float))
                    or not math.isfinite(float(time))
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    invalid_parents.add(parent)
                    continue
                if time_conversion is None or value_conversion is None:
                    invalid_parents.add(parent)
                    continue
                point = {
                    "time_s": float(row[columns.time]) * time_conversion.scale + time_conversion.offset,
                    "temperature_c": float(row[columns.value]) * value_conversion.scale + value_conversion.offset,
                }
                for name, column in series_metadata.items():
                    point[name] = row.get(column)
                stage_category = profile.stage_category_for(point.get("stage_name"))
                if stage_category:
                    point["stage_category"] = stage_category
                    point["mapping_status"] = "工程辞書一致"
                grouped.setdefault(parent, []).append((float(order), point))
            for parent, ordered in grouped.items():
                if parent in invalid_parents:
                    continue
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
