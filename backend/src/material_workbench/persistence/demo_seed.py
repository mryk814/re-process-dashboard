"""Initialize reserved demo projects without mutating an existing user DB."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from material_workbench.contracts.schemas import ProjectInput
from material_workbench.persistence.store import Store
from material_workbench.task_modules import PredictionRuntime, TaskModule

if TYPE_CHECKING:
    from material_workbench.tasks.task_registry import TaskRegistry


def initialize_demo_projects(
    store: Store,
    modules: Mapping[str, TaskModule],
    runtimes: Mapping[str, PredictionRuntime],
    registry: TaskRegistry,
    *,
    seed_candidates: bool,
) -> None:
    for task_id, module in modules.items():
        starter = module.starter_project
        runtime = runtimes.get(task_id)
        if starter is None or runtime is None:
            continue
        project_existed = store.get_project(starter.project_id) is not None
        store.ensure_project(
            starter.project_id,
            ProjectInput(name=starter.name, task_id=task_id),
            starter=True,
        )
        should_seed = seed_candidates or (not project_existed and starter.seed_on_upgrade)
        if not should_seed or store.list_candidates(starter.project_id):
            continue
        task_definition = registry.contract_for(task_id).task_definition
        for candidate in starter.candidate_factory(
            runtime.data.medians,
            task_definition,
        ):
            registry.validate_candidate(task_id, candidate)
            store.create_candidate(candidate, starter.project_id)
