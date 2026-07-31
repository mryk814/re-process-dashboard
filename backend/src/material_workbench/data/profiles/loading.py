from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from pydantic import ValidationError

from material_workbench.contracts.task_contracts import TaskDefinition
from material_workbench.data.profiles.schema import (
    PROFILE_SCHEMA_VERSION,
    DatasetInputProfile,
    DatasetProfileError,
    TaskProfile,
)
from material_workbench.data.profiles.validation import validate_profile


def load_task_definitions(directory: str | Path | None = None) -> dict[str, TaskDefinition]:
    root = Path(directory) if directory else Path(__file__).parents[2] / "tasks" / "task_definitions"
    definitions: dict[str, TaskDefinition] = {}
    for source in sorted(root.glob("*.json")):
        document = json.loads(source.read_text(encoding="utf-8"))
        definition = TaskDefinition.model_validate(document["task_definition"])
        if definition.id in definitions:
            raise DatasetProfileError([f"duplicate production TaskDefinition id {definition.id!r}"])
        definitions[definition.id] = definition
    if not definitions:
        raise DatasetProfileError([f"no production TaskDefinitions found in {root}"])
    if directory is None:
        from material_workbench.task_composition.external_tasks import (
            external_task_contracts,
        )

        external = {
            task_id: fixture.task_definition
            for task_id, fixture in external_task_contracts().items()
        }
        duplicates = sorted(set(definitions) & set(external))
        if duplicates:
            raise DatasetProfileError([
                f"external TaskDefinitions cannot replace bundled Tasks: {duplicates}"
            ])
        definitions.update(external)
    return definitions


def load_dataset_profile(
    path: str | Path | None = None,
    task_definitions: Mapping[str, TaskDefinition] | None = None,
) -> DatasetInputProfile:
    profile_path = Path(path) if path else Path(__file__).parent.parent / "dataset-input-profile-tutorial.json"
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
            if task_id not in definitions:
                continue
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
        shared = dict(raw["shared"])
        physical_ranges = shared.get("physical_ranges")
        if isinstance(physical_ranges, dict):
            shared["physical_ranges"] = {
                task_id: ranges
                for task_id, ranges in physical_ranges.items()
                if task_id in definitions
            }
        profile = DatasetInputProfile(
            profile_id=str(raw["id"]),
            shared=shared,
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


def materialize_dataset_profile_document(path: Path) -> dict[str, Any]:
    """Resolve Profile inheritance into a standalone JSON-compatible document."""
    return _load_profile_document(path)
