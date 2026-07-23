from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .candidates import CANDIDATE_APPLICATION_ERRORS, candidate_http_error
from .dependencies import get_project_runtime_resolver, get_store, get_task_registry
from .errors import PROJECT_API_ERRORS
from ..application.data_exploration import (
    DataExplorationService,
    DataExplorationValidationError,
    DataExplorerUnavailableError,
    LineageNotFoundError,
)
from material_workbench.contracts.schemas import Candidate, LineageIndexResponse, LineageResponse, QualityResponse
from material_workbench.persistence.store import ProjectNotFoundError, Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


def get_data_exploration_service(
    store: StoreDependency, registry: RegistryDependency, resolver: ResolverDependency
) -> DataExplorationService:
    return DataExplorationService(store, registry, resolver)


DataExplorationServiceDependency = Annotated[DataExplorationService, Depends(get_data_exploration_service)]


def _raise_data_error(exc: Exception) -> None:
    if isinstance(exc, ProjectNotFoundError):
        raise HTTPException(404, "プロジェクトが見つかりません") from exc
    if isinstance(exc, DataExplorerUnavailableError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, LineageNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, DataExplorationValidationError):
        raise HTTPException(422, str(exc)) from exc
    if isinstance(exc, CANDIDATE_APPLICATION_ERRORS):
        converted = candidate_http_error(exc)
        raise converted from exc
    raise exc


DATA_EXPLORATION_ERRORS = (
    ProjectNotFoundError,
    DataExplorerUnavailableError,
    LineageNotFoundError,
    DataExplorationValidationError,
) + CANDIDATE_APPLICATION_ERRORS


@router.get("/api/projects/{project_id}/quality", response_model=QualityResponse, responses=PROJECT_API_ERRORS)
def quality(project_id: str, service: DataExplorationServiceDependency) -> QualityResponse:
    try:
        return service.quality(project_id)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)


@router.get(
    "/api/projects/{project_id}/quality/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_quality(project_id: str, service: DataExplorationServiceDependency) -> Response:
    try:
        contents = service.quality_csv(project_id)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)
    return Response(
        content=contents,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=detected-data-quality.csv"},
    )


@router.get("/api/projects/{project_id}/lineage", response_model=LineageIndexResponse, response_model_exclude_none=True, responses=PROJECT_API_ERRORS)
def lineage_index(
    project_id: str,
    service: DataExplorationServiceDependency,
    query: str = "",
    entity_type: str = "",
    issue_filter: Literal["all", "with_issues", "without_issues"] = "all",
    limit: int = Query(default=200, ge=1, le=500),
) -> LineageIndexResponse:
    try:
        return service.lineage_index(
            project_id,
            query=query,
            entity_type=entity_type,
            issue_filter=issue_filter,
            limit=limit,
        )
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)


@router.get("/api/projects/{project_id}/lineage/{entity_key}", response_model=LineageResponse, responses=PROJECT_API_ERRORS)
def lineage(
    project_id: str,
    entity_key: str,
    service: DataExplorationServiceDependency,
    limit: int = Query(default=40, ge=1, le=200),
) -> LineageResponse:
    try:
        return service.lineage(project_id, entity_key, limit=limit)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)


@router.post("/api/projects/{project_id}/lineage/{entity_key}/candidate", status_code=201, response_model=Candidate, responses=PROJECT_API_ERRORS)
def create_candidate_from_lineage(
    project_id: str,
    entity_key: str,
    service: DataExplorationServiceDependency,
    process_key: str | None = None,
    melt_key: str | None = None,
) -> Candidate:
    try:
        return service.create_candidate_from_lineage(
            project_id,
            entity_key,
            process_key=process_key,
            melt_key=melt_key,
        )
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)
