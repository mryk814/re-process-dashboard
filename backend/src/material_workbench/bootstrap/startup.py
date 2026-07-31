"""Application startup lifecycle and generation-safe resource promotion."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI

from material_workbench.application.ai_review_provider import AiReviewProvider
from material_workbench.application.project_runtime import ProjectRuntimeResolver
from material_workbench.application.workspace_catalog_bootstrap import (
    bootstrap_workspace_catalog,
)
from material_workbench.bootstrap.contributions import (
    ApplicationContribution,
    ApplicationContributionContext,
    ApplicationContributionRuntime,
    initialize_application_contributions,
    install_application_contribution_state,
    rebuild_application_contributions,
)
from material_workbench.bootstrap.resources import (
    AppResources,
    prepare_app_resources,
)
from material_workbench.contracts.data_library_contracts import (
    TaskResourceRefreshResult,
    present_resource_warning,
)
from material_workbench.contracts.subsystem_availability import (
    SubsystemAvailabilityRegistry,
)
from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.modeling.model_lifecycle import (
    ACTIVE_PACKAGES_PATH,
    AVAILABLE_PACKAGES_PATH,
    validate_personal_model_store_path,
)
from material_workbench.developer_experience.task_scaffolding import (
    validate_personal_task_store_path,
)
from material_workbench.persistence.demo_seed import (
    QUICKSTART_PROJECT_ID,
    initialize_demo_projects,
    installed_starter_project_ids,
    starter_project_ids,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.task_composition.builtin.annealed import ANNEALED_TASK_ID
from material_workbench.tasks.task_registry import TaskRegistry

logger = logging.getLogger(__name__)
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def personal_data_library_path() -> Path:
    """Return the user-owned root for managed personal Dataset sources."""

    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return (
            Path(local_app_data)
            / "Material Decision Workbench"
            / "data-library"
        ).resolve()
    xdg_data_home = os.getenv("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return (base / "material-decision-workbench" / "data-library").resolve()


def default_data_library_path(database: Path) -> Path:
    """Keep the checked-out main workspace from becoming a personal data sink."""

    repository_workspace = (REPOSITORY_ROOT / "data" / "workbench.db").resolve()
    if database.expanduser().resolve() == repository_workspace:
        return personal_data_library_path()
    return database.expanduser().resolve().parent / "data-library"


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
class RuntimeContext:
    data: Any
    task_registry: TaskRegistry
    workspace_catalog: Any
    project_runtime_resolver: ProjectRuntimeResolver
    contribution_runtimes: Mapping[str, ApplicationContributionRuntime]


def _backup_sqlite(source: Path, destination: Path) -> None:
    """Create one logical SQLite snapshot without copying a live journal."""

    destination.unlink(missing_ok=True)
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
        destination_connection.commit()
    finally:
        destination_connection.close()
        source_connection.close()


_SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")


def _sqlite_generation_paths(database: Path) -> tuple[Path, ...]:
    return (
        database,
        *(Path(f"{database}{suffix}") for suffix in _SQLITE_SIDECAR_SUFFIXES),
    )


def _remove_sqlite_generation(database: Path) -> None:
    for path in _sqlite_generation_paths(database):
        path.unlink(missing_ok=True)


def _preserve_live_sqlite_generation(
    source: Path,
    destination: Path,
) -> None:
    """Move the byte-exact live SQLite generation to a private name.

    Readers are already idle and middleware blocks new requests while this
    runs. The main file and every sidecar remain one preserved generation.
    """

    _remove_sqlite_generation(destination)
    moved: list[tuple[Path, Path]] = []
    try:
        for source_path, destination_path in zip(
            _sqlite_generation_paths(source),
            _sqlite_generation_paths(destination),
            strict=True,
        ):
            if source_path.exists():
                os.replace(source_path, destination_path)
                moved.append((source_path, destination_path))
    except Exception:
        for source_path, destination_path in reversed(moved):
            if destination_path.exists():
                os.replace(destination_path, source_path)
        raise


def _restore_live_sqlite_generation(
    preserved: Path,
    destination: Path,
) -> None:
    """Restore preserved sidecars, then atomically republish the main file."""

    _remove_sqlite_generation(destination)
    preserved_paths = _sqlite_generation_paths(preserved)
    destination_paths = _sqlite_generation_paths(destination)
    for index in range(1, len(preserved_paths)):
        if preserved_paths[index].exists():
            os.replace(preserved_paths[index], destination_paths[index])
    os.replace(preserved_paths[0], destination_paths[0])


def create_lifespan(
    db_path: str | Path | None = None,
    *,
    source_overrides: Mapping[str, str | Path] | None = None,
    package_roots: Mapping[str, str | Path] | None = None,
    active_packages_path: str | Path | None = None,
    model_store_path: str | Path | None = None,
    task_store_path: str | Path | None = None,
    data_library_path: str | Path | None = None,
    contributions: tuple[ApplicationContribution, ...] = (),
    ai_review_provider: AiReviewProvider | None = None,
    resources: AppResources | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    database = Path(db_path or os.getenv("WORKBENCH_DB_PATH", "data/workbench.db"))
    data_library_root = Path(
        data_library_path
        or os.getenv("WORKBENCH_DATA_LIBRARY_PATH", "")
        or default_data_library_path(database)
    )
    configured_active_packages_path = Path(
        active_packages_path or ACTIVE_PACKAGES_PATH
    ).resolve()
    personal_store = None
    if model_store_path is not None:
        personal_store = validate_personal_model_store_path(Path(model_store_path))
        personal_store.mkdir(parents=True, exist_ok=True)
    personal_task_store = validate_personal_task_store_path(
        Path(task_store_path) if task_store_path is not None else None
    )
    configured_available_packages_paths = tuple(
        dict.fromkeys(
            (
                AVAILABLE_PACKAGES_PATH.resolve(),
                *(
                    (personal_store / "available-packages.json",)
                    if personal_store is not None
                    else (
                        (
                            configured_active_packages_path.with_name(
                                "available-packages.json"
                            ),
                        )
                        if active_packages_path is not None
                        else ()
                    )
                ),
            )
        )
    )
    configured_personal_available_packages_paths = (
        (personal_store / "available-packages.json",)
        if personal_store is not None
        else ()
    )
    if resources is not None and any(
        value is not None
        for value in (source_overrides, package_roots, active_packages_path)
    ):
        raise ValueError(
            "preloaded resources cannot be combined with source or package overrides"
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        model_package_origins: dict[str, str] = {}
        model_store_warnings = []
        database_existed = database.exists()
        defer_resources = resources is None and os.getenv(
            "WORKBENCH_DEFER_RESOURCES", ""
        ).strip().lower() in {"1", "true", "yes"}
        app.state.workspace_database = database.expanduser().resolve()
        app.state.workspace_kind = os.getenv(
            "WORKBENCH_WORKSPACE_KIND", ""
        ).strip() or (
            "main"
            if app.state.workspace_database == Path("data/workbench.db").resolve()
            else "custom"
        )
        try:
            prepared = resources or prepare_app_resources(
                source_overrides=source_overrides,
                package_roots=package_roots,
                active_packages_path=active_packages_path,
                task_ids=(
                    frozenset(
                        {
                            os.getenv(
                                "WORKBENCH_STARTUP_TASK_ID",
                                ANNEALED_TASK_ID,
                            )
                        }
                    )
                    if defer_resources
                    else None
                ),
            )
        except Exception as exc:
            _raise_startup_error("resources", "データ・Model Package", exc)
        app.state.data = prepared.default_data
        app.state.task_registry = prepared.task_registry
        app.state.ai_review_provider = ai_review_provider
        app.state.inference_work_graph = InferenceWorkGraph(max_entries=256)
        try:
            app.state.store = Store(database)
            demo_seed_mode = os.getenv("WORKBENCH_DEMO_SEED", "").strip().lower()
            selected_starters = set(
                installed_starter_project_ids(app.state.store, prepared.modules)
            )
            selected_starters.add(QUICKSTART_PROJECT_ID)
            if demo_seed_mode == "all":
                selected_starters.update(starter_project_ids(prepared.modules))
            initialize_demo_projects(
                app.state.store,
                prepared.modules,
                prepared.runtimes,
                prepared.task_registry,
                seed_candidates=(
                    not database_existed
                    or demo_seed_mode in {"1", "true", "yes", "all"}
                ),
                project_ids=selected_starters,
            )
        except Exception as exc:
            _raise_startup_error("database", "ワークスペースDB", exc)
        try:
            app.state.workspace_catalog = bootstrap_workspace_catalog(
                database,
                prepared.task_registry,
                available_packages_paths=configured_available_packages_paths,
                personal_available_packages_paths=(
                    configured_personal_available_packages_paths
                ),
                package_origins=model_package_origins,
                warnings=model_store_warnings,
            )
        except Exception as exc:
            _raise_startup_error("catalog", "データ・モデルカタログ", exc)
        app.state.data_library_root = data_library_root.resolve()
        app.state.available_packages_paths = configured_available_packages_paths
        app.state.personal_available_packages_paths = (
            configured_personal_available_packages_paths
        )
        app.state.model_package_origins = model_package_origins
        app.state.model_store_warnings = model_store_warnings
        app.state.model_store_path = personal_store
        app.state.task_store_path = personal_task_store
        app.state.project_runtime_resolver = ProjectRuntimeResolver(
            app.state.workspace_catalog, prepared.task_registry
        )
        app.state.subsystem_availability = SubsystemAvailabilityRegistry()
        contribution_context = ApplicationContributionContext(
            store=app.state.store,
            workspace_catalog=app.state.workspace_catalog,
            task_registry=prepared.task_registry,
            subsystem_availability=app.state.subsystem_availability,
        )
        contribution_runtimes = initialize_application_contributions(
            contributions,
            contribution_context,
            defer_resources=defer_resources,
        )
        install_application_contribution_state(app, contribution_runtimes)
        app.state.runtime_context = RuntimeContext(
            data=app.state.data,
            task_registry=app.state.task_registry,
            workspace_catalog=app.state.workspace_catalog,
            project_runtime_resolver=app.state.project_runtime_resolver,
            contribution_runtimes=contribution_runtimes,
        )
        app.state.resources_ready = not defer_resources
        app.state.resources_promoting = False
        app.state.resources_loading_error = None
        app.state.active_resource_requests = 0
        app.state.resource_requests_idle = asyncio.Event()
        app.state.resource_requests_idle.set()
        app.state.resource_promotion_complete = asyncio.Event()
        app.state.resource_promotion_complete.set()
        task_resource_refresh_lock = asyncio.Lock()
        app.state.csv_onboarding_lock = asyncio.Lock()

        async def refresh_task_resources() -> TaskResourceRefreshResult:
            """Stage Task, Package, and DB changes before one generation swap."""

            async with task_resource_refresh_lock:
                previous_task_ids = set(
                    app.state.runtime_context.task_registry.available_task_ids
                )
                previous_model_package_ids = {
                    item.id
                    for item in (
                        app.state.runtime_context.workspace_catalog.list_model_package_refs(
                            include_archived=True
                        )
                    )
                }
                complete = await asyncio.to_thread(
                    prepare_app_resources,
                    source_overrides=source_overrides,
                    package_roots=package_roots,
                    active_packages_path=active_packages_path,
                )
                refresh_warnings: list[Any] = []
                refreshed_origins = dict(model_package_origins)
                workspace_database = Path(app.state.workspace_database)
                staged_database = workspace_database.with_name(
                    f".{workspace_database.name}.task-refresh-{uuid4().hex}.db"
                )
                rollback_database = workspace_database.with_name(
                    f".{workspace_database.name}.task-refresh-rollback-{uuid4().hex}.db"
                )

                def context_for(catalog: WorkspaceCatalog) -> RuntimeContext:
                    resolver = ProjectRuntimeResolver(
                        catalog,
                        complete.task_registry,
                    )
                    refreshed_contributions = rebuild_application_contributions(
                        contributions,
                        ApplicationContributionContext(
                            store=app.state.store,
                            workspace_catalog=catalog,
                            task_registry=complete.task_registry,
                            subsystem_availability=(app.state.subsystem_availability),
                        ),
                        app.state.runtime_context.contribution_runtimes,
                        promote_deferred=False,
                    )
                    return RuntimeContext(
                        data=complete.default_data,
                        task_registry=complete.task_registry,
                        workspace_catalog=catalog,
                        project_runtime_resolver=resolver,
                        contribution_runtimes=refreshed_contributions,
                    )

                app.state.resource_promotion_complete.clear()
                app.state.resources_promoting = True
                try:
                    # The refresh request is not counted as a resource reader.
                    # New requests receive 503 while existing readers finish
                    # against the context captured by middleware.
                    await app.state.resource_requests_idle.wait()

                    def stage() -> None:
                        _backup_sqlite(workspace_database, staged_database)
                        staged_catalog = bootstrap_workspace_catalog(
                            staged_database,
                            complete.task_registry,
                            available_packages_paths=(
                                configured_available_packages_paths
                            ),
                            personal_available_packages_paths=(
                                configured_personal_available_packages_paths
                            ),
                            package_origins=refreshed_origins,
                            warnings=refresh_warnings,
                        )
                        # Constructors and every contract check must succeed
                        # before the live database can be replaced.
                        context_for(staged_catalog)

                    await asyncio.to_thread(stage)
                    _preserve_live_sqlite_generation(
                        workspace_database,
                        rollback_database,
                    )
                    try:
                        os.replace(staged_database, workspace_database)
                        live_catalog = WorkspaceCatalog(workspace_database)
                        context = context_for(live_catalog)
                    except Exception:
                        _restore_live_sqlite_generation(
                            rollback_database,
                            workspace_database,
                        )
                        raise

                    app.state.runtime_context = context
                    app.state.data = context.data
                    app.state.workspace_catalog = context.workspace_catalog
                    app.state.project_runtime_resolver = (
                        context.project_runtime_resolver
                    )
                    install_application_contribution_state(
                        app, context.contribution_runtimes
                    )
                    app.state.task_registry = context.task_registry
                    model_package_origins.clear()
                    model_package_origins.update(refreshed_origins)
                    app.state.model_store_warnings = refresh_warnings
                    app.state.resources_ready = True
                finally:
                    _remove_sqlite_generation(staged_database)
                    _remove_sqlite_generation(rollback_database)
                    app.state.resources_promoting = False
                    app.state.resource_promotion_complete.set()
                task_ids = set(context.task_registry.available_task_ids)
                model_package_ids = {
                    item.id
                    for item in context.workspace_catalog.list_model_package_refs()
                }
                return TaskResourceRefreshResult(
                    task_ids=sorted(task_ids),
                    added_task_ids=sorted(task_ids - previous_task_ids),
                    model_package_ids=sorted(model_package_ids),
                    added_model_package_ids=sorted(
                        model_package_ids - previous_model_package_ids
                    ),
                    warnings=[
                        present_resource_warning(warning)
                        for warning in refresh_warnings
                    ],
                )

        app.state.refresh_task_resources = refresh_task_resources
        promotion_task: asyncio.Task[None] | None = None
        if defer_resources:

            async def promote_remaining_resources() -> None:
                try:
                    complete = await asyncio.to_thread(
                        prepare_app_resources,
                        source_overrides=source_overrides,
                        package_roots=package_roots,
                        active_packages_path=active_packages_path,
                    )

                    def promote() -> RuntimeContext:
                        selected_starters = set(
                            installed_starter_project_ids(
                                app.state.store,
                                complete.modules,
                            )
                        )
                        selected_starters.add(QUICKSTART_PROJECT_ID)
                        initialize_demo_projects(
                            app.state.store,
                            complete.modules,
                            complete.runtimes,
                            complete.task_registry,
                            seed_candidates=False,
                            project_ids=selected_starters,
                        )
                        catalog = bootstrap_workspace_catalog(
                            database,
                            complete.task_registry,
                            available_packages_paths=(
                                configured_available_packages_paths
                            ),
                            personal_available_packages_paths=(
                                configured_personal_available_packages_paths
                            ),
                            package_origins=model_package_origins,
                            warnings=model_store_warnings,
                        )
                        resolver = ProjectRuntimeResolver(
                            catalog,
                            complete.task_registry,
                        )
                        promoted_contributions = rebuild_application_contributions(
                            contributions,
                            ApplicationContributionContext(
                                store=app.state.store,
                                workspace_catalog=catalog,
                                task_registry=complete.task_registry,
                                subsystem_availability=(
                                    app.state.subsystem_availability
                                ),
                            ),
                            app.state.runtime_context.contribution_runtimes,
                            promote_deferred=True,
                        )
                        return RuntimeContext(
                            data=complete.default_data,
                            task_registry=complete.task_registry,
                            workspace_catalog=catalog,
                            project_runtime_resolver=resolver,
                            contribution_runtimes=promoted_contributions,
                        )

                    app.state.resource_promotion_complete.clear()
                    app.state.resources_promoting = True
                    await app.state.resource_requests_idle.wait()
                    context = await asyncio.to_thread(promote)
                    # API dependencies read this one immutable generation.
                    app.state.runtime_context = context
                    # Mirrors remain for diagnostics and existing test helpers.
                    app.state.data = context.data
                    app.state.workspace_catalog = context.workspace_catalog
                    app.state.project_runtime_resolver = (
                        context.project_runtime_resolver
                    )
                    install_application_contribution_state(
                        app, context.contribution_runtimes
                    )
                    app.state.task_registry = context.task_registry
                    app.state.resources_ready = True
                except Exception as exc:
                    logger.exception("deferred resource preparation failed")
                    app.state.resources_loading_error = str(exc)
                finally:
                    app.state.resources_promoting = False
                    app.state.resource_promotion_complete.set()

            promotion_task = asyncio.create_task(promote_remaining_resources())
        try:
            yield
        finally:
            if promotion_task is not None and not promotion_task.done():
                if app.state.resources_promoting:
                    await asyncio.shield(promotion_task)
                else:
                    promotion_task.cancel()

    return lifespan
