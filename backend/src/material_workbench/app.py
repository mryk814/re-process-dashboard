from __future__ import annotations

import os
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from fastapi import FastAPI

from .api.errors import PROJECT_API_ERRORS, install_exception_handlers
from .api.security import configure_local_access
from .api.catalog import router as catalog_router
from .api.data_library import router as data_library_router
from .api.developer import router as developer_router
from .api.project_series import router as project_series_router
from .api.profile_workbench import router as profile_workbench_router
from .api.projects import router as projects_router
from .api.candidates import router as candidates_router
from .api.data_exploration import router as data_exploration_router
from .api.screening import router as screening_router
from .api.inference import router as inference_router
from .api.records import router as records_router
from material_workbench.persistence.demo_seed import initialize_demo_projects
from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.modeling.model_lifecycle import ACTIVE_PACKAGES_PATH, load_active_packages, resolve_configured_package, validate_active_package_task_set
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import DataExplorerEntry, TaskRegistry
from material_workbench.persistence.workspace_catalog_bootstrap import bootstrap_workspace_catalog
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver
from .task_modules import (
    PRIMARY_DEFAULT_SOURCE,
    PredictionRuntime,
    TaskModule,
    registered_task_modules,
)

logger = logging.getLogger(__name__)


def _raise_startup_error(stage: str, label: str, exc: Exception) -> None:
    payload = {
        "stage": stage,
        "label": label,
        "error_type": type(exc).__name__,
        "detail": str(exc),
    }
    logger.exception(
        "WORKBENCH_STARTUP_ERROR %s",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    raise exc


@dataclass(frozen=True)
class _AppResources:
    """Prepared source data and model runtimes treated as read-only by the app."""

    modules: Mapping[str, TaskModule]
    data_by_source: Mapping[str, Any]
    runtimes: Mapping[str, PredictionRuntime]
    task_registry: TaskRegistry


def _prepare_app_resources(
    source_path: str | Path | None = None,
    *,
    flank_wear_source_path: str | Path | None = None,
    package_roots: Mapping[str, str | Path] | None = None,
    active_packages_path: str | Path | None = None,
) -> _AppResources:
    """Load workbook and package resources that callers treat as read-only."""

    source = Path(
        source_path
        or os.getenv("WORKBENCH_SOURCE_PATH", str(PRIMARY_DEFAULT_SOURCE))
    )
    configured = Path(active_packages_path) if active_packages_path else ACTIVE_PACKAGES_PATH
    injected = dict(package_roots or {})
    modules = dict(registered_task_modules())
    validate_active_package_task_set(load_active_packages(configured), set(modules))
    data_by_source: dict[str, Any] = {}
    runtimes: dict[str, PredictionRuntime] = {}
    explorers: dict[str, DataExplorerEntry] = {}
    for task_id, module in modules.items():
        if module.source_kind not in data_by_source:
            explicit_source = source if module.source_kind == "primary" else flank_wear_source_path
            configured_source = Path(explicit_source or os.getenv(module.source_env, str(module.default_source)))
            if not configured_source.is_absolute() and not configured_source.exists():
                repository_source = Path(__file__).resolve().parents[3] / configured_source
                if repository_source.exists():
                    configured_source = repository_source
            data_by_source[module.source_kind] = module.data_loader(configured_source, None)
        data = data_by_source[module.source_kind]
        package = resolve_configured_package(
            task_id,
            config_path=configured,
            override=injected.get(task_id) or os.getenv(module.package_override_env),
        )
        runtimes[task_id] = module.runtime_factory(data, package)
        if module.data_explorer is not None:
            explorers[task_id] = DataExplorerEntry(data=data, capability=module.data_explorer)
    task_registry = TaskRegistry(runtimes, data_explorers=explorers, modules=modules)
    return _AppResources(
        modules=MappingProxyType(modules),
        data_by_source=MappingProxyType(data_by_source),
        runtimes=MappingProxyType(runtimes),
        task_registry=task_registry,
    )


def create_app(
    source_path: str | Path | None = None,
    db_path: str | Path | None = None,
    *,
    flank_wear_source_path: str | Path | None = None,
    package_roots: Mapping[str, str | Path] | None = None,
    active_packages_path: str | Path | None = None,
    data_library_path: str | Path | None = None,
    _resources: _AppResources | None = None,
) -> FastAPI:
    database = Path(db_path or os.getenv("WORKBENCH_DB_PATH", "data/workbench.db"))
    data_library_root = Path(
        data_library_path
        or os.getenv("WORKBENCH_DATA_LIBRARY_PATH", "")
        or database.parent / "data-library"
    )
    if _resources is not None and any(
        value is not None
        for value in (source_path, flank_wear_source_path, package_roots, active_packages_path)
    ):
        raise ValueError("preloaded resources cannot be combined with source or package overrides")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database_existed = database.exists()
        try:
            prepared = _resources or _prepare_app_resources(
                source_path,
                flank_wear_source_path=flank_wear_source_path,
                package_roots=package_roots,
                active_packages_path=active_packages_path,
            )
        except Exception as exc:
            _raise_startup_error("resources", "データ・Model Package", exc)
        app.state.data = prepared.data_by_source["primary"]
        app.state.task_registry = prepared.task_registry
        app.state.inference_work_graph = InferenceWorkGraph(max_entries=256)
        try:
            app.state.store = Store(database)
            explicit_demo_seed = os.getenv("WORKBENCH_DEMO_SEED", "").strip().lower() in {"1", "true", "yes"}
            initialize_demo_projects(
                app.state.store,
                prepared.modules,
                prepared.runtimes,
                seed_candidates=not database_existed or explicit_demo_seed,
            )
        except Exception as exc:
            _raise_startup_error("database", "ワークスペースDB", exc)
        try:
            app.state.workspace_catalog = bootstrap_workspace_catalog(database, prepared.task_registry)
        except Exception as exc:
            _raise_startup_error("catalog", "データ・モデルカタログ", exc)
        app.state.data_library_root = data_library_root.resolve()
        app.state.project_runtime_resolver = ProjectRuntimeResolver(
            app.state.workspace_catalog, prepared.task_registry
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
    app.include_router(data_library_router)
    app.include_router(developer_router)
    app.include_router(project_series_router)
    app.include_router(profile_workbench_router)
    app.include_router(projects_router)
    app.include_router(candidates_router)
    app.include_router(data_exploration_router)
    app.include_router(screening_router)
    app.include_router(inference_router)
    app.include_router(records_router)

    return app


app = create_app()
