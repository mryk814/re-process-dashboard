"""Initialize reserved demo projects without mutating an existing user DB."""
from __future__ import annotations

from collections.abc import Mapping

from material_workbench.contracts.schemas import ProjectInput
from material_workbench.persistence.store import Store
from material_workbench.task_modules import PredictionRuntime, TaskModule


def initialize_demo_projects(
    store: Store,
    modules: Mapping[str, TaskModule],
    runtimes: Mapping[str, PredictionRuntime],
    *,
    seed_candidates: bool,
) -> None:
    for task_id, module in modules.items():
        starter = module.starter_project
        if starter is None:
            continue
        project_existed = store.get_project(starter.project_id) is not None
        store.ensure_project(
            starter.project_id,
            ProjectInput(name=starter.name, task_id=task_id),
        )
        should_seed = seed_candidates or (not project_existed and starter.seed_on_upgrade)
        if not should_seed or store.list_candidates(starter.project_id):
            continue
        for candidate in starter.candidate_factory(runtimes[task_id].data.medians):
            store.create_candidate(candidate, starter.project_id)
