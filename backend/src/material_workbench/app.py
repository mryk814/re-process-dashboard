from __future__ import annotations

import asyncio
import os
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from material_workbench.contracts.blend_contracts import BlendContractRegistry
from .api.errors import PROJECT_API_ERRORS, install_exception_handlers
from .api.security import configure_local_access
from .api.catalog import router as catalog_router
from .api.chains import (
    execution_router as chain_execution_router,
    router as chains_router,
)
from .api.data_library import router as data_library_router
from .api.data_lifecycle import router as data_lifecycle_router
from .api.series_assets import router as series_assets_router
from .api.ai_reviews import router as ai_reviews_router
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
    DeterministicTransformCatalogUnavailableError,
    load_deterministic_transform_catalog,
)
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import DataExplorerEntry, TaskRegistry
from material_workbench.persistence.workspace_catalog_bootstrap import bootstrap_workspace_catalog
from material_workbench.application.ai_review_provider import AiReviewProvider
from material_workbench.persistence.welding_chain_bootstrap import (
    WeldingChainBootstrapError,
    bootstrap_welding_chain,
)
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
    ANNEALED_TASK_ID,
    PRIMARY_DEFAULT_SOURCE,
    PredictionRuntime,
    TaskModule,
    registered_task_modules,
)
from material_workbench.contracts.task_contracts import TaskAvailability
from material_workbench.contracts.subsystem_availability import (
    SubsystemAvailabilityRegistry,
    SubsystemKind,
    WELDING_CHAIN_EVALUATION_RESOURCE_ID,
    WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
    WELDING_CHAIN_RESOURCE_ID,
    WELDING_CHAIN_SUBSYSTEM_ID,
    WELDING_TRANSFORM_RESOURCE_ID,
    WELDING_TRANSFORM_SUBSYSTEM_ID,
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


def _task_unavailable(
    task_id: str,
    *,
    stage: str,
    label: str,
    resource_id: str,
    expected_locator: str | Path,
    recovery_hint: str,
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
    return TaskAvailability(
        status="unavailable",
        stage=stage,
        message=message,
        resource_id=resource_id,
        expected_locator=str(expected_locator),
        recovery_hint=recovery_hint,
    )


def _record_optional_failure(
    registry: SubsystemAvailabilityRegistry,
    *,
    subsystem_id: str,
    kind: SubsystemKind,
    resource_id: str,
    owner_kind: Literal["chain", "transform"] | None = None,
    owner_resource_id: str | None = None,
    stage: str,
    label: str,
    impact: str,
    recovery_hint: str,
    exc: Exception,
) -> None:
    logger.exception(
        "OPTIONAL_SUBSYSTEM_UNAVAILABLE subsystem_id=%s stage=%s "
        "error_type=%s detail=%s",
        subsystem_id,
        stage,
        type(exc).__name__,
        exc,
    )
    registry.record_unavailable(
        subsystem_id=subsystem_id,
        kind=kind,
        resource_id=resource_id,
        owner_kind=owner_kind,
        owner_resource_id=owner_resource_id,
        stage=stage,
        cause=f"{type(exc).__name__}: {str(exc).splitlines()[0]}",
        message=f"{label}を利用できません。",
        impact=impact,
        recovery_hint=recovery_hint,
    )


@dataclass(frozen=True)
class _AppResources:
    """Prepared source data and model runtimes treated as read-only by the app."""

    modules: Mapping[str, TaskModule]
    data_by_source: Mapping[str, Any]
    runtimes: Mapping[str, PredictionRuntime]
    task_registry: TaskRegistry


@dataclass(frozen=True)
class _RuntimeContext:
    data: Any
    task_registry: TaskRegistry
    workspace_catalog: Any
    project_runtime_resolver: ProjectRuntimeResolver
    chain_execution_service: ChainExecutionService
    chain_uncertainty_service: ChainUncertaintyService | None


def _prepare_app_resources(
    source_path: str | Path | None = None,
    *,
    flank_wear_source_path: str | Path | None = None,
    package_roots: Mapping[str, str | Path] | None = None,
    active_packages_path: str | Path | None = None,
    task_ids: frozenset[str] | None = None,
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
        if task_ids is not None and task_id not in task_ids:
            unavailable[task_id] = TaskAvailability(
                status="unavailable",
                stage="runtime",
                message="起動後にデータとModel Packageを準備しています。",
                resource_id=task_id,
                expected_locator=f"task:{task_id}",
                recovery_hint="準備完了後に自動で利用可能になります。",
            )
            continue
        configured_source = Path(module.default_source)
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
                task_id,
                stage="source",
                label="データソース",
                resource_id=module.source_kind,
                expected_locator=configured_source.resolve(),
                recovery_hint=(
                    f"{module.source_env}と対象データファイルを確認して再起動してください。"
                ),
                exc=exc,
            )
            continue
        package_override = injected.get(task_id) or os.getenv(
            module.package_override_env
        )
        configured_package = Path(package_override) if package_override else configured
        try:
            configured_package = resolve_configured_package(
                task_id,
                config_path=configured,
                override=package_override,
            )
            package = ModelPackageLoader().load(configured_package)
        except (OSError, ValueError, KeyError) as exc:
            unavailable[task_id] = _task_unavailable(
                task_id,
                stage="package",
                label="Model Package",
                resource_id=task_id,
                expected_locator=configured_package.resolve(),
                recovery_hint=(
                    f"{module.package_override_env}、active-packages.json、"
                    "対象Packageのmanifestとartifactを確認して再起動してください。"
                ),
                exc=exc,
            )
            continue
        try:
            runtimes[task_id] = module.runtime_factory(loaded, package)
        except (OSError, ValueError, KeyError) as exc:
            unavailable[task_id] = _task_unavailable(
                task_id,
                stage="runtime",
                label="予測runtime",
                resource_id=package.manifest.package_id,
                expected_locator=package.root,
                recovery_hint=(
                    "対象Packageのruntime種別、manifest、artifact、"
                    "追加依存を確認して再起動してください。"
                ),
                exc=exc,
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
    ai_review_provider: AiReviewProvider | None = None,
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
        defer_resources = (
            _resources is None
            and os.getenv("WORKBENCH_DEFER_RESOURCES", "").strip().lower()
            in {"1", "true", "yes"}
        )
        app.state.workspace_database = database.expanduser().resolve()
        app.state.workspace_kind = (
            os.getenv("WORKBENCH_WORKSPACE_KIND", "").strip()
            or (
                "main"
                if app.state.workspace_database
                == Path("data/workbench.db").resolve()
                else "custom"
            )
        )
        try:
            prepared = _resources or _prepare_app_resources(
                source_path,
                flank_wear_source_path=flank_wear_source_path,
                package_roots=package_roots,
                active_packages_path=active_packages_path,
                task_ids=(
                    frozenset({
                        os.getenv(
                            "WORKBENCH_STARTUP_TASK_ID",
                            ANNEALED_TASK_ID,
                        )
                    })
                    if defer_resources
                    else None
                ),
            )
        except Exception as exc:
            _raise_startup_error("resources", "データ・Model Package", exc)
        app.state.data = prepared.data_by_source.get("primary") or next(
            iter(prepared.data_by_source.values()), None
        )
        app.state.task_registry = prepared.task_registry
        app.state.blend_contract_registry = blend_contracts or BlendContractRegistry()
        app.state.ai_review_provider = ai_review_provider
        app.state.inference_work_graph = InferenceWorkGraph(max_entries=256)
        try:
            app.state.store = Store(database)
            explicit_demo_seed = os.getenv("WORKBENCH_DEMO_SEED", "").strip().lower() in {"1", "true", "yes"}
            initialize_demo_projects(
                app.state.store,
                prepared.modules,
                prepared.runtimes,
                prepared.task_registry,
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
        app.state.subsystem_availability = SubsystemAvailabilityRegistry()
        transform_catalog = None
        try:
            transform_catalog = load_deterministic_transform_catalog(
                active_transforms_path
            )
            app.state.subsystem_availability.record_available(
                subsystem_id=WELDING_TRANSFORM_SUBSYSTEM_ID,
                kind="deterministic_transform",
                resource_id=WELDING_TRANSFORM_RESOURCE_ID,
                owner_kind="transform",
                owner_resource_id=WELDING_TRANSFORM_RESOURCE_ID,
                stage="deterministic_transforms",
            )
        except DeterministicTransformCatalogUnavailableError as exc:
            _record_optional_failure(
                app.state.subsystem_availability,
                subsystem_id=WELDING_TRANSFORM_SUBSYSTEM_ID,
                kind="deterministic_transform",
                resource_id=WELDING_TRANSFORM_RESOURCE_ID,
                owner_kind="transform",
                owner_resource_id=WELDING_TRANSFORM_RESOURCE_ID,
                stage="deterministic_transforms",
                label="溶接材料の決定論的Transform",
                impact="このTransformを使う配合編集とChain実行を停止します。ほかの予測Taskと保存済み証跡は利用できます。",
                recovery_hint="active-transforms.jsonと対象Packageのmanifest・artifact digestを確認して再起動してください。",
                exc=exc,
            )
        app.state.deterministic_transform_catalog = transform_catalog
        chain_revision_id = None
        if transform_catalog is None:
            dependency = app.state.subsystem_availability.get(
                WELDING_TRANSFORM_SUBSYSTEM_ID
            )
            app.state.subsystem_availability.record_unavailable(
                subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                kind="chain",
                resource_id=WELDING_CHAIN_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_catalog",
                cause=f"dependency_unavailable: {WELDING_TRANSFORM_SUBSYSTEM_ID}",
                message="溶接材料Chainを利用できません。",
                impact="このChainの候補編集と実行を停止します。保存済みProject・Run・SnapshotはProject概要から参照できます。",
                recovery_hint=(
                    dependency.recovery_hint
                    if dependency is not None
                    else "依存する決定論的Transformを復旧して再起動してください。"
                ),
            )
        elif defer_resources:
            app.state.subsystem_availability.record_unavailable(
                subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                kind="chain",
                resource_id=WELDING_CHAIN_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_catalog",
                cause="resources_loading",
                message="起動後にChainのTask resourceを準備しています。",
                impact="準備中はChain候補の編集と実行を待機します。",
                recovery_hint="準備完了後に自動で利用可能になります。",
            )
        else:
            try:
                chain_revision_id = bootstrap_welding_chain(
                    store=app.state.store,
                    workspace_catalog=app.state.workspace_catalog,
                    task_registry=prepared.task_registry,
                    transform_catalog=transform_catalog,
                )
                app.state.subsystem_availability.record_available(
                    subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                    kind="chain",
                    resource_id=WELDING_CHAIN_RESOURCE_ID,
                    owner_kind="chain",
                    owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                    stage="chain_catalog",
                )
            except WeldingChainBootstrapError as exc:
                _record_optional_failure(
                    app.state.subsystem_availability,
                    subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                    kind="chain",
                    resource_id=WELDING_CHAIN_RESOURCE_ID,
                    owner_kind="chain",
                    owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                    stage="chain_catalog",
                    label="溶接材料Chain",
                    impact="このChainの候補編集と実行を停止します。保存済みProject・Run・SnapshotはProject概要から参照できます。",
                    recovery_hint="Chain Definition、binding、固定Dataset／Package参照を確認して再起動してください。",
                    exc=exc,
                )
        app.state.welding_chain_revision_id = chain_revision_id
        evaluation_catalog = None
        try:
            configured_evaluation = (
                chain_evaluation_path
                or os.getenv("WORKBENCH_CHAIN_EVALUATION_PATH")
                or DEFAULT_CHAIN_EVALUATION_PATH
            )
            evaluation_catalog = ChainEvaluationCatalog.load(configured_evaluation)
            app.state.subsystem_availability.record_available(
                subsystem_id=WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
                kind="chain_evaluation",
                resource_id=WELDING_CHAIN_EVALUATION_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_evaluation",
            )
        except (OSError, ValueError) as exc:
            _record_optional_failure(
                app.state.subsystem_availability,
                subsystem_id=WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
                kind="chain_evaluation",
                resource_id=WELDING_CHAIN_EVALUATION_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_evaluation",
                label="溶接材料Chainの評価成果物",
                impact="段単体／通し評価だけを停止します。Chain候補、実行、保存済み証跡とほかのTaskは利用できます。",
                recovery_hint="評価JSONのschema、digest、Chain／Stage identityを確認して再起動してください。",
                exc=exc,
            )
        app.state.chain_evaluation_catalog = evaluation_catalog
        app.state.chain_execution_service = ChainExecutionService(
            app.state.store,
            prepared.task_registry,
            transform_catalog,
            ChainExecutionCoordinator(),
        )
        if transform_catalog is not None:
            app.state.chain_uncertainty_service = ChainUncertaintyService(
                app.state.store,
                app.state.chain_execution_service,
            )
        else:
            app.state.chain_uncertainty_service = None
        app.state.runtime_context = _RuntimeContext(
            data=app.state.data,
            task_registry=app.state.task_registry,
            workspace_catalog=app.state.workspace_catalog,
            project_runtime_resolver=app.state.project_runtime_resolver,
            chain_execution_service=app.state.chain_execution_service,
            chain_uncertainty_service=app.state.chain_uncertainty_service,
        )
        app.state.resources_ready = not defer_resources
        app.state.resources_promoting = False
        app.state.resources_loading_error = None
        app.state.active_resource_requests = 0
        app.state.resource_requests_idle = asyncio.Event()
        app.state.resource_requests_idle.set()
        promotion_task: asyncio.Task[None] | None = None
        if defer_resources:
            async def promote_remaining_resources() -> None:
                try:
                    complete = await asyncio.to_thread(
                        _prepare_app_resources,
                        source_path,
                        flank_wear_source_path=flank_wear_source_path,
                        package_roots=package_roots,
                        active_packages_path=active_packages_path,
                    )

                    def promote() -> tuple[
                        _RuntimeContext,
                        str | None,
                        WeldingChainBootstrapError | None,
                    ]:
                        initialize_demo_projects(
                            app.state.store,
                            complete.modules,
                            complete.runtimes,
                            complete.task_registry,
                            seed_candidates=False,
                        )
                        catalog = bootstrap_workspace_catalog(
                            database,
                            complete.task_registry,
                        )
                        resolver = ProjectRuntimeResolver(
                            catalog,
                            complete.task_registry,
                        )
                        transform_catalog = app.state.deterministic_transform_catalog
                        chain_revision_id = app.state.welding_chain_revision_id
                        chain_error = None
                        if transform_catalog is not None:
                            try:
                                chain_revision_id = bootstrap_welding_chain(
                                    store=app.state.store,
                                    workspace_catalog=catalog,
                                    task_registry=complete.task_registry,
                                    transform_catalog=transform_catalog,
                                )
                            except WeldingChainBootstrapError as exc:
                                chain_error = exc
                        chain_execution = ChainExecutionService(
                            app.state.store,
                            complete.task_registry,
                            transform_catalog,
                            ChainExecutionCoordinator(),
                        )
                        chain_uncertainty = (
                            ChainUncertaintyService(
                                app.state.store,
                                chain_execution,
                            )
                            if transform_catalog is not None
                            else None
                        )
                        data = (
                            complete.data_by_source.get("primary")
                            or next(iter(complete.data_by_source.values()), None)
                        )
                        return (
                            _RuntimeContext(
                                data=data,
                                task_registry=complete.task_registry,
                                workspace_catalog=catalog,
                                project_runtime_resolver=resolver,
                                chain_execution_service=chain_execution,
                                chain_uncertainty_service=chain_uncertainty,
                            ),
                            chain_revision_id,
                            chain_error,
                        )

                    app.state.resources_promoting = True
                    await app.state.resource_requests_idle.wait()
                    context, chain_revision_id, chain_error = await asyncio.to_thread(
                        promote
                    )
                    # API dependencies read this one immutable generation.
                    app.state.runtime_context = context
                    # Mirrors remain for diagnostics and existing test helpers.
                    app.state.data = context.data
                    app.state.workspace_catalog = context.workspace_catalog
                    app.state.project_runtime_resolver = (
                        context.project_runtime_resolver
                    )
                    app.state.chain_execution_service = (
                        context.chain_execution_service
                    )
                    app.state.chain_uncertainty_service = (
                        context.chain_uncertainty_service
                    )
                    app.state.task_registry = context.task_registry
                    if chain_error is None:
                        app.state.subsystem_availability.record_available(
                            subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                            kind="chain",
                            resource_id=WELDING_CHAIN_RESOURCE_ID,
                            owner_kind="chain",
                            owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                            stage="chain_catalog",
                        )
                    else:
                        _record_optional_failure(
                            app.state.subsystem_availability,
                            subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                            kind="chain",
                            resource_id=WELDING_CHAIN_RESOURCE_ID,
                            owner_kind="chain",
                            owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                            stage="chain_catalog",
                            label="溶接材料Chain",
                            impact="このChainの候補編集と実行を停止します。保存済みProject・Run・SnapshotはProject概要から参照できます。",
                            recovery_hint="Chain Definition、binding、固定Dataset／Package参照を確認して再起動してください。",
                            exc=chain_error,
                        )
                    app.state.welding_chain_revision_id = chain_revision_id
                    app.state.resources_ready = True
                except Exception as exc:
                    logger.exception("deferred resource preparation failed")
                    app.state.resources_loading_error = str(exc)
                finally:
                    app.state.resources_promoting = False

            promotion_task = asyncio.create_task(promote_remaining_resources())
        try:
            yield
        finally:
            if promotion_task is not None and not promotion_task.done():
                if app.state.resources_promoting:
                    await asyncio.shield(promotion_task)
                else:
                    promotion_task.cancel()

    app = FastAPI(
        title="Material Decision Workbench API",
        version="0.1.0",
        lifespan=lifespan,
        responses={422: PROJECT_API_ERRORS[422]},
    )

    @app.middleware("http")
    async def gate_resource_promotion(request: Request, call_next):
        health_paths = {"/health", "/api/health", "/api/readiness"}
        is_health_request = request.url.path in health_paths
        if (
            getattr(request.app.state, "resources_promoting", False)
            and not is_health_request
        ):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": (
                        "追加TaskをWorkspaceへ安全に登録しています。"
                        "完了後に自動で再試行してください。"
                    )
                },
            )
        if is_health_request:
            return await call_next(request)

        request.app.state.active_resource_requests += 1
        request.app.state.resource_requests_idle.clear()
        try:
            return await call_next(request)
        finally:
            request.app.state.active_resource_requests -= 1
            if request.app.state.active_resource_requests == 0:
                request.app.state.resource_requests_idle.set()

    configure_local_access(app)
    install_exception_handlers(app)
    app.include_router(catalog_router)
    app.include_router(chains_router)
    app.include_router(chain_execution_router)
    app.include_router(data_library_router)
    app.include_router(data_lifecycle_router)
    app.include_router(series_assets_router)
    app.include_router(ai_reviews_router)
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
