from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI

from .api.errors import PROJECT_API_ERRORS, install_exception_handlers
from .api.security import configure_local_access
from .api.catalog import router as catalog_router
from .api.projects import router as projects_router
from .api.candidates import router as candidates_router
from .api.data_exploration import router as data_exploration_router
from .api.screening import router as screening_router
from .api.inference import router as inference_router
from .api.records import router as records_router
from .demo_seed import initialize_demo_projects
from .inference_work_graph import InferenceWorkGraph
from .model_lifecycle import ACTIVE_PACKAGES_PATH, load_active_packages, resolve_configured_package, validate_active_package_task_set
from .store import Store
from .task_registry import DataExplorerEntry, TaskRegistry
from .task_modules import registered_task_modules


def create_app(
    source_path: str | Path | None = None,
    db_path: str | Path | None = None,
    *,
    flank_wear_source_path: str | Path | None = None,
    package_roots: Mapping[str, str | Path] | None = None,
    active_packages_path: str | Path | None = None,
) -> FastAPI:
    source = Path(source_path or os.getenv("WORKBENCH_SOURCE_PATH", "data/source/process_dashboard_realistic_excel_v2.xlsx"))
    database = Path(db_path or os.getenv("WORKBENCH_DB_PATH", "data/workbench.db"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database_existed = database.exists()
        configured = Path(active_packages_path) if active_packages_path else ACTIVE_PACKAGES_PATH
        injected = dict(package_roots or {})
        modules = registered_task_modules()
        validate_active_package_task_set(load_active_packages(configured), set(modules))
        data_by_source: dict[str, Any] = {}
        runtimes = {}
        explorers = {}
        for task_id, module in modules.items():
            if module.source_kind not in data_by_source:
                explicit_source = source if module.source_kind == "primary" else flank_wear_source_path
                configured_source = Path(explicit_source or os.getenv(module.source_env, str(module.default_source)))
                if not configured_source.is_absolute() and not configured_source.exists():
                    repository_source = Path(__file__).resolve().parents[3] / configured_source
                    if repository_source.exists():
                        configured_source = repository_source
                data_by_source[module.source_kind] = module.data_loader(configured_source)
            data = data_by_source[module.source_kind]
            package = resolve_configured_package(
                task_id,
                config_path=configured,
                override=injected.get(task_id) or os.getenv(module.package_override_env),
            )
            runtimes[task_id] = module.runtime_factory(data, package)
            if module.data_explorer is not None:
                explorers[task_id] = DataExplorerEntry(data=data, capability=module.data_explorer)
        app.state.data = data_by_source["primary"]
        app.state.task_registry = TaskRegistry(runtimes, data_explorers=explorers, modules=modules)
        app.state.inference_work_graph = InferenceWorkGraph(max_entries=256)
        app.state.store = Store(database)
        explicit_demo_seed = os.getenv("WORKBENCH_DEMO_SEED", "").strip().lower() in {"1", "true", "yes"}
        initialize_demo_projects(
            app.state.store,
            modules,
            runtimes,
            seed_candidates=not database_existed or explicit_demo_seed,
        )
        yield

    app = FastAPI(
        title="Material Decision Workbench API",
        version="0.1.0",
        lifespan=lifespan,
        responses={422: PROJECT_API_ERRORS[422]},
    )
    configure_local_access(app)
    install_exception_handlers(app)
    app.include_router(catalog_router)
    app.include_router(projects_router)
    app.include_router(candidates_router)
    app.include_router(data_exploration_router)
    app.include_router(screening_router)
    app.include_router(inference_router)
    app.include_router(records_router)

    return app


app = create_app()
