from __future__ import annotations

import os
import csv
import math
from collections import Counter
from contextlib import asynccontextmanager
from io import BytesIO, StringIO
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Literal, Mapping

from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from .api.errors import DomainApiException, PROJECT_API_ERRORS, install_exception_handlers
from .api.security import configure_local_access
from .api.catalog import router as catalog_router
from .api.projects import router as projects_router
from .importer import lineage_neighborhood, lineage_node_detail
from .demo_seed import initialize_demo_projects
from .inference_work_graph import InferenceKey, InferenceWorkGraph
from .runtime import ModelRuntime
from .model_lifecycle import ACTIVE_PACKAGES_PATH, load_active_packages, resolve_configured_package, validate_active_package_task_set
from .schemas import (
    ActualMeasurement,
    ActualMeasurementInput,
    Candidate,
    CandidateImportResponse,
    CandidateInput,
    CandidateUpdate,
    CurveFamilyResponse,
    DetailedPredictionResponse,
    InferenceDiagnosticsResponse,
    LineageIndexResponse,
    LineageResponse,
    PredictionResponse,
    PredictionVsActualResponse,
    Project,
    QualityResponse,
    ResponseCurveResponse,
    ScreeningRequest,
    ScreeningCandidateBatchRequest,
    ScreeningCandidateBatchResponse,
    ScreeningRunResponse,
    SimilarObservation,
    SnapshotResponse,
)
from .services import candidate_from_lineage, candidates_xlsx, import_candidates_xlsx, run_latin_hypercube
from .snapshot_reader import SnapshotPayloadError, candidate_input_from_snapshot
from .store import CandidateArchivedError, CandidateLimitError, CandidateRevisionConflictError, ProjectNotFoundError, Store, StoreDataIntegrityError
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

    def project_data_explorer(project_id: str, capability: Literal["quality", "lineage"]) -> DataExplorerEntry:
        project = require_project(project_id)
        try:
            explorer = task_registry().data_explorer_for(project.task_id)
        except TaskRegistryError as exc:
            raise HTTPException(404, "このプロジェクトではデータ探索を利用できません") from exc
        if not getattr(explorer.capability, capability):
            raise HTTPException(404, "このプロジェクトではデータ探索を利用できません")
        return explorer

    def inference_work_graph() -> InferenceWorkGraph:
        return app.state.inference_work_graph

    def inference_key(
        task_id: str,
        candidate: Candidate,
        operation: str,
        *,
        parameters: Mapping[str, Any] | None = None,
        uses_package: bool = False,
        uses_support: bool = False,
    ) -> InferenceKey:
        entry = task_registry().entry_for(task_id)
        canonical = task_registry().validate_candidate(task_id, candidate).model_dump(
            mode="json",
            exclude={"provenance"},
        )
        return InferenceKey.build(
            task_id=task_id,
            runtime_type=entry.runtime_type,
            canonical_input=canonical,
            package_digest=entry.package_digest if uses_package else "",
            pipeline_digest=entry.pipeline_digest,
            support_digest=entry.support_digest if uses_support else None,
            operation=operation,
            operation_parameters=parameters,
        )

    def require_project(project_id: str):
        project = store().get_project(project_id)
        if not project:
            raise HTTPException(404, "プロジェクトが見つかりません")
        return project

    def create_candidate_in_project(payload: CandidateInput, project_id: str):
        project = require_project(project_id)
        if payload.provenance.source_kind == "copy":
            reference = payload.provenance.source_ref
            source_candidate = store().get_candidate(
                reference.candidate_id,
                reference.project_id,
                include_archived=True,
            )
            if source_candidate is None:
                raise HTTPException(422, "コピー元候補が見つかりません")
            if source_candidate.revision != reference.candidate_revision:
                raise HTTPException(422, "コピー元候補のrevisionが一致しません")
            source_project = require_project(reference.project_id)
            if source_project.task_id != project.task_id:
                raise HTTPException(422, "異なる予測タスクの候補はコピーできません")
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

    @app.post(
        "/api/projects/{project_id}/candidates/{candidate_id}/preview",
        response_model=PredictionResponse,
        responses=PROJECT_API_ERRORS,
        operation_id="previewProjectCandidate",
    )
    def preview_project_candidate(project_id: str, candidate_id: str, expected_revision: int) -> dict[str, Any]:
        project = require_project(project_id)
        require_task_operation(project.task_id, "preview")
        candidate = candidate_at_revision(project_id, candidate_id, expected_revision)
        task_runtime = task_registry().runtime_for(project.task_id)
        prediction = inference_work_graph().execute(
            inference_key(
                project.task_id,
                candidate,
                "preview",
                parameters={"target_values": project.target_values},
                uses_package=True,
            ),
            lambda: task_runtime.predict_core(
                candidate,
                detailed=False,
                target_values=project.target_values,
            ),
        )
        support = inference_work_graph().execute(
            inference_key(project.task_id, candidate, "support", uses_support=True),
            lambda: task_registry().entry_for(project.task_id).support_provider.support_summary(candidate),
        )
        prediction["candidate_id"] = candidate.id
        prediction["support"] = support
        prediction["similar"] = []
        if support.status != "supported" and support.message not in prediction["warnings"]:
            prediction["warnings"].append(support.message)
        return prediction

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
        if not task_registry().entry_for(project.task_id).application_capability.candidate_excel_import:
            raise HTTPException(422, "Excel候補importはこの予測タスクでは利用できません")
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            raise HTTPException(422, "Excel .xlsx ファイルを選択してください")
        runtime = task_registry().runtime_for(project.task_id)
        payloads, errors = import_candidates_xlsx(
            await file.read(),
            task_id=project.task_id,
            profile_path=runtime.data.profile_path,
        )
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
        if not task_registry().entry_for(project.task_id).application_capability.candidate_excel_export:
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
        if existing.provenance != candidate_input.provenance:
            raise DomainApiException(409, "candidate_provenance_immutable", "候補の作成元は変更できません")
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

    def candidate_at_revision(project_id: str, candidate_id: str, expected_revision: int) -> Candidate:
        candidate = store().get_candidate(candidate_id, project_id)
        if not candidate:
            raise HTTPException(404, "候補が見つかりません")
        if candidate.revision != expected_revision:
            raise DomainApiException(
                409,
                "revision_conflict",
                f"候補はrevision {candidate.revision}へ更新されています",
                current_candidate=candidate,
            )
        return candidate

    def detailed_prediction_for(project: Project, candidate: Candidate) -> dict[str, Any]:
        require_task_operation(project.task_id, "detailed_prediction")
        task_runtime = task_registry().runtime_for(project.task_id)
        result = inference_work_graph().execute(
            inference_key(
                project.task_id,
                candidate,
                "detailed",
                parameters={"target_values": project.target_values, "policy_id": "detailed-v1"},
                uses_package=True,
                uses_support=True,
            ),
            lambda: task_runtime.predict(
                candidate,
                detailed=True,
                include_curve=False,
                target_values=project.target_values,
            ),
        )
        result["candidate_id"] = candidate.id
        return result

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

    @app.get(
        "/api/projects/{project_id}/candidates/{candidate_id}/response-curve",
        response_model=ResponseCurveResponse,
        responses=PROJECT_API_ERRORS,
        operation_id="getCandidateResponseCurve",
    )
    def response_curve(
        project_id: str,
        candidate_id: str,
        expected_revision: int,
        target: str,
        variable: str,
        points: int = Query(9, ge=3, le=51),
        range_min: float | None = Query(None),
        range_max: float | None = Query(None),
        stage_name: str | None = Query(None, min_length=1),
        stage_position_m: float | None = Query(None, ge=0),
    ) -> dict[str, Any]:
        project = require_project(project_id)
        candidate = candidate_at_revision(project_id, candidate_id, expected_revision)
        definition = task_registry().contract_for(project.task_id).task_definition
        if target not in {item.key for item in definition.outputs}:
            raise HTTPException(422, "この予測タスクにない予測特性です")
        require_task_operation(project.task_id, "response_curve")
        if (range_min is None) != (range_max is None):
            raise HTTPException(422, "応答曲線の範囲は最小値と最大値をセットで指定してください")
        is_stage_temperature = variable == "heat.stage_temperature_c"
        if is_stage_temperature != (stage_name is not None and stage_position_m is not None):
            raise HTTPException(422, "工程温度の応答曲線は工程名と入口からの工程位置をセットで指定してください")
        if stage_name is not None and not stage_name.strip():
            raise HTTPException(422, "工程名は空白以外の文字を指定してください")
        axis_range = None
        if range_min is not None and range_max is not None:
            if not math.isfinite(range_min) or not math.isfinite(range_max) or range_min >= range_max:
                raise HTTPException(422, "応答曲線の範囲は有限の数値で、最小値 < 最大値にしてください")
            axis_range = (range_min, range_max)
        try:
            task_runtime = task_registry().runtime_for(project.task_id)
            curve_handler = task_registry().response_curve_for(project.task_id)
            result = inference_work_graph().execute(
                inference_key(
                    project.task_id,
                    candidate,
                    "curve",
                    parameters={"target": target, "variable": variable, "points": points, "range_min": range_min, "range_max": range_max, "stage_name": stage_name, "stage_position_m": stage_position_m, "policy_id": "fixed-grid-v2"},
                    uses_package=True,
                ),
                lambda: curve_handler(
                    task_runtime, candidate, target, variable, points, axis_range, stage_name, stage_position_m
                ),
            )
            return result
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(
        "/api/projects/{project_id}/candidates/{candidate_id}/curve-family",
        response_model=CurveFamilyResponse,
        responses=PROJECT_API_ERRORS,
        operation_id="getCandidateCurveFamily",
    )
    def curve_family(
        project_id: str,
        candidate_id: str,
        expected_revision: int,
        target: str,
        vary: str = "",
        levels: int = Query(5, ge=2, le=9),
        points: int = Query(15, ge=3, le=51),
    ) -> dict[str, Any]:
        project = require_project(project_id)
        candidate = candidate_at_revision(project_id, candidate_id, expected_revision)
        contract = task_registry().contract_for(project.task_id)
        if target not in {item.key for item in contract.task_definition.outputs}:
            raise HTTPException(422, "この予測タスクにない予測特性です")
        if contract.task_definition.curve_axis_path is None or not contract.runtime_capability.operations.response_curve:
            raise HTTPException(422, "この予測タスクは曲線ビューに対応していません")
        try:
            task_runtime = task_registry().runtime_for(project.task_id)
            family_handler = task_registry().curve_family_for(project.task_id)
            return inference_work_graph().execute(
                inference_key(
                    project.task_id,
                    candidate,
                    "curve_family",
                    parameters={"target": target, "vary": vary, "levels": levels, "points": points, "policy_id": "axis-grid-v1"},
                    uses_package=True,
                ),
                lambda: family_handler(task_runtime, candidate, target, vary or None, levels, points),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get(
        "/api/projects/{project_id}/candidates/{candidate_id}/similar",
        response_model=list[SimilarObservation],
        responses=PROJECT_API_ERRORS,
        operation_id="getCandidateSimilarity",
    )
    def similar(
        project_id: str,
        candidate_id: str,
        expected_revision: int,
        limit: int = Query(6, ge=1, le=20),
    ) -> list[dict[str, object]]:
        project = require_project(project_id)
        require_task_operation(project.task_id, "similarity")
        candidate = candidate_at_revision(project_id, candidate_id, expected_revision)
        support_provider = task_registry().entry_for(project.task_id).support_provider
        return inference_work_graph().execute(
            inference_key(
                project.task_id,
                candidate,
                "similarity",
                parameters={"limit": limit},
                uses_support=True,
            ),
            lambda: support_provider.similarity(candidate, limit),
        )

    @app.get(
        "/api/diagnostics/inference",
        response_model=InferenceDiagnosticsResponse,
        operation_id="getInferenceDiagnostics",
    )
    def inference_diagnostics() -> dict[str, Any]:
        return inference_work_graph().diagnostics()

    @app.get("/api/projects/{project_id}/quality", response_model=QualityResponse, responses=PROJECT_API_ERRORS)
    def quality(project_id: str) -> dict[str, Any]:
        project = require_project(project_id)
        data = project_data_explorer(project_id, "quality").data
        scenarios = data.quality
        detected = data.detected_quality
        # Keep the original top-level fields for the current renderer.  The
        # detected fields are actual structural checks, not workbook fixtures.
        return {
            "total": len(scenarios),
            "by_category": Counter(
                row[quality_category] for row in scenarios
            ) if (quality_category := data.technical_columns.get(("quality", "category"))) else {},
            "issues": scenarios,
            "reference_scenarios": scenarios,
            "detected_total": len(detected),
            "detected_by_type": Counter(row["issue_type"] for row in detected),
            "detected_issues": detected,
            "dataset": {
                "task_id": project.task_id,
                "source_path": data.source_path,
                "source_sha256": data.source_sha256,
                "profile_id": data.profile_id,
                "profile_path": data.profile_path,
            },
        }

    @app.get(
        "/api/projects/{project_id}/quality/export.csv",
        response_class=Response,
        responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
    )
    def export_quality(project_id: str) -> Response:
        output = StringIO()
        issues = project_data_explorer(project_id, "quality").data.detected_quality
        fieldnames = [
            "issue_id", "issue_type", "source_sheet", "entity_key", "detail",
            "focus_entity_key", "related_entity_keys", "missing_reference_key", "suggested_view",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({**issue, "related_entity_keys": "|".join(issue["related_entity_keys"])} for issue in issues)
        return Response(
            content="\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": "attachment; filename=detected-data-quality.csv"},
        )

    @app.get("/api/projects/{project_id}/lineage", response_model=LineageIndexResponse, response_model_exclude_none=True, responses=PROJECT_API_ERRORS)
    def lineage_index(project_id: str, query: str = "", entity_type: str = "", issue_only: bool = False, limit: int = 40) -> dict[str, Any]:
        data = project_data_explorer(project_id, "lineage").data
        normalized = query.strip().casefold()
        issue_keys = {issue["entity_key"] for issue in data.detected_quality if issue["entity_key"]}
        items: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        for sheet_name, key_column in data.entity_sheets.items():
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
            sheet_name = next((sheet for sheet, column in data.entity_sheets.items() if column == key_column), issue["source_sheet"])
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

    @app.get("/api/projects/{project_id}/lineage/{entity_key}", response_model=LineageResponse, responses=PROJECT_API_ERRORS)
    def lineage(project_id: str, entity_key: str, limit: int = Query(default=40, ge=1, le=200)) -> dict[str, Any]:
        project = require_project(project_id)
        data = project_data_explorer(project_id, "lineage").data
        item = data.lineage.get(entity_key)
        if item is None:
            raise HTTPException(404, "来歴が見つかりません")
        try:
            node = lineage_node_detail(data, entity_key)
        except KeyError:
            raise HTTPException(404, "来歴ノードの元データが見つかりません") from None
        graph = lineage_neighborhood(data, entity_key, max_nodes=limit)
        connected_keys = {graph_node["key"] for graph_node in graph["nodes"]}
        issues = [issue for issue in data.detected_quality if issue["entity_key"] in connected_keys]
        try:
            payload = candidate_from_lineage(data, entity_key)
            task_registry().validate_candidate(project.task_id, payload)
        except (TaskRegistryError, ValueError) as exc:
            candidate_eligible = False
            candidate_reason = str(exc)
        else:
            candidate_eligible = True
            candidate_reason = "接続された実績を候補入力として引き継げます"
        return {
            "key": entity_key,
            "relations": item,
            "quality_issues": issues,
            "node": node,
            "graph": graph,
            "candidate_eligible": candidate_eligible,
            "candidate_reason": candidate_reason,
        }

    @app.post("/api/projects/{project_id}/lineage/{entity_key}/candidate", status_code=201, response_model=Candidate, responses=PROJECT_API_ERRORS)
    def create_candidate_from_lineage(project_id: str, entity_key: str) -> dict[str, Any]:
        explorer = project_data_explorer(project_id, "lineage")
        if not explorer.capability.candidate_creation:
            raise HTTPException(404, "このプロジェクトでは実績から候補を作成できません")
        try:
            payload = candidate_from_lineage(explorer.data, entity_key)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return create_candidate_in_project(payload, project_id).model_dump(mode="json")

    @app.post("/api/screening", status_code=201, response_model=ScreeningRunResponse)
    def screening(payload: ScreeningRequest, project_id: str = "default") -> dict[str, Any]:
        project = require_project(project_id)
        definition = task_registry().contract_for(project.task_id).task_definition
        base = store().get_candidate(payload.base_candidate_id, project_id)
        if not base:
            raise HTTPException(404, "基準候補が見つかりません")
        try:
            base = Candidate.model_validate({**base.model_dump(), "inputs": payload.base_inputs.model_dump()})
            task_registry().validate_candidate(project.task_id, CandidateInput.model_validate(base.model_dump()))
        except (TaskRegistryError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        screenable_fields = {
            field.path: field
            for group in definition.input_groups
            for field in group.fields
            if field.editable and field.kind != "heat_pattern"
        }
        heat_pattern_paths = {
            f"heat_pattern.{index}.{field}"
            for index, _ in enumerate(base.inputs.heat_pattern or [])
            for field in ("time_s", "temperature_c")
        } if any(field.editable and field.kind == "heat_pattern" for group in definition.input_groups for field in group.fields) else set()
        unknown_variables = sorted(set(payload.variables) - set(screenable_fields) - heat_pattern_paths)
        if unknown_variables:
            raise HTTPException(422, f"この予測タスクで探索できない変数です: {', '.join(unknown_variables)}")
        for path, spec in payload.variables.items():
            field = screenable_fields.get(path)
            values = [spec.value] if spec.mode == "fixed" else spec.values or []
            if field is not None and field.kind == "categorical":
                if spec.mode == "range" or any(not isinstance(value, str) or value not in field.choices for value in values):
                    raise HTTPException(422, f"{field.label}は定義済み選択肢から指定してください")
            elif spec.mode in {"fixed", "list"} and any(
                not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value))
                for value in values
            ):
                raise HTTPException(422, f"{field.label if field is not None else 'ヒートパターン'}には有限の数値を指定してください")
            elif spec.mode == "range" and (not math.isfinite(float(spec.min)) or not math.isfinite(float(spec.max))):
                raise HTTPException(422, f"{field.label if field is not None else 'ヒートパターン'}には有限の範囲を指定してください")
        output = next(
            (item for item in definition.outputs if item.key == payload.target),
            None,
        )
        if output is None:
            raise HTTPException(422, "この予測タスクにない目標特性です")
        outputs = {item.key: item for item in definition.outputs}
        unknown_secondary = sorted((set(payload.secondary_targets) - set(outputs)) | ({payload.target} & set(payload.secondary_targets)))
        if unknown_secondary:
            raise HTTPException(422, f"副条件の特性を確認してください: {', '.join(unknown_secondary)}")
        capabilities = {
            item.target: item
            for item in task_registry().contract_for(project.task_id).runtime_capability.targets
        }
        try:
            result = run_latin_hypercube(
                task_registry().runtime_for(project.task_id),
                base,
                payload,
                goal_directions={key: item.goal_direction for key, item in outputs.items()},
                probability_available={key: item.goal_probability != "unavailable" for key, item in capabilities.items()},
                candidate_validator=lambda candidate: task_registry().validate_candidate(project.task_id, candidate),
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

    @app.post("/api/screening/{run_id}/candidates", status_code=201, response_model=ScreeningCandidateBatchResponse, responses=PROJECT_API_ERRORS)
    def screening_points_to_candidates(run_id: str, payload: ScreeningCandidateBatchRequest, project_id: str = "default") -> dict[str, Any]:
        run = store().get_screening_run(run_id, project_id)
        if not run:
            raise HTTPException(404, "スクリーニング結果が見つかりません")
        points = {item["index"]: item for item in run["points"]}
        unique_indices = list(dict.fromkeys(payload.point_indices))
        missing = [index for index in unique_indices if index not in points]
        if missing:
            raise HTTPException(404, f"スクリーニング点が見つかりません: {', '.join(map(str, missing))}")
        candidate_payloads = [(index, CandidateInput.model_validate({
            **points[index]["candidate"],
            "name": f"Screen {run_id[:6]} #{index + 1}",
            "provenance": {
                "source_kind": "screening",
                "source_ref": {"run_id": run_id, "point_id": str(index), "point_index": index},
            },
        })) for index in unique_indices]
        project = require_project(project_id)
        try:
            for _, candidate_payload in candidate_payloads:
                task_registry().validate_candidate(project.task_id, candidate_payload)
            created, skipped = store().create_screening_candidates(candidate_payloads, run_id, project_id)
        except CandidateLimitError as exc:
            raise DomainApiException(409, "candidate_limit", str(exc)) from exc
        except (TaskRegistryError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"candidates": created, "skipped_point_indices": skipped}

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
