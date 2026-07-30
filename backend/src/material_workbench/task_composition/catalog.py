"""Immutable catalog of allow-listed built-in task compositions."""
from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from material_workbench.task_composition.builtin_tasks import (
    BUILTIN_TASK_MODULES,
    PRIMARY_DEFAULT_SOURCE,
)
from material_workbench.task_composition.descriptors import TaskModule

TASK_MODULES: Mapping[str, TaskModule] = MappingProxyType(BUILTIN_TASK_MODULES)


def registered_task_modules() -> Mapping[str, TaskModule]:
    from material_workbench.task_composition.external_tasks import (
        external_task_modules,
    )

    external = external_task_modules()
    duplicates = sorted(set(TASK_MODULES) & set(external))
    if duplicates:
        raise ValueError(
            f"external Task IDs cannot replace bundled Tasks: {duplicates}"
        )
    return MappingProxyType({**TASK_MODULES, **external})


def task_module(task_id: str) -> TaskModule:
    try:
        return registered_task_modules()[task_id]
    except KeyError as exc:
        raise ValueError(f"unknown registered task: {task_id}") from exc


def resolve_task_source(task_id: str, source: str | Path | None = None) -> Path:
    module = task_module(task_id)
    selected = Path(source) if source is not None else module.default_source
    if module.source_kind != "primary" and selected == PRIMARY_DEFAULT_SOURCE:
        selected = module.default_source
    if selected.is_absolute() or selected.exists():
        return selected
    repository_source = Path(__file__).resolve().parents[4] / selected
    return repository_source if repository_source.exists() else selected
