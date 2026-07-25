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

from material_workbench.contracts.blend_contracts import BlendContractRegistry
from .api.errors import PROJECT_API_ERRORS, install_exception_handlers
from .api.security import configure_local_access
from .api.catalog import router as catalog_router
from .api.chains import (
    execution_router as chain_execution_router,
    router as chains_router,
)
from .api.data_library import router as data_library_router
from .api.decision_activities import router as decision_activities_router
from .api.developer import router as developer_router
from .api.project_series import router as project_series_router
from .api.profile_workbench import router as profile_workbench_router
from .api.projects import router as projects_router
from .api.candidates import router as candidates_router
from .api.blend_optimization import router as blend_optimization_router
from .api.data_exploration import router as data_exploration_router
from .api.screening import router as screening_router
from .api.inference import router as inference_router
from .api.records import router as records_router
from .api.transforms import router as transforms_router
from material_workbench.persistence.demo_seed import initialize_demo_projects
from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.modeling.model_lifecycle import ACTIVE_PACKAGES_PATH, load_active_packages, resolve_configured_package, validate_active_package_task_set
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.modeling.transform_catalog import (
    load_deterministic_transform_catalog,
)
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import DataExplorerEntry, TaskRegistry
from material_workbench.persistence.workspace_catalog_bootstrap import bootstrap_workspace_catalog
from material_workbench.persistence.welding_chain_bootstrap import bootstrap_welding_chain
from material_workbench.application.chain_execution import (
    ChainExecutionCoordinator,
    ChainExecutionService,
)
from material_workbench.application.chain_uncertainty import ChainUncertaintyService
from material_workbench.application.chain_evaluation import (
    ChainEvaluationCatalog,
    DEFAULT_CHAIN_EVALUATION_PATH,
)
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver
from .task_modules import (
    PRIMARY_DEFAULT_SOURCE,
    PredictionRuntime,
    TaskModule,
    registered_task_modules,
)
from material_workbench.contracts.task_contracts import TaskAvailability

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


def _task_unavailable(
    task_id: str,
    *,
    stage: str,
    label: str,
    exc: Exception,
) -> TaskAvailability:
    logger.warning(
        "TASK_UNAVAILABLE task_id=%s stage=%s error_type=%s detail=%s",
        task_id,
        stage,
        type(exc).__name__,
        exc,
    )
    message = (
        f"{label}のファイルが見つかりません。設定を確認して再起動してください。"
        if isinstance(exc, FileNotFoundError)
        else f"{label}を準備できません: {exc}"
    )
    return TaskAvailability(status="unavailable", stage=stage, message=message)


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
    unavailable: dict[str, TaskAvailability] = {}
    for task_id, module in modules.items():
        try:
            explicit_source = (
                source
                if module.source_kind == "primary"
                else flank_wear_source_path if module.source_kind == "flank_wear" else None
            )
            configured_source = Path(explicit_source or os.getenv(module.source_env, str(module.default_source)))
            if not configured_source.is_absolute() and not configured_source.exists():
                repository_source = Path(__file__).resolve().parents[3] / configured_source
                if repository_source.exists():
                    configured_source = repository_source
            loaded = module.data_loader(configured_source, None)
            data_by_source[task_id] = loaded
            data_by_source.setdefault(module.source_kind, loaded)
        except (OSError, ValueError, KeyError) as exc:
            unavailable[task_id] = _task_unavailable(
                task_id, stage="source", label="データソース", exc=exc
            )
            continue
        try:
            package = ModelPackageLoader().load(
                resolve_configured_package(
                    task_id,
                    config_path=configured,
                    override=injected.get(task_id) or os.getenv(module.package_override_env),
                )
            )
        except (OSError, ValueError, KeyError) as exc:
            unavailable[task_id] = _task_unavailable(
                task_id, stage="package", label="Model Package", exc=exc
            )
            continue
        try:
            runtimes[task_id] = module.runtime_factory(loaded, package)
        except (OSError, ValueError, KeyError) as exc:
            unavailable[task_id] = _task_unavailable(
                task_id, stage="runtime", label="予測runtime", exc=exc
            )
            continue
        if module.data_explorer is not None:
            explorers[task_id] = DataExplorerEntry(data=loaded, capability=module.data_explorer)
    task_registry = TaskRegistry(
        runtimes,
        data_explorers=explorers,
        modules=modules,
        unavailable=unavailable,
        degrade_invalid_runtimes=True,
    )
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
    active_transforms_path: str | Path | None = None,
    chain_evaluation_path: str | Path | None = None,
    blend_contracts: BlendContractRegistry | None = None,
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
        app.state.data = prepared.data_by_source.get("primary") or next(
            iter(prepared.data_by_source.values()), None
        )
        app.state.task_registry = prepared.task_registry
        app.state.blend_contract_registry = blend_contracts or BlendContractRegistry()
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
        try:
            app.state.deterministic_transform_catalog = (
                load_deterministic_transform_catalog(active_transforms_path)
            )
        except Exception as exc:
            _raise_startup_error(
                "deterministic_transforms",
                "決定論的Transform Package",
                exc,
            )
        try:
            app.state.welding_chain_revision_id = bootstrap_welding_chain(
                store=app.state.store,
                workspace_catalog=app.state.workspace_catalog,
                task_registry=prepared.task_registry,
                transform_catalog=app.state.deterministic_transform_catalog,
            )
        except Exception as exc:
            _raise_startup_error(
                "chain_catalog",
                "多段Chainカタログ",
                exc,
            )
        try:
            app.state.chain_evaluation_catalog = ChainEvaluationCatalog.load(
                chain_evaluation_path or DEFAULT_CHAIN_EVALUATION_PATH
            )
        except Exception as exc:
            _raise_startup_error(
                "chain_evaluation",
                "多段Chain評価成果物",
                exc,
            )
        app.state.chain_execution_service = ChainExecutionService(
            app.state.store,
            prepared.task_registry,
            app.state.deterministic_transform_catalog,
            ChainExecutionCoordinator(),
        )
        app.state.chain_uncertainty_service = ChainUncertaintyService(
            app.state.store,
            app.state.chain_execution_service,
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
    app.include_router(chains_router)
    app.include_router(chain_execution_router)
    app.include_router(data_library_router)
    app.include_router(developer_router)
    app.include_router(project_series_router)
    app.include_router(profile_workbench_router)
    app.include_router(projects_router)
    app.include_router(candidates_router)
    app.include_router(blend_optimization_router)
    app.include_router(data_exploration_router)
    app.include_router(screening_router)
    app.include_router(decision_activities_router)
    app.include_router(inference_router)
    app.include_router(records_router)
    app.include_router(transforms_router)

    return app


app = create_app()
