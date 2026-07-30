from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .dependencies import get_project_runtime_resolver, get_store, get_task_registry
from .errors import DomainApiException, PROJECT_API_ERRORS
from ..application.screening import (
    ScreeningBatchSelectionError,
    ScreeningNotFoundError,
    ScreeningReferencedError,
    ScreeningService,
    ScreeningValidationError,
)
from material_workbench.contracts.schemas import ScreeningCandidateBatchRequest, ScreeningCandidateBatchResponse, ScreeningRequest, ScreeningRunResponse
from material_workbench.contracts.proposal_contracts import ProposalStrategyAvailability
from material_workbench.contracts.batch_proposal_contracts import BatchSelectorAvailability
from material_workbench.persistence.store import CandidateLimitError, ProjectNotFoundError, Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.application.project_runtime import ProjectRuntimeResolver


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


def get_screening_service(
    store: StoreDependency, registry: RegistryDependency, resolver: ResolverDependency
) -> ScreeningService:
    return ScreeningService(store, registry, resolver)


ScreeningServiceDependency = Annotated[ScreeningService, Depends(get_screening_service)]
SCREENING_ERRORS = (
    ProjectNotFoundError,
    ScreeningNotFoundError,
    ScreeningReferencedError,
    ScreeningValidationError,
    CandidateLimitError,
)


def _raise_screening_error(exc: Exception) -> None:
    if isinstance(exc, ProjectNotFoundError):
        raise HTTPException(404, "プロジェクトが見つかりません") from exc
    if isinstance(exc, ScreeningNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, ScreeningReferencedError):
        raise DomainApiException(409, "screening_run_referenced", str(exc)) from exc
    if isinstance(exc, ScreeningBatchSelectionError):
        raise DomainApiException(
            422,
            f"batch_{exc.failure_kind}",
            str(exc),
        ) from exc
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


@router.get(
    "/api/projects/{project_id}/proposal-strategies",
    response_model=list[ProposalStrategyAvailability],
)
def list_proposal_strategies(
    project_id: str,
    target: str,
    service: ScreeningServiceDependency,
) -> list[ProposalStrategyAvailability]:
    try:
        return service.available_strategies(project_id, target)
    except SCREENING_ERRORS as exc:
        _raise_screening_error(exc)


@router.get(
    "/api/projects/{project_id}/batch-selectors",
    response_model=list[BatchSelectorAvailability],
)
def list_batch_selectors(
    project_id: str,
    target: str,
    service: ScreeningServiceDependency,
) -> list[BatchSelectorAvailability]:
    try:
        return service.available_batch_selectors(project_id, target)
    except SCREENING_ERRORS as exc:
        _raise_screening_error(exc)


@router.get("/api/screening/{run_id}", response_model=ScreeningRunResponse)
def get_screening_run(run_id: str, service: ScreeningServiceDependency, project_id: str = "default") -> ScreeningRunResponse:
    try:
        return service.get(run_id, project_id)
    except SCREENING_ERRORS as exc:
        _raise_screening_error(exc)


@router.delete("/api/screening/{run_id}", status_code=204, responses=PROJECT_API_ERRORS)
def delete_screening_run(run_id: str, service: ScreeningServiceDependency, project_id: str = "default") -> None:
    try:
        service.delete(run_id, project_id)
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
