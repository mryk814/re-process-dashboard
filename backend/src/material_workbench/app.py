from __future__ import annotations

import os
import csv
import importlib.util
from collections import Counter
from contextlib import asynccontextmanager
from io import BytesIO, StringIO
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Mapping

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .candidate_migration import HOT_PROJECT_ID
from .importer import lineage_neighborhood, lineage_node_detail, load_workbook_data
from .hot_rolling import TASK_ID as HOT_ROLLING_TASK_ID, HotRollingRuntime
from .runtime import ModelRuntime, TASK_ID as ANNEALED_TASK_ID
from .model_lifecycle import ACTIVE_PACKAGES_PATH, resolve_configured_package, validate_lifecycle_metadata
from .schemas import (
    ApiError,
    ActualMeasurement,
    ActualMeasurementInput,
    Candidate,
    CandidateImportResponse,
    CandidateInput,
    CandidateUpdate,
    DetailedPredictionResponse,
    LineageIndexResponse,
    LineageResponse,
    ModelPackageStatus,
    PredictionResponse,
    PredictionVsActualResponse,
    Project,
    ProjectDecisionInput,
    ProjectInput,
    QualityResponse,
    ResponseCurvesResponse,
    ResponseCurvesResult,
    ScreeningRequest,
    ScreeningRunResponse,
    SimilarObservation,
    SnapshotResponse,
)
from .services import candidate_from_lineage, candidates_xlsx, import_candidates_xlsx, run_latin_hypercube
from .snapshot_reader import SnapshotPayloadError, candidate_input_from_snapshot
from .store import CandidateArchivedError, CandidateLimitError, CandidateRevisionConflictError, InvalidProjectDecisionError, ProjectNotFoundError, Store, StoreDataIntegrityError
from .task_contracts import ResolvedTaskDefinition
from .task_registry import TaskRegistry, TaskRegistryError


PROJECT_API_ERRORS = {
    404: {"model": ApiError},
    409: {"model": ApiError},
    422: {"model": ApiError},
}


class DomainApiException(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, current_candidate: Candidate | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.current_candidate = current_candidate


def _default_candidate_payloads(medians: dict[str, float]) -> list[CandidateInput]:
    composition = {key: round(value, 5) for key, value in medians.items()}
    variants = [
        ("基準候補", 1.00, 810.0, 103.0),
        ("高強度案", 1.16, 830.0, 96.0),
        ("延性重視案", 0.88, 790.0, 112.0),
    ]
    return [
        CandidateInput(
            name=name,
            inputs={
                "composition": {**composition, "C": round(composition["C"] * carbon_factor, 5)},
                "process": {"thickness_mm": 1.4, "line_speed_m_min": line_speed},
                "categorical": {"coating": "GI"},
                "heat_pattern": [
                    {"time_s": 0, "temperature_c": 25},
                    {"time_s": 280, "temperature_c": peak - 10},
                    {"time_s": 340, "temperature_c": peak},
                    {"time_s": 650, "temperature_c": 120},
                ],
            },
        )
        for name, carbon_factor, peak, line_speed in variants
    ]


def _default_hot_rolling_candidates(medians: dict[str, float]) -> list[CandidateInput]:
    composition = {key: round(value, 5) for key, value in medians.items()}
    variants = [
        ("基準熱延", 1170, 30, 900, 620, 35, 34, 3.4, "A"),
        ("低温巻取", 1170, 30, 890, 560, 42, 34, 3.2, "B"),
        ("延性重視", 1160, 34, 920, 660, 28, 34, 3.8, "C"),
    ]
    keys = (
        "reheat_temperature_c", "hold_time_min", "finish_temperature_c",
        "coiling_temperature_c", "cooling_rate_c_s", "entry_thickness_mm",
        "exit_thickness_mm",
    )
    return [CandidateInput(
        name=name,
        inputs={
            "composition": composition,
            "process": dict(zip(keys, values)),
            "categorical": {"route": route},
            "heat_pattern": None,
        },
    ) for name, *values, route in variants]


def create_app(
    source_path: str | Path | None = None,
    db_path: str | Path | None = None,
    *,
    package_roots: Mapping[str, str | Path] | None = None,
    active_packages_path: str | Path | None = None,
) -> FastAPI:
    source = Path(source_path or os.getenv("WORKBENCH_SOURCE_PATH", "data/source/process_dashboard_realistic_excel_v2.xlsx"))
    database = Path(db_path or os.getenv("WORKBENCH_DB_PATH", "data/workbench.db"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.data = load_workbook_data(source)
        configured = Path(active_packages_path) if active_packages_path else ACTIVE_PACKAGES_PATH
        injected = dict(package_roots or {})
        annealed_runtime = ModelRuntime(
            app.state.data,
            package_root=resolve_configured_package(
                ANNEALED_TASK_ID,
                config_path=configured,
                override=injected.get(ANNEALED_TASK_ID) or os.getenv("MATERIAL_WORKBENCH_MODEL_PACKAGE"),
            ),
        )
        hot_rolling_runtime = HotRollingRuntime(
            app.state.data,
            package_root=resolve_configured_package(
                HOT_ROLLING_TASK_ID,
                config_path=configured,
                override=injected.get(HOT_ROLLING_TASK_ID) or os.getenv("MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE"),
            ),
        )
        app.state.task_registry = TaskRegistry({
            ANNEALED_TASK_ID: annealed_runtime,
            HOT_ROLLING_TASK_ID: hot_rolling_runtime,
        })
        app.state.store = Store(database)
        app.state.store.ensure_project(
            HOT_PROJECT_ID,
            ProjectInput(name="熱延条件の候補検討", task_id=HOT_ROLLING_TASK_ID),
        )
        if not app.state.store.list_candidates():
            for candidate in _default_candidate_payloads(app.state.data.medians):
                app.state.store.create_candidate(candidate)
        if not app.state.store.list_candidates(HOT_PROJECT_ID):
            for candidate in _default_hot_rolling_candidates(app.state.data.medians):
                app.state.store.create_candidate(candidate, HOT_PROJECT_ID)
        yield

    app = FastAPI(title="Material Decision Workbench API", version="0.1.0", lifespan=lifespan)
    # Electron loads the packaged renderer from file://, which browsers serialize as Origin: null.
    # The local sidecar only listens on loopback, and credentials are not accepted.
    app.add_middleware(CORSMiddleware, allow_origins=["null", "http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:5180", "http://localhost:5180"], allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = {
            404: "not_found",
            422: "validation_error",
            503: "runtime_unavailable",
        }.get(exc.status_code, "validation_error")
        message = exc.detail if isinstance(exc.detail, str) else "リクエストを処理できません"
        payload = ApiError(code=code, message=message)
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json", exclude={"current_candidate"}))

    @app.exception_handler(DomainApiException)
    async def domain_error(_: Request, exc: DomainApiException) -> JSONResponse:
        payload = ApiError(
            code=exc.code,  # type: ignore[arg-type]
            message=exc.message,
            current_candidate=exc.current_candidate,
        )
        excluded = {"current_candidate"} if exc.current_candidate is None else None
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json", exclude=excluded))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        payload = ApiError(
            code="validation_error",
            message="入力内容を確認してください",
            field_errors=[
                {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                for error in exc.errors()
            ],
        )
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json", exclude={"current_candidate"}))

    def store() -> Store:
        return app.state.store

    def runtime() -> ModelRuntime:
        return app.state.task_registry.runtime_for(ANNEALED_TASK_ID)

    def task_registry() -> TaskRegistry:
        return app.state.task_registry

    def require_project(project_id: str):
        project = store().get_project(project_id)
        if not project:
            raise HTTPException(404, "プロジェクトが見つかりません")
        return project

    def create_candidate_in_project(payload: CandidateInput, project_id: str):
        project = require_project(project_id)
        try:
            task_registry().validate_candidate(project.task_id, payload)
        except (TaskRegistryError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            return store().create_candidate(payload, project_id)
        except ProjectNotFoundError as exc:
            raise HTTPException(404, "プロジェクトが見つかりません") from exc
        except CandidateLimitError as exc:
            raise DomainApiException(409, "candidate_limit", str(exc)) from exc

    @app.get("/api/health")
    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, Any]:
        return {"ok": True, "models": sorted(runtime().models), "source": str(source)}

    @app.get(
        "/api/projects/{project_id}/model-package",
        response_model=ModelPackageStatus,
        responses=PROJECT_API_ERRORS,
        operation_id="getProjectModelPackage",
    )
    def model_package(project_id: str) -> dict[str, Any]:
        project = require_project(project_id)
        entry = task_registry().entry_for(project.task_id)
        package = entry.model_package
        manifest = package.manifest
        quality = validate_lifecycle_metadata(package, task_registry().contract_for(project.task_id))
        dependencies = {
            "builtin.linear.v1": True,
            "builtin.exact_gp.v1": True,
            "sklearn.skops.v1": importlib.util.find_spec("skops") is not None,
            "lightgbm.booster.v1": importlib.util.find_spec("lightgbm") is not None,
            "gpytorch.static_exact_rbf.v1": importlib.util.find_spec("torch") is not None and importlib.util.find_spec("safetensors") is not None,
            "numpyro.dense_posterior.v1": True,
        }
        return {
            "id": manifest.package_id,
            "version": manifest.package_version,
            "task_id": manifest.task_id,
            "manifest_sha256": package.manifest_sha256,
            "active_runtimes": sorted({item.runtime_type for item in manifest.predictors}),
            "supported_runtimes": [
                {"runtime_type": runtime_type, "available": available}
                for runtime_type, available in dependencies.items()
            ],
            "predictors": [
                {"target": item.target, "runtime_type": item.runtime_type, "predictive_family": item.predictive_family}
                for item in manifest.predictors
            ],
            "quality_report": quality.model_dump(mode="json"),
        }

    @app.get(
        "/api/projects/{project_id}/task-definition",
        response_model=ResolvedTaskDefinition,
        responses=PROJECT_API_ERRORS,
        operation_id="getProjectTaskDefinition",
    )
    def task_definition(project_id: str) -> ResolvedTaskDefinition:
        project = require_project(project_id)
        return task_registry().resolved_definition_for(project.task_id)

    @app.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/preview",
        response_model=PredictionResponse,
        responses=PROJECT_API_ERRORS,
        operation_id="previewProjectCandidate",
    )
    def preview_project_candidate(project_id: str, candidate_id: str) -> dict[str, Any]:
        project = require_project(project_id)
        candidate = store().get_candidate(candidate_id, project_id)
        if candidate is None:
            raise HTTPException(404, "候補が見つかりません")
        return task_registry().runtime_for(project.task_id).predict(
            candidate,
            detailed=False,
            include_curve=False,
            target_values=project.target_values,
        )

    @app.get("/api/bootstrap")
    def bootstrap() -> dict[str, Any]:
        data = app.state.data
        candidates = [candidate.model_dump(mode="json") for candidate in store().list_candidates()]
        quality_category = data.technical_columns[("quality", "category")]
        return {
            "meta": {
                **data.source_summary,
                "project": require_project("default").model_dump(mode="json"),
                "model_targets": sorted(runtime().models),
            },
            "candidates": candidates,
            "summary": {
                "routes": Counter(row.get("standard_route") for row in data.anneal_features.values()),
                "quality_by_category": Counter(row.get(quality_category) for row in data.quality),
            },
        }

    @app.get("/api/projects", response_model=list[Project])
    def list_projects() -> list[dict[str, Any]]:
        return [project.model_dump(mode="json") for project in store().list_projects()]

    @app.post("/api/projects", status_code=201, response_model=Project)
    def create_project(payload: ProjectInput) -> dict[str, Any]:
        try:
            contract = task_registry().contract_for(payload.task_id)
        except TaskRegistryError as exc:
            raise HTTPException(422, str(exc)) from exc
        unsupported_targets = sorted(set(payload.target_values) - {item.key for item in contract.task_definition.outputs})
        if unsupported_targets:
            raise HTTPException(422, f"タスクに存在しない目標特性です: {', '.join(unsupported_targets)}")
        if payload.decision_candidate_id:
            raise HTTPException(422, "新しいプロジェクトでは採用候補を空にしてください")
        return store().create_project(payload).model_dump(mode="json")

    @app.get("/api/projects/{project_id}", response_model=Project)
    def get_project_by_id(project_id: str) -> dict[str, Any]:
        return require_project(project_id).model_dump(mode="json")

    @app.put("/api/projects/{project_id}", response_model=Project, responses=PROJECT_API_ERRORS)
    def update_project_by_id(project_id: str, payload: ProjectInput) -> dict[str, Any]:
        current = require_project(project_id)
        try:
            contract = task_registry().contract_for(payload.task_id)
        except TaskRegistryError as exc:
            raise HTTPException(422, str(exc)) from exc
        unsupported_targets = sorted(set(payload.target_values) - {item.key for item in contract.task_definition.outputs})
        if unsupported_targets:
            raise HTTPException(422, f"タスクに存在しない目標特性です: {', '.join(unsupported_targets)}")
        if current.task_id != payload.task_id and store().list_candidates(project_id, include_archived=True):
            raise DomainApiException(409, "project_task_locked", "候補があるプロジェクトの予測タスクは変更できません")
        try:
            project = store().update_project(project_id, payload)
        except InvalidProjectDecisionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not project:
            raise HTTPException(404, "プロジェクトが見つかりません")
        return project.model_dump(mode="json")

    @app.put("/api/projects/{project_id}/decision", response_model=Project)
    def update_project_decision(project_id: str, payload: ProjectDecisionInput) -> dict[str, Any]:
        try:
            project = store().update_project_decision(
                project_id,
                payload.candidate_id,
                payload.snapshot_id,
                payload.note,
            )
        except InvalidProjectDecisionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not project:
            raise HTTPException(404, "プロジェクトが見つかりません")
        return project.model_dump(mode="json")

    @app.get("/api/project", response_model=Project)
    def get_project() -> dict[str, Any]:
        return require_project("default").model_dump(mode="json")

    @app.put("/api/project", response_model=Project, responses=PROJECT_API_ERRORS)
    def update_project(payload: ProjectInput) -> dict[str, Any]:
        return update_project_by_id("default", payload)

    @app.get("/api/projects/{project_id}/candidates", response_model=list[Candidate], responses=PROJECT_API_ERRORS)
    def list_candidates(project_id: str, include_archived: bool = False) -> list[dict[str, Any]]:
        require_project(project_id)
        return [candidate.model_dump(mode="json") for candidate in store().list_candidates(project_id, include_archived=include_archived)]

    @app.post("/api/projects/{project_id}/candidates", status_code=201, response_model=Candidate, responses=PROJECT_API_ERRORS)
    def create_candidate(project_id: str, payload: CandidateInput) -> dict[str, Any]:
        return create_candidate_in_project(payload, project_id).model_dump(mode="json")

    @app.post("/api/projects/{project_id}/candidates/import", response_model=CandidateImportResponse, responses=PROJECT_API_ERRORS)
    async def import_candidates(project_id: str, file: UploadFile = File(...)) -> dict[str, Any]:
        project = require_project(project_id)
        if project.task_id != ANNEALED_TASK_ID:
            raise HTTPException(422, "Excel候補importはこの予測タスクでは利用できません")
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(422, "Excel .xlsx ファイルを選択してください")
        payloads, errors = import_candidates_xlsx(await file.read())
        try:
            created = [candidate.model_dump(mode="json") for candidate in store().create_candidates(payloads, project_id)]
        except ProjectNotFoundError as exc:
            raise HTTPException(404, "プロジェクトが見つかりません") from exc
        except CandidateLimitError as exc:
            raise DomainApiException(409, "candidate_limit", str(exc)) from exc
        return {"created": len(created), "errors": errors, "candidates": created}

    @app.get("/api/projects/{project_id}/candidates/export.xlsx")
    def export_candidates(project_id: str) -> StreamingResponse:
        project = require_project(project_id)
        if project.task_id != ANNEALED_TASK_ID:
            raise HTTPException(422, "Excel候補exportはこの予測タスクでは利用できません")
        contents = candidates_xlsx(store().list_candidates(project_id), task_registry().runtime_for(project.task_id))
        return StreamingResponse(BytesIO(contents), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=candidates-with-predictions.xlsx"})

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}", response_model=Candidate, responses=PROJECT_API_ERRORS)
    def get_candidate(project_id: str, candidate_id: str, include_archived: bool = False) -> dict[str, Any]:
        require_project(project_id)
        candidate = store().get_candidate(candidate_id, project_id, include_archived=include_archived)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        return candidate.model_dump(mode="json")

    @app.put("/api/projects/{project_id}/candidates/{candidate_id}", response_model=Candidate, responses=PROJECT_API_ERRORS)
    def update_candidate(project_id: str, candidate_id: str, payload: CandidateUpdate) -> dict[str, Any]:
        project = require_project(project_id)
        existing = store().get_candidate(candidate_id, project_id, include_archived=True)
        if existing is None:
            raise HTTPException(404, "候補が見つかりません")
        if existing.archived_at is not None:
            raise DomainApiException(409, "candidate_archived", "archive済み候補は編集できません")
        candidate_input = CandidateInput.model_validate(payload.model_dump(exclude={"expected_revision"}))
        try:
            task_registry().validate_candidate(project.task_id, candidate_input)
        except (TaskRegistryError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            candidate = store().update_candidate(candidate_id, project_id, candidate_input, payload.expected_revision)
        except CandidateRevisionConflictError as exc:
            raise DomainApiException(409, "revision_conflict", str(exc), current_candidate=exc.current) from exc
        except CandidateArchivedError as exc:
            raise DomainApiException(409, "candidate_archived", str(exc)) from exc
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        return candidate.model_dump(mode="json")

    @app.delete("/api/projects/{project_id}/candidates/{candidate_id}", status_code=204, responses=PROJECT_API_ERRORS)
    def delete_candidate(project_id: str, candidate_id: str, expected_revision: int) -> Response:
        require_project(project_id)
        if store().get_candidate(candidate_id, project_id, include_archived=True) is None:
            raise HTTPException(404, "候補が見つかりません")
        try:
            deleted = store().delete_candidate(candidate_id, project_id, expected_revision)
        except CandidateRevisionConflictError as exc:
            raise DomainApiException(409, "revision_conflict", str(exc), current_candidate=exc.current) from exc
        except CandidateArchivedError as exc:
            raise DomainApiException(409, "candidate_archived", str(exc)) from exc
        except StoreDataIntegrityError as exc:
            raise DomainApiException(409, "data_integrity_error", str(exc)) from exc
        if not deleted:
            raise HTTPException(404, "候補が見つかりません")
        return Response(status_code=204)

    def prediction(project_id: str, candidate_id: str, detailed: bool, include_curve: bool = False) -> dict[str, Any]:
        candidate = store().get_candidate(candidate_id, project_id)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        project = store().get_project(candidate.project_id)
        if project is None:
            raise HTTPException(404, "プロジェクトが見つかりません")
        task_runtime = task_registry().runtime_for(project.task_id)
        return task_runtime.predict(candidate, detailed=detailed, include_curve=include_curve, target_values=project.target_values)

    @app.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/predict",
        response_model=DetailedPredictionResponse,
        responses=PROJECT_API_ERRORS,
        operation_id="createDetailedCandidatePrediction",
    )
    def predict(project_id: str, candidate_id: str) -> dict[str, Any]:
        candidate = store().get_candidate(candidate_id, project_id)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        result = prediction(project_id, candidate_id, detailed=True, include_curve=True)
        return {"prediction": result, "snapshot": snapshot_for_candidate(candidate, result)}

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}/response-curve")
    def response_curve(project_id: str, candidate_id: str, target: str = "TS", variable: str = "heat.peak_temperature_c") -> list[dict[str, float]]:
        if target not in {"TS", "YS", "EL", "lambda"}:
            raise HTTPException(422, "未対応の予測特性です")
        project = require_project(project_id)
        candidate = store().get_candidate(candidate_id, project_id)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        try:
            task_runtime = task_registry().runtime_for(project.task_id)
            if not hasattr(task_runtime, "response_curve"):
                raise ValueError("この予測タスクは応答曲線に対応していません")
            return task_runtime.response_curve(candidate, target, variable)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}/response-curves", response_model=ResponseCurvesResult)
    def response_curves(project_id: str, candidate_id: str, variable: str | None = None) -> dict[str, Any]:
        project = require_project(project_id)
        candidate = store().get_candidate(candidate_id, project_id)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        try:
            task_runtime = task_registry().runtime_for(project.task_id)
            if not hasattr(task_runtime, "response_curves"):
                raise ValueError("この予測タスクは応答曲線に対応していません")
            return task_runtime.response_curves(candidate, variable)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}/similar", response_model=list[SimilarObservation])
    def similar(project_id: str, candidate_id: str) -> list[dict[str, object]]:
        return prediction(project_id, candidate_id, detailed=False)["similar"]

    @app.get("/api/quality", response_model=QualityResponse)
    def quality() -> dict[str, Any]:
        scenarios = app.state.data.quality
        detected = app.state.data.detected_quality
        # Keep the original top-level fields for the current renderer.  The
        # detected fields are actual structural checks, not workbook fixtures.
        return {
            "total": len(scenarios),
            "by_category": Counter(
                row[app.state.data.technical_columns[("quality", "category")]] for row in scenarios
            ),
            "issues": scenarios,
            "reference_scenarios": scenarios,
            "detected_total": len(detected),
            "detected_by_type": Counter(row["issue_type"] for row in detected),
            "detected_issues": detected,
        }

    @app.get("/api/quality/export.csv")
    def export_quality() -> StreamingResponse:
        output = StringIO()
        issues = app.state.data.detected_quality
        writer = csv.DictWriter(output, fieldnames=["issue_id", "issue_type", "source_sheet", "entity_key", "detail"])
        writer.writeheader()
        writer.writerows(issues)
        return StreamingResponse(iter(["\ufeff" + output.getvalue()]), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=detected-data-quality.csv"})

    @app.get("/api/lineage", response_model=LineageIndexResponse, response_model_exclude_none=True)
    def lineage_index(query: str = "", entity_type: str = "", issue_only: bool = False, limit: int = 40) -> dict[str, Any]:
        data = app.state.data
        normalized = query.strip().casefold()
        issue_keys = {issue["entity_key"] for issue in data.detected_quality if issue["entity_key"]}
        items: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        for sheet_name, key_column in app.state.data.entity_sheets.items():
            records = data.entities[key_column]
            counts[sheet_name] += len(records)
            if entity_type and sheet_name != entity_type:
                continue
            for key, source_row in records.items():
                metadata: dict[str, Any] = {}
                if sheet_name == data.role_to_sheet["annealing"]:
                    relations = data.lineage.get(key, {})
                    melt_key_column = data.role_to_key["melt"]
                    melt_keys = sorted(set(relations.get(melt_key_column, [])))
                    melt_row = data.entities.get(melt_key_column, {}).get(melt_keys[0], {}) if len(melt_keys) == 1 else {}
                    feature = data.anneal_features.get(key, {})
                    values_by_property: dict[str, list[float]] = {}
                    for observation in data.observations:
                        if observation["parent_key"] != key or observation["source"] == data.role_to_sheet["hot_tensile"]:
                            continue
                        for property_name, value in observation["outputs"].items():
                            values_by_property.setdefault(property_name, []).append(float(value))
                    metadata = {
                        "family": str(melt_row.get(data.technical_columns[("melt", "family")]) or ""),
                        "project": str(source_row.get(data.technical_columns[("annealing", "project")]) or ""),
                        "route": str(feature.get("standard_route") or ""),
                        "peak_temperature_c": feature.get("max_temperature_c"),
                        "coating": str(feature.get("coating") or ""),
                        "learning_status": str(source_row.get(data.policy_columns[("annealing", "learning_flag/v1")]) or ""),
                        "has_observation": bool(values_by_property),
                        "observation_summary": {
                            property_name: {
                                "mean": round(float(fmean(values)), 3),
                                "std": round(float(pstdev(values)), 3),
                                "n": len(values),
                            }
                            for property_name, values in sorted(values_by_property.items())
                        },
                    }
                search_text = " ".join([key, *(str(value) for value in metadata.values() if not isinstance(value, dict))]).casefold()
                if normalized and normalized not in search_text:
                    continue
                if issue_only and key not in issue_keys:
                    continue
                items.append({"key": key, "entity_type": sheet_name, "has_issue": key in issue_keys, **metadata})
        known_keys = {item["key"] for item in items}
        for issue in data.detected_quality:
            key = issue["entity_key"]
            if not key or key in known_keys or key not in data.lineage:
                continue
            relations = data.lineage[key]
            key_column = next((column for column, values in relations.items() if key in values), "")
            sheet_name = next((sheet for sheet, column in app.state.data.entity_sheets.items() if column == key_column), issue["source_sheet"])
            if entity_type and sheet_name != entity_type:
                continue
            if normalized and normalized not in key.casefold():
                continue
            items.append({"key": key, "entity_type": sheet_name, "has_issue": True})
            known_keys.add(key)
        items.sort(key=lambda item: (not item["has_issue"], item["entity_type"], item["key"]))
        return {
            "items": items[:max(1, min(limit, 100))],
            "total_entities": sum(counts.values()),
            "relation_rows": len(data.sheets[data.relation_sheet]),
            "detected_issues": len(data.detected_quality),
            "counts_by_type": counts,
        }

    @app.get("/api/lineage/{entity_key}", response_model=LineageResponse)
    def lineage(entity_key: str) -> dict[str, Any]:
        item = app.state.data.lineage.get(entity_key)
        if item is None:
            raise HTTPException(404, "来歴が見つかりません")
        try:
            node = lineage_node_detail(app.state.data, entity_key)
        except KeyError:
            raise HTTPException(404, "来歴ノードの元データが見つかりません") from None
        graph = lineage_neighborhood(app.state.data, entity_key)
        connected_keys = {graph_node["key"] for graph_node in graph["nodes"]}
        issues = [issue for issue in app.state.data.detected_quality if issue["entity_key"] in connected_keys]
        try:
            candidate_from_lineage(app.state.data, entity_key)
        except ValueError as exc:
            candidate_eligible = False
            candidate_reason = str(exc)
        else:
            candidate_eligible = True
            candidate_reason = "接続された焼鈍実績を候補入力として引き継げます"
        return {
            "key": entity_key,
            "relations": item,
            "quality_issues": issues,
            "node": node,
            "graph": graph,
            "candidate_eligible": candidate_eligible,
            "candidate_reason": candidate_reason,
        }

    @app.post("/api/lineage/{entity_key}/candidate", status_code=201, response_model=Candidate, responses=PROJECT_API_ERRORS)
    def create_candidate_from_lineage(entity_key: str, project_id: str = "default") -> dict[str, Any]:
        try:
            payload = candidate_from_lineage(app.state.data, entity_key)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return create_candidate_in_project(payload, project_id).model_dump(mode="json")

    @app.post("/api/screening", status_code=201, response_model=ScreeningRunResponse)
    def screening(payload: ScreeningRequest, project_id: str = "default") -> dict[str, Any]:
        project = require_project(project_id)
        output = next(
            (item for item in task_registry().contract_for(project.task_id).task_definition.outputs if item.key == payload.target),
            None,
        )
        if output is None:
            raise HTTPException(422, "この予測タスクにない目標特性です")
        target_capability = next(
            item for item in task_registry().contract_for(project.task_id).runtime_capability.targets if item.target == payload.target
        )
        base = store().get_candidate(payload.base_candidate_id, project_id)
        if not base:
            raise HTTPException(404, "基準候補が見つかりません")
        try:
            result = run_latin_hypercube(
                task_registry().runtime_for(project.task_id),
                base,
                payload,
                goal_direction=output.goal_direction,
                probability_available=target_capability.goal_probability != "unavailable",
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return store().create_screening_run(jsonable_encoder(result), project_id)

    @app.get("/api/screening", response_model=list[ScreeningRunResponse])
    def list_screening_runs(project_id: str = "default") -> list[dict[str, Any]]:
        require_project(project_id)
        return store().list_screening_runs(project_id)

    @app.get("/api/screening/{run_id}", response_model=ScreeningRunResponse)
    def get_screening_run(run_id: str, project_id: str = "default") -> dict[str, Any]:
        run = store().get_screening_run(run_id, project_id)
        if not run:
            raise HTTPException(404, "スクリーニング結果が見つかりません")
        return run

    @app.post("/api/screening/{run_id}/points/{point_index}/candidate", status_code=201, response_model=Candidate, responses=PROJECT_API_ERRORS)
    def screening_point_to_candidate(run_id: str, point_index: int, project_id: str = "default") -> dict[str, Any]:
        run = store().get_screening_run(run_id, project_id)
        if not run:
            raise HTTPException(404, "スクリーニング結果が見つかりません")
        point = next((item for item in run["points"] if item["index"] == point_index), None)
        if not point:
            raise HTTPException(404, "スクリーニング点が見つかりません")
        payload = CandidateInput.model_validate({
            **point["candidate"],
            "name": f"Screen {run_id[:6]} #{point_index + 1}",
            "provenance": {"source_kind": "screening", "source_ref": {"run_id": run_id, "point_id": str(point_index)}},
        })
        return create_candidate_in_project(payload, project_id).model_dump(mode="json")

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}/snapshots", response_model=list[SnapshotResponse])
    def snapshots(project_id: str, candidate_id: str) -> list[dict[str, Any]]:
        if not store().get_candidate(candidate_id, project_id, include_archived=True):
            raise HTTPException(404, "候補が見つかりません")
        return store().list_snapshots(candidate_id)

    @app.post("/api/projects/{project_id}/candidates/{candidate_id}/snapshots", status_code=201, response_model=SnapshotResponse)
    def create_snapshot(project_id: str, candidate_id: str) -> dict[str, Any]:
        candidate = store().get_candidate(candidate_id, project_id)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        return snapshot_for_candidate(candidate)

    def snapshot_for_candidate(candidate: Any, result: dict[str, Any] | None = None) -> dict[str, Any]:
        if result is None:
            project = store().get_project(candidate.project_id)
            if project is None:
                raise HTTPException(404, "プロジェクトが見つかりません")
            result = task_registry().runtime_for(project.task_id).predict(
                candidate,
                detailed=True,
                include_curve=project.task_id == ANNEALED_TASK_ID,
                target_values=project.target_values,
            )
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
        require_project(project_id)
        snapshot = store().get_snapshot(snapshot_id)
        if not snapshot or store().get_candidate(snapshot["candidate_id"], project_id, include_archived=True) is None:
            raise HTTPException(404, "スナップショットが見つかりません")
        try:
            payload = candidate_input_from_snapshot(snapshot_id, snapshot["payload"])
        except SnapshotPayloadError as exc:
            raise HTTPException(422, str(exc)) from exc
        return create_candidate_in_project(payload, project_id).model_dump(mode="json")

    @app.get("/api/projects/{project_id}/candidates/{candidate_id}/actuals", response_model=list[ActualMeasurement])
    def list_actuals(project_id: str, candidate_id: str) -> list[dict[str, Any]]:
        if not store().get_candidate(candidate_id, project_id, include_archived=True):
            raise HTTPException(404, "候補が見つかりません")
        return [actual.model_dump(mode="json") for actual in store().list_actuals(candidate_id)]

    @app.post("/api/projects/{project_id}/candidates/{candidate_id}/actuals", status_code=201, response_model=ActualMeasurement)
    def create_actual(project_id: str, candidate_id: str, payload: ActualMeasurementInput) -> dict[str, Any]:
        project = require_project(project_id)
        candidate = store().get_candidate(candidate_id, project_id)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        outputs = {output.key: output.unit for output in task_registry().contract_for(project.task_id).task_definition.outputs}
        if outputs.get(payload.property) != payload.unit:
            raise HTTPException(422, "実測の特性または単位が予測タスクと一致しません")
        snapshot = snapshot_for_candidate(candidate)
        return store().create_actual(candidate_id, snapshot["id"], payload).model_dump(mode="json")

    @app.delete("/api/projects/{project_id}/candidates/{candidate_id}/actuals/{actual_id}", status_code=204)
    def delete_actual(project_id: str, candidate_id: str, actual_id: str) -> Response:
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
