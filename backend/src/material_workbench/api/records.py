from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from .candidates import CANDIDATE_APPLICATION_ERRORS, candidate_http_error
from .dependencies import get_inference_work_graph, get_project_runtime_resolver, get_store, get_task_registry
from .errors import DomainApiException, PROJECT_API_ERRORS
from .inference import INFERENCE_ERRORS, get_inference_service, inference_http_error
from ..application.records import RecordIntegrityError, RecordNotFoundError, RecordService, RecordValidationError
from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.contracts.schemas import ActualMeasurement, ActualMeasurementInput, Candidate, DetailedPredictionResponse, PredictionVsActualResponse, SnapshotResponse
from material_workbench.persistence.store import ProjectNotFoundError, Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.application.project_runtime import ProjectRuntimeResolver


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
GraphDependency = Annotated[InferenceWorkGraph, Depends(get_inference_work_graph)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


def get_record_service(
    store: StoreDependency,
    registry: RegistryDependency,
    graph: GraphDependency,
    resolver: ResolverDependency,
) -> RecordService:
    return RecordService(
        store,
        registry,
        get_inference_service(store, registry, graph, resolver),
        resolver,
    )


RecordServiceDependency = Annotated[RecordService, Depends(get_record_service)]
RECORD_ERRORS = (ProjectNotFoundError, RecordNotFoundError, RecordValidationError, RecordIntegrityError) + INFERENCE_ERRORS + CANDIDATE_APPLICATION_ERRORS


def _raise_record_error(exc: Exception) -> None:
    if isinstance(exc, RecordNotFoundError): raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, RecordValidationError): raise HTTPException(422, str(exc)) from exc
    if isinstance(exc, RecordIntegrityError): raise DomainApiException(409, "data_integrity_error", str(exc)) from exc
    converted = inference_http_error(exc) if isinstance(exc, INFERENCE_ERRORS) else candidate_http_error(exc)
    raise converted from exc


@router.post("/api/projects/{project_id}/candidates/{candidate_id}/predict", response_model=DetailedPredictionResponse, responses=PROJECT_API_ERRORS, operation_id="createDetailedCandidatePrediction")
def predict(project_id: str, candidate_id: str, expected_revision: int, service: RecordServiceDependency) -> DetailedPredictionResponse:
    try: return service.predict_and_snapshot(project_id, candidate_id, expected_revision)
    except RECORD_ERRORS as exc: _raise_record_error(exc)


@router.get("/api/projects/{project_id}/candidates/{candidate_id}/snapshots", response_model=list[SnapshotResponse])
def snapshots(project_id: str, candidate_id: str, service: RecordServiceDependency) -> list[SnapshotResponse]:
    try: return service.list_snapshots(project_id, candidate_id)
    except RECORD_ERRORS as exc: _raise_record_error(exc)


@router.post("/api/projects/{project_id}/candidates/{candidate_id}/snapshots", status_code=201, response_model=SnapshotResponse)
def create_snapshot(project_id: str, candidate_id: str, service: RecordServiceDependency) -> SnapshotResponse:
    try: return service.create_snapshot(project_id, candidate_id)
    except RECORD_ERRORS as exc: _raise_record_error(exc)


@router.post("/api/projects/{project_id}/snapshots/{snapshot_id}/restore", status_code=201, response_model=Candidate, responses=PROJECT_API_ERRORS)
def restore_snapshot(project_id: str, snapshot_id: str, service: RecordServiceDependency) -> Candidate:
    try: return service.restore(project_id, snapshot_id)
    except RECORD_ERRORS as exc: _raise_record_error(exc)


@router.get("/api/projects/{project_id}/snapshots/{snapshot_id}", response_model=SnapshotResponse, responses=PROJECT_API_ERRORS)
def get_snapshot(project_id: str, snapshot_id: str, service: RecordServiceDependency) -> SnapshotResponse:
    try: return service.get_snapshot(project_id, snapshot_id)
    except RECORD_ERRORS as exc: _raise_record_error(exc)


@router.get("/api/projects/{project_id}/candidates/{candidate_id}/actuals", response_model=list[ActualMeasurement])
def list_actuals(project_id: str, candidate_id: str, service: RecordServiceDependency) -> list[ActualMeasurement]:
    try: return service.list_actuals(project_id, candidate_id)
    except RECORD_ERRORS as exc: _raise_record_error(exc)


@router.post("/api/projects/{project_id}/candidates/{candidate_id}/actuals", status_code=201, response_model=ActualMeasurement, responses=PROJECT_API_ERRORS)
def create_actual(project_id: str, candidate_id: str, payload: ActualMeasurementInput, expected_revision: int, service: RecordServiceDependency) -> ActualMeasurement:
    try: return service.create_actual(project_id, candidate_id, expected_revision, payload)
    except RECORD_ERRORS as exc: _raise_record_error(exc)


@router.delete("/api/projects/{project_id}/candidates/{candidate_id}/actuals/{actual_id}", status_code=204)
def delete_actual(project_id: str, candidate_id: str, actual_id: str, service: RecordServiceDependency) -> Response:
    try: service.delete_actual(project_id, candidate_id, actual_id)
    except RECORD_ERRORS as exc: _raise_record_error(exc)
    return Response(status_code=204)


@router.get("/api/projects/{project_id}/candidates/{candidate_id}/prediction-vs-actual", response_model=PredictionVsActualResponse, responses=PROJECT_API_ERRORS)
def prediction_vs_actual(project_id: str, candidate_id: str, service: RecordServiceDependency) -> PredictionVsActualResponse:
    try: return service.prediction_vs_actual(project_id, candidate_id)
    except RECORD_ERRORS as exc: _raise_record_error(exc)
