"""Initialize reserved demo projects without mutating an existing user DB."""
from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING

from material_workbench.contracts.candidate_project_contracts import (
    CandidateInput,
    ProjectInput,
)
from material_workbench.persistence.store import Store
from material_workbench.task_composition.descriptors import TaskModule
from material_workbench.task_composition.ports import PredictionRuntime

if TYPE_CHECKING:
    from material_workbench.tasks.task_registry import TaskRegistry


QUICKSTART_PROJECT_ID = "default"


def starter_project_ids(modules: Mapping[str, TaskModule]) -> frozenset[str]:
    return frozenset(
        starter.project_id
        for module in modules.values()
        if (starter := module.starter_project) is not None
    )


def gallery_project_ids(modules: Mapping[str, TaskModule]) -> frozenset[str]:
    """Return the intentionally small user-facing sample portfolio."""

    return frozenset(
        starter.project_id
        for module in modules.values()
        if (
            (starter := module.starter_project) is not None
            and starter.distribution == "gallery"
        )
    )


def installed_starter_project_ids(
    store: Store,
    modules: Mapping[str, TaskModule],
) -> frozenset[str]:
    return frozenset(
        project_id
        for project_id in starter_project_ids(modules)
        if store.get_project(project_id, include_archived=True) is not None
    )


def initialize_demo_projects(
    store: Store,
    modules: Mapping[str, TaskModule],
    runtimes: Mapping[str, PredictionRuntime],
    registry: TaskRegistry,
    *,
    seed_candidates: bool,
    project_ids: Collection[str] | None = None,
) -> None:
    selected = None if project_ids is None else frozenset(project_ids)
    for task_id, module in modules.items():
        starter = module.starter_project
        runtime = runtimes.get(task_id)
        if (
            starter is None
            or runtime is None
            or (selected is not None and starter.project_id not in selected)
        ):
            continue
        project_existed = store.get_project(starter.project_id) is not None
        store.ensure_project(
            starter.project_id,
            ProjectInput(name=starter.name, task_id=task_id),
            starter=True,
        )
        should_seed = seed_candidates or (not project_existed and starter.seed_on_upgrade)
        task_definition = registry.contract_for(task_id).task_definition
        existing_candidates = store.list_candidates(starter.project_id)
        if (
            existing_candidates
            and starter.legacy_candidate_factory is not None
        ):
            legacy = starter.legacy_candidate_factory(runtime, task_definition)
            current_payloads = [
                CandidateInput.model_validate(candidate.model_dump())
                for candidate in existing_candidates
            ]
            if current_payloads == legacy:
                replacements = starter.candidate_factory(runtime, task_definition)
                if len(replacements) != len(existing_candidates):
                    raise ValueError(
                        "Starter candidate migration must preserve candidate identity"
                    )
                for current, replacement in zip(
                    existing_candidates, replacements, strict=True
                ):
                    registry.validate_candidate(task_id, replacement)
                    store.update_candidate(
                        current.id,
                        starter.project_id,
                        replacement,
                        current.revision,
                    )
                existing_candidates = store.list_candidates(starter.project_id)
        if not should_seed or existing_candidates:
            continue
        for candidate in starter.candidate_factory(
            runtime,
            task_definition,
        ):
            registry.validate_candidate(task_id, candidate)
            store.create_candidate(candidate, starter.project_id)


def install_starter_projects(
    store: Store,
    registry: TaskRegistry,
    project_ids: Collection[str],
) -> list[str]:
    selected = frozenset(project_ids)
    unknown = selected - starter_project_ids({
        task_id: registry.module_for(task_id)
        for task_id in registry.task_ids
    })
    if unknown:
        raise KeyError(f"unknown starter projects: {sorted(unknown)}")
    modules = {
        task_id: registry.module_for(task_id)
        for task_id in registry.available_task_ids
    }
    runtimes = {
        task_id: registry.runtime_for(task_id)
        for task_id in registry.available_task_ids
    }
    initialize_demo_projects(
        store,
        modules,
        runtimes,
        registry,
        seed_candidates=True,
        project_ids=selected,
    )
    return sorted(
        project_id
        for project_id in selected
        if store.get_project(project_id, include_archived=True) is not None
    )
