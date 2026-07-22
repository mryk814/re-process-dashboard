from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .dependencies import get_project_runtime_resolver, get_store, get_task_registry
from .errors import DomainApiException, PROJECT_API_ERRORS
from ..application.screening import ScreeningNotFoundError, ScreeningService, ScreeningValidationError
from ..schemas import ScreeningCandidateBatchRequest, ScreeningCandidateBatchResponse, ScreeningRequest, ScreeningRunResponse
from ..store import CandidateLimitError, ProjectNotFoundError, Store
from ..task_registry import TaskRegistry
from ..project_runtime_resolver import ProjectRuntimeResolver


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


def get_screening_service(
    store: StoreDependency, registry: RegistryDependency, resolver: ResolverDependency
) -> ScreeningService:
    return ScreeningService(store, registry, resolver)


ScreeningServiceDependency = Annotated[ScreeningService, Depends(get_screening_service)]
SCREENING_ERRORS = (ProjectNotFoundError, ScreeningNotFoundError, ScreeningValidationError, CandidateLimitError)


def _raise_screening_error(exc: Exception) -> None:
    if isinstance(exc, ProjectNotFoundError):
        raise HTTPException(404, "プロジェクトが見つかりません") from exc
    if isinstance(exc, ScreeningNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ScreeningValidationError):
        raise HTTPException(422, str(exc)) from exc
    if isinstance(exc, CandidateLimitError):
        raise DomainApiException(409, "candidate_limit", str(exc)) from exc
    raise exc


@router.post("/api/screening", status_code=201, response_model=ScreeningRunResponse)
def screening(payload: ScreeningRequest, service: ScreeningServiceDependency, project_id: str = "default") -> ScreeningRunResponse:
    try:
        return service.run(payload, project_id)
    except SCREENING_ERRORS as exc:
        _raise_screening_error(exc)


@router.get("/api/screening", response_model=list[ScreeningRunResponse])
def list_screening_runs(service: ScreeningServiceDependency, project_id: str = "default") -> list[ScreeningRunResponse]:
    try:
        return service.list(project_id)
    except SCREENING_ERRORS as exc:
        _raise_screening_error(exc)


@router.get("/api/screening/{run_id}", response_model=ScreeningRunResponse)
def get_screening_run(run_id: str, service: ScreeningServiceDependency, project_id: str = "default") -> ScreeningRunResponse:
    try:
        return service.get(run_id, project_id)
    except SCREENING_ERRORS as exc:
        _raise_screening_error(exc)


@router.post("/api/screening/{run_id}/candidates", status_code=201, response_model=ScreeningCandidateBatchResponse, responses=PROJECT_API_ERRORS)
def screening_points_to_candidates(
    run_id: str,
    payload: ScreeningCandidateBatchRequest,
    service: ScreeningServiceDependency,
    project_id: str = "default",
) -> ScreeningCandidateBatchResponse:
    try:
        return service.promote(run_id, payload, project_id)
    except SCREENING_ERRORS as exc:
        _raise_screening_error(exc)
