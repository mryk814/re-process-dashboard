from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .candidates import CANDIDATE_APPLICATION_ERRORS, candidate_http_error
from .dependencies import get_project_runtime_resolver, get_store, get_task_registry
from .errors import PROJECT_API_ERRORS
from decision_workbench.application.historical_observations import (
    HistoricalObservationNotFoundError,
    HistoricalObservationService,
    HistoricalObservationUnavailableError,
)
from decision_workbench.contracts.historical_observation_contracts import (
    HistoricalObservationCandidateResponse,
    HistoricalObservationEvidence,
    HistoricalObservationListResponse,
)
from decision_workbench.persistence.store import ProjectNotFoundError, Store
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.tasks.task_registry import TaskRegistry


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


def get_historical_observation_service(
    store: StoreDependency, registry: RegistryDependency, resolver: ResolverDependency
) -> HistoricalObservationService:
    return HistoricalObservationService(store, registry, resolver)


ServiceDependency = Annotated[HistoricalObservationService, Depends(get_historical_observation_service)]


def _raise_error(exc: Exception) -> None:
    if isinstance(exc, ProjectNotFoundError):
        raise HTTPException(404, "プロジェクトが見つかりません") from exc
    if isinstance(exc, HistoricalObservationNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, HistoricalObservationUnavailableError):
        raise HTTPException(422, str(exc)) from exc
    if isinstance(exc, CANDIDATE_APPLICATION_ERRORS):
        raise candidate_http_error(exc) from exc
    raise exc


HISTORICAL_OBSERVATION_ERRORS = (
    ProjectNotFoundError,
    HistoricalObservationNotFoundError,
    HistoricalObservationUnavailableError,
) + CANDIDATE_APPLICATION_ERRORS


@router.get(
    "/api/projects/{project_id}/historical-observations",
    response_model=HistoricalObservationListResponse,
    responses=PROJECT_API_ERRORS,
)
def list_historical_observations(
    project_id: str, service: ServiceDependency
) -> HistoricalObservationListResponse:
    try:
        return service.list(project_id)
    except HISTORICAL_OBSERVATION_ERRORS as exc:
        _raise_error(exc)


@router.post(
    "/api/projects/{project_id}/historical-observations/{observation_id}/candidate",
    status_code=201,
    response_model=HistoricalObservationCandidateResponse,
    responses=PROJECT_API_ERRORS,
)
def create_candidate_from_historical_observation(
    project_id: str, observation_id: str, service: ServiceDependency
) -> HistoricalObservationCandidateResponse:
    try:
        return service.create_candidate(project_id, observation_id)
    except HISTORICAL_OBSERVATION_ERRORS as exc:
        _raise_error(exc)


@router.get(
    "/api/projects/{project_id}/candidates/{candidate_id}/historical-evidence",
    response_model=HistoricalObservationEvidence,
    responses=PROJECT_API_ERRORS,
)
def historical_observation_evidence(
    project_id: str, candidate_id: str, service: ServiceDependency
) -> HistoricalObservationEvidence:
    try:
        return service.evidence(project_id, candidate_id)
    except HISTORICAL_OBSERVATION_ERRORS as exc:
        _raise_error(exc)
