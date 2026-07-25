"""Shared interpretation of persisted Dataset Profile documents."""
from __future__ import annotations

from typing import Any


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
