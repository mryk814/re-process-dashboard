from __future__ import annotations

import os
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Mapping

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder

from .api.errors import DomainApiException, PROJECT_API_ERRORS, install_exception_handlers
from .api.security import configure_local_access
from .api.catalog import router as catalog_router
from .api.projects import router as projects_router
from .api.candidates import CANDIDATE_APPLICATION_ERRORS, candidate_http_error, router as candidates_router
from .api.data_exploration import router as data_exploration_router
from .api.screening import router as screening_router
from .api.inference import get_inference_service, router as inference_router
from .application.candidates import CandidateService
from .demo_seed import initialize_demo_projects
from .inference_work_graph import InferenceWorkGraph
from .runtime import ModelRuntime
from .model_lifecycle import ACTIVE_PACKAGES_PATH, load_active_packages, resolve_configured_package, validate_active_package_task_set
from .schemas import (
    ActualMeasurement,
    ActualMeasurementInput,
    Candidate,
    CandidateInput,
    DetailedPredictionResponse,
    PredictionVsActualResponse,
    Project,
    SnapshotResponse,
)
from .snapshot_reader import SnapshotPayloadError, candidate_input_from_snapshot
from .store import CandidateRevisionConflictError, Store
from .task_registry import DataExplorerEntry, TaskRegistry, TaskRegistryError
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

    def store() -> Store:
        return app.state.store

    def runtime() -> ModelRuntime:
        default_project = app.state.store.get_project("default")
        assert default_project is not None
        return app.state.task_registry.runtime_for(default_project.task_id)

    def task_registry() -> TaskRegistry:
        return app.state.task_registry

    def require_task_operation(task_id: str, operation: str) -> None:
        try:
            task_registry().require_operation(task_id, operation)  # type: ignore[arg-type]
        except TaskRegistryError as exc:
            raise HTTPException(422, str(exc)) from exc

    def inference_work_graph() -> InferenceWorkGraph:
        return app.state.inference_work_graph

    def require_project(project_id: str):
        project = store().get_project(project_id)
        if not project:
            raise HTTPException(404, "プロジェクトが見つかりません")
        return project

    def candidate_service() -> CandidateService:
        return CandidateService(store(), task_registry())

    def create_candidate_in_project(payload: CandidateInput, project_id: str):
        try:
            return candidate_service().create(project_id, payload)
        except CANDIDATE_APPLICATION_ERRORS as exc:
            converted = candidate_http_error(exc)
            if converted is exc:
                raise
            raise converted from exc

    @app.get("/api/bootstrap", deprecated=True)
    def bootstrap() -> dict[str, Any]:
        data = app.state.data
        candidates = [candidate.model_dump(mode="json") for candidate in store().list_candidates()]
        quality_category = data.technical_columns.get(("quality", "category"))
        return {
            "meta": {
                **data.source_summary,
                "project": require_project("default").model_dump(mode="json"),
                "model_targets": sorted(runtime().models),
            },
            "candidates": candidates,
            "summary": {
                "routes": Counter(row.get("standard_route") for row in data.anneal_features.values()),
                "quality_by_category": Counter(row.get(quality_category) for row in data.quality) if quality_category else {},
            },
        }

    def candidate_at_revision(project_id: str, candidate_id: str, expected_revision: int) -> Candidate:
        try:
            return candidate_service().at_revision(project_id, candidate_id, expected_revision)
        except CANDIDATE_APPLICATION_ERRORS as exc:
            converted = candidate_http_error(exc)
            if isinstance(exc, CandidateRevisionConflictError):
                converted = DomainApiException(
                    409,
                    "revision_conflict",
                    f"候補はrevision {exc.current.revision}へ更新されています",
                    current_candidate=exc.current,
                )
            if converted is exc:
                raise
            raise converted from exc

    def detailed_prediction_for(project: Project, candidate: Candidate) -> dict[str, Any]:
        return get_inference_service(store(), task_registry(), inference_work_graph()).detailed_for(project, candidate)

    @app.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/predict",
        response_model=DetailedPredictionResponse,
        responses=PROJECT_API_ERRORS,
        operation_id="createDetailedCandidatePrediction",
    )
    def predict(project_id: str, candidate_id: str, expected_revision: int) -> dict[str, Any]:
        candidate = candidate_at_revision(project_id, candidate_id, expected_revision)
        project = require_project(project_id)
        result = detailed_prediction_for(project, candidate)
        return {"prediction": result, "snapshot": snapshot_for_candidate(candidate, result)}

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}/snapshots", response_model=list[SnapshotResponse])
    def snapshots(project_id: str, candidate_id: str) -> list[dict[str, Any]]:
        require_task_operation(require_project(project_id).task_id, "snapshot")
        if not store().get_candidate(candidate_id, project_id, include_archived=True):
            raise HTTPException(404, "候補が見つかりません")
        return store().list_snapshots(candidate_id)

    @app.post("/api/projects/{project_id}/candidates/{candidate_id}/snapshots", status_code=201, response_model=SnapshotResponse)
    def create_snapshot(project_id: str, candidate_id: str) -> dict[str, Any]:
        require_task_operation(require_project(project_id).task_id, "snapshot")
        candidate = store().get_candidate(candidate_id, project_id)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        return snapshot_for_candidate(candidate)

    def snapshot_for_candidate(candidate: Any, result: dict[str, Any] | None = None) -> dict[str, Any]:
        project = store().get_project(candidate.project_id)
        if project is None:
            raise HTTPException(404, "プロジェクトが見つかりません")
        require_task_operation(project.task_id, "snapshot")
        if result is None:
            result = detailed_prediction_for(project, candidate)
        payload = {
            "snapshot_schema_version": "prediction-snapshot-v2",
            "candidate_id": candidate.id,
            "raw_candidate": candidate.model_dump(mode="json"),
            "canonical_input": result["canonical_input"],
            "prediction": result,
            "provenance": result["model_meta"],
        }
        # Stored JSON is a complete, immutable prediction artifact, not a mutable candidate reference.
        return store().create_snapshot(candidate.id, jsonable_encoder(payload))

    @app.post("/api/projects/{project_id}/snapshots/{snapshot_id}/restore", status_code=201, response_model=Candidate, responses=PROJECT_API_ERRORS)
    def restore_snapshot(project_id: str, snapshot_id: str) -> dict[str, Any]:
        require_task_operation(require_project(project_id).task_id, "snapshot")
        snapshot = store().get_snapshot(snapshot_id)
        if not snapshot or store().get_candidate(snapshot["candidate_id"], project_id, include_archived=True) is None:
            raise HTTPException(404, "スナップショットが見つかりません")
        try:
            payload = candidate_input_from_snapshot(snapshot_id, snapshot["payload"])
        except SnapshotPayloadError as exc:
            raise HTTPException(422, str(exc)) from exc
        return create_candidate_in_project(payload, project_id).model_dump(mode="json")

    @app.get(
        "/api/projects/{project_id}/snapshots/{snapshot_id}",
        response_model=SnapshotResponse,
        responses=PROJECT_API_ERRORS,
    )
    def get_snapshot(project_id: str, snapshot_id: str) -> dict[str, Any]:
        require_task_operation(require_project(project_id).task_id, "snapshot")
        snapshot = store().get_snapshot(snapshot_id)
        if not snapshot or store().get_candidate(
            snapshot["candidate_id"], project_id, include_archived=True
        ) is None:
            raise HTTPException(404, "スナップショットが見つかりません")
        return snapshot

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}/actuals", response_model=list[ActualMeasurement])
    def list_actuals(project_id: str, candidate_id: str) -> list[dict[str, Any]]:
        require_task_operation(require_project(project_id).task_id, "actual_measurement")
        if not store().get_candidate(candidate_id, project_id, include_archived=True):
            raise HTTPException(404, "候補が見つかりません")
        return [actual.model_dump(mode="json") for actual in store().list_actuals(candidate_id)]

    @app.post("/api/projects/{project_id}/candidates/{candidate_id}/actuals", status_code=201, response_model=ActualMeasurement, responses=PROJECT_API_ERRORS)
    def create_actual(project_id: str, candidate_id: str, payload: ActualMeasurementInput, expected_revision: int) -> dict[str, Any]:
        project = require_project(project_id)
        require_task_operation(project.task_id, "actual_measurement")
        candidate = candidate_at_revision(project_id, candidate_id, expected_revision)
        outputs = {output.key: output.unit for output in task_registry().contract_for(project.task_id).task_definition.outputs}
        if outputs.get(payload.property) != payload.unit:
            raise HTTPException(422, "実測の特性または単位が予測タスクと一致しません")
        snapshot = snapshot_for_candidate(candidate)
        return store().create_actual(candidate_id, snapshot["id"], payload).model_dump(mode="json")

    @app.delete("/api/projects/{project_id}/candidates/{candidate_id}/actuals/{actual_id}", status_code=204)
    def delete_actual(project_id: str, candidate_id: str, actual_id: str) -> Response:
        require_task_operation(require_project(project_id).task_id, "actual_measurement")
        if not store().get_candidate(candidate_id, project_id, include_archived=True):
            raise HTTPException(404, "候補が見つかりません")
        if actual_id not in {item.id for item in store().list_actuals(candidate_id)}:
            raise HTTPException(404, "実測が見つかりません")
        if not store().delete_actual(actual_id):
            raise HTTPException(404, "実測が見つかりません")
        return Response(status_code=204)

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}/prediction-vs-actual", response_model=PredictionVsActualResponse, responses=PROJECT_API_ERRORS)
    def prediction_vs_actual(project_id: str, candidate_id: str) -> dict[str, Any]:
        actuals = list_actuals(project_id, candidate_id)
        comparisons = []
        for actual in actuals:
            snapshot = store().get_snapshot(actual["snapshot_id"])
            if not snapshot:
                raise DomainApiException(409, "data_integrity_error", "実測に対応する予測スナップショットが見つかりません")
            payload = snapshot["payload"]
            comparisons.append({"actual": actual, "snapshot_id": snapshot["id"], "prediction": payload["prediction"], "provenance": payload["provenance"]})
        return {"candidate_id": candidate_id, "actuals": actuals, "comparisons": comparisons}

    return app


app = create_app()
