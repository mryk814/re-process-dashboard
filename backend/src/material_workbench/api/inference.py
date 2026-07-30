from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .candidates import CANDIDATE_APPLICATION_ERRORS, candidate_http_error
from .dependencies import get_inference_work_graph, get_project_runtime_resolver, get_store, get_task_registry
from .errors import DomainApiException, PROJECT_API_ERRORS
from ..application.inference import (
    InferenceResponseCurveNotApplicableError,
    InferenceResponseCurveTrainingRangeUnavailableError,
    InferenceService,
    InferenceValidationError,
)
from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.contracts.schemas import CurveFamilyResponse, InferenceDiagnosticsResponse, PredictionResponse, ResponseContourResponse, ResponseCurveResponse, SimilarObservation
from material_workbench.persistence.store import CandidateRevisionConflictError, ProjectNotFoundError, Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.application.project_runtime import ProjectRuntimeResolver


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
GraphDependency = Annotated[InferenceWorkGraph, Depends(get_inference_work_graph)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


def get_inference_service(
    store: StoreDependency,
    registry: RegistryDependency,
    graph: GraphDependency,
    resolver: ResolverDependency,
) -> InferenceService:
    return InferenceService(store, registry, graph, resolver)


InferenceServiceDependency = Annotated[InferenceService, Depends(get_inference_service)]
INFERENCE_ERRORS = (ProjectNotFoundError, InferenceValidationError) + CANDIDATE_APPLICATION_ERRORS


def inference_http_error(exc: Exception) -> Exception:
    if isinstance(exc, InferenceResponseCurveNotApplicableError):
        return DomainApiException(422, "response_curve_not_applicable", str(exc))
    if isinstance(exc, InferenceResponseCurveTrainingRangeUnavailableError):
        return DomainApiException(422, "response_curve_training_range_unavailable", str(exc))
    if isinstance(exc, InferenceValidationError):
        return HTTPException(422, str(exc))
    if isinstance(exc, CandidateRevisionConflictError):
        return DomainApiException(409, "revision_conflict", f"候補はrevision {exc.current.revision}へ更新されています", current_candidate=exc.current)
    return candidate_http_error(exc)


def _raise_inference_error(exc: Exception) -> None:
    converted = inference_http_error(exc)
    if converted is exc:
        raise exc
    raise converted from exc


@router.post("/api/projects/{project_id}/candidates/{candidate_id}/preview", response_model=PredictionResponse, responses=PROJECT_API_ERRORS, operation_id="previewProjectCandidate")
def preview_project_candidate(project_id: str, candidate_id: str, expected_revision: int, service: InferenceServiceDependency) -> dict[str, Any]:
    try:
        return service.preview(project_id, candidate_id, expected_revision)
    except INFERENCE_ERRORS as exc:
        _raise_inference_error(exc)


@router.get("/api/projects/{project_id}/candidates/{candidate_id}/response-curve", response_model=ResponseCurveResponse, responses=PROJECT_API_ERRORS, operation_id="getCandidateResponseCurve")
def response_curve(project_id: str, candidate_id: str, expected_revision: int, target: str, variable: str, service: InferenceServiceDependency, points: int = Query(9, ge=3, le=51), range_min: float | None = Query(None), range_max: float | None = Query(None), stage_name: str | None = Query(None, min_length=1), stage_position_m: float | None = Query(None, ge=0)) -> dict[str, Any]:
    try:
        return service.response_curve(project_id, candidate_id, expected_revision, target, variable, points, range_min, range_max, stage_name, stage_position_m)
    except INFERENCE_ERRORS as exc:
        _raise_inference_error(exc)


@router.get("/api/projects/{project_id}/candidates/{candidate_id}/curve-family", response_model=CurveFamilyResponse, responses=PROJECT_API_ERRORS, operation_id="getCandidateCurveFamily")
def curve_family(project_id: str, candidate_id: str, expected_revision: int, target: str, service: InferenceServiceDependency, vary: str = "", levels: int = Query(5, ge=2, le=9), points: int = Query(15, ge=3, le=51)) -> dict[str, Any]:
    try:
        return service.curve_family(project_id, candidate_id, expected_revision, target, vary, levels, points)
    except INFERENCE_ERRORS as exc:
        _raise_inference_error(exc)


@router.get("/api/projects/{project_id}/candidates/{candidate_id}/response-contour", response_model=ResponseContourResponse, responses=PROJECT_API_ERRORS, operation_id="getCandidateResponseContour")
def response_contour(
    project_id: str,
    candidate_id: str,
    expected_revision: int,
    target: str,
    x_variable: str,
    y_variable: str,
    service: InferenceServiceDependency,
    points: int = Query(11, ge=7, le=17),
) -> dict[str, Any]:
    try:
        return service.response_contour(
            project_id,
            candidate_id,
            expected_revision,
            target,
            x_variable,
            y_variable,
            points,
        )
    except INFERENCE_ERRORS as exc:
        _raise_inference_error(exc)


@router.get("/api/projects/{project_id}/candidates/{candidate_id}/similar", response_model=list[SimilarObservation], responses=PROJECT_API_ERRORS, operation_id="getCandidateSimilarity")
def similar(
    project_id: str,
    candidate_id: str,
    expected_revision: int,
    service: InferenceServiceDependency,
    limit: int = Query(6, ge=1, le=20),
    target: str | None = Query(None),
) -> list[dict[str, object]]:
    try:
        return service.similar(project_id, candidate_id, expected_revision, limit, target)
    except INFERENCE_ERRORS as exc:
        _raise_inference_error(exc)


@router.get("/api/diagnostics/inference", response_model=InferenceDiagnosticsResponse, operation_id="getInferenceDiagnostics")
def inference_diagnostics(service: InferenceServiceDependency) -> dict[str, Any]:
    return service.diagnostics()
