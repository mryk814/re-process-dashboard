"""Shared interpretation of persisted Dataset Profile documents."""
from __future__ import annotations

from typing import Any
from pathlib import Path


def supported_task_ids(document: dict[str, Any]) -> tuple[str, ...]:
    if document.get("schema_version") in {
        "tabular-dataset-profile/v1",
        "observation-dataset-profile/v1",
        "welding-stage-b-profile/v1",
    }:
        task_id = document.get("task_id")
        return (task_id,) if isinstance(task_id, str) and task_id else ()
    tasks = document.get("tasks")
    return tuple(sorted(tasks)) if isinstance(tasks, dict) else ()


def profile_task_ids(profile: Any) -> tuple[str, ...]:
    task_id = getattr(profile, "task_id", None)
    if isinstance(task_id, str) and task_id:
        return (task_id,)
    tasks = getattr(profile, "tasks", {})
    return tuple(sorted(tasks)) if isinstance(tasks, dict) else ()


def load_profile_document(path: str | Path) -> Any:
    """Load any allow-listed Dataset Profile family from one explicit path."""
    profile_path = Path(path).resolve(strict=True)
    document = __import__("json").loads(profile_path.read_text(encoding="utf-8"))
    schema_version = document.get("schema_version")
    if schema_version == "tabular-dataset-profile/v1":
        from material_workbench.modeling.tabular_regression import (
            load_tabular_profile,
        )

        return load_tabular_profile(profile_path)
    if schema_version == "observation-dataset-profile/v1":
        from material_workbench.data.observation_profile import (
            load_observation_profile,
        )

        return load_observation_profile(profile_path)
    if schema_version == "welding-stage-b-profile/v1":
        from material_workbench.data.stage_b_training import load_stage_b_profile

        return load_stage_b_profile(profile_path)
    from material_workbench.data.dataset_profile import load_dataset_profile

    return load_dataset_profile(profile_path)


def lifecycle_profile_for_data(data: Any) -> Any:
    """Return the Profile that owns dataset identity, not a derived runtime view."""
    lifecycle_profile = getattr(data, "lifecycle_profile", None)
    if lifecycle_profile is not None:
        return lifecycle_profile
    profile = getattr(data, "profile", None)
    if profile is not None:
        return profile
    return Path(data.profile_path)
