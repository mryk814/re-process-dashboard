"""Allow-listed training-row presentation for canonical Task data."""
from __future__ import annotations

from typing import Any, Mapping, Sequence


_IDENTIFIER_COLUMNS: tuple[dict[str, Any], ...] = (
    {"key": "observation_id", "label": "実測ID", "unit": None, "group": "識別"},
    {"key": "parent_key", "label": "親工程条件", "unit": None, "group": "識別"},
)


def _heat_pattern_label(points: Any) -> str | None:
    if not isinstance(points, list) or not points:
        return None
    labels = [
        f"{float(point['time_s']):g}s / {float(point['temperature_c']):g}℃"
        for point in points
        if isinstance(point, dict)
        and point.get("time_s") is not None
        and point.get("temperature_c") is not None
    ]
    return " → ".join(labels) if labels else None


class CanonicalTrainingInspectorAdapter:
    """Presentation for the canonical composition/process observation layout.

    This is intentionally a Task-composition adapter rather than a generic
    application service: it is the only layer that knows how canonical input
    paths are represented in raw observation dictionaries.
    """

    def selected_input_values(
        self,
        observation: Mapping[str, Any],
        input_paths: Sequence[str],
    ) -> Mapping[str, Any]:
        process = observation.get("features") or {}
        composition = observation.get("composition") or {}
        values: dict[str, Any] = {}
        for path in input_paths:
            if path == "heat_pattern":
                values[path] = _heat_pattern_label(process.get("heat_pattern"))
                continue
            group, key = path.split(".", 1)
            values[path] = (
                composition.get(key)
                if group == "composition"
                else process.get(key)
            )
        return values

    def parent_condition_metadata(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        return {"composition_key": rows[0].get("composition_key")}

    def feature_identifier_columns(
        self,
        training_unit: str,
    ) -> Sequence[Mapping[str, Any]]:
        if training_unit == "parent_condition_mean":
            return (
                {"key": "parent_key", "label": "親工程条件", "unit": None, "group": "識別"},
                {"key": "composition_key", "label": "成分キー", "unit": None, "group": "識別"},
                {"key": "observation_ids", "label": "実測ID", "unit": None, "group": "識別"},
                {"key": "replicate_count", "label": "個々値数", "unit": "件", "group": "識別"},
            )
        if training_unit == "replicate_context_mean":
            return (
                {"key": "replicate_context", "label": "反復コンテキスト", "unit": None, "group": "識別"},
                {"key": "validation_group", "label": "検証グループ", "unit": None, "group": "識別"},
                {"key": "observation_ids", "label": "実測ID", "unit": None, "group": "識別"},
                {"key": "replicate_count", "label": "個々値数", "unit": "件", "group": "識別"},
            )
        return _IDENTIFIER_COLUMNS

    def feature_identifier_values(
        self,
        row: Mapping[str, Any],
        training_unit: str,
    ) -> Mapping[str, Any]:
        values: dict[str, Any] = {"parent_key": row["parent_key"]}
        if training_unit == "individual_observation":
            values["observation_id"] = row["observation_id"]
        if training_unit == "parent_condition_mean":
            values["composition_key"] = row.get("presentation", {}).get(
                "composition_key"
            )
        if training_unit == "replicate_context_mean":
            values["replicate_context"] = row["replicate_context"]
            values["validation_group"] = row["validation_group"]
        if training_unit in {"parent_condition_mean", "replicate_context_mean"}:
            values["observation_ids"] = ", ".join(row["observation_ids"])
            values["replicate_count"] = row["replicate_count"]
        return values

    def output_space_context(
        self,
        rows: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        process_keys = {str(row["parent_key"]) for row in rows}
        composition_keys = {
            str(row["composition_key"])
            for row in rows
            if row.get("composition_key")
        }
        return {
            "process_key": (
                next(iter(process_keys)) if len(process_keys) == 1 else None
            ),
            "composition_key": (
                next(iter(composition_keys)) if len(composition_keys) == 1 else None
            ),
        }


CANONICAL_TRAINING_INSPECTOR = CanonicalTrainingInspectorAdapter()
