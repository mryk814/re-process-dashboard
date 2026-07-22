from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response

from .dependencies import get_store, get_task_registry, get_workspace_catalog
from .errors import DomainApiException, PROJECT_API_ERRORS
from ..application.projects import (
    ProjectHistoryIntegrityError,
    ProjectService,
    ProjectTaskLockedError,
    ProjectValidationError,
)
from ..schemas import Project, ProjectCreateInput, ProjectDecisionInput, ProjectHistoryResponse, ProjectUpdateInput
from ..store import ProjectHasSuccessorsError, ProjectNotFoundError, ProtectedProjectError, Store
from ..task_registry import TaskRegistry
from ..workspace_catalog import WorkspaceCatalog


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
CatalogDependency = Annotated[WorkspaceCatalog, Depends(get_workspace_catalog)]


def get_project_service(
    store: StoreDependency, registry: RegistryDependency, catalog: CatalogDependency
) -> ProjectService:
    return ProjectService(store, registry, catalog)


ProjectServiceDependency = Annotated[ProjectService, Depends(get_project_service)]


def _not_found(exc: ProjectNotFoundError) -> HTTPException:
    return HTTPException(404, "プロジェクトが見つかりません")


@router.get("/api/projects", response_model=list[Project])
def list_projects(service: ProjectServiceDependency) -> list[Project]:
    return service.list()


@router.post("/api/projects", status_code=201, response_model=Project)
def create_project(payload: ProjectCreateInput, service: ProjectServiceDependency) -> Project:
    try:
        return service.create(payload)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProjectTaskLockedError as exc:
        raise DomainApiException(409, "project_task_locked", str(exc)) from exc
    except ProjectValidationError as exc:
        raise HTTPException(422, str(exc)) from exc

@router.get(
    "/api/projects/{project_id}",
    response_model=Project,
    summary="Get Project By Id",
    operation_id="get_project_by_id_api_projects__project_id__get",
)
def get_project(project_id: str, service: ProjectServiceDependency) -> Project:
    try:
        return service.require(project_id)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc


@router.delete(
    "/api/projects/{project_id}",
    status_code=204,
    responses=PROJECT_API_ERRORS,
    summary="Delete Project By Id",
    operation_id="delete_project_by_id_api_projects__project_id__delete",
)
def delete_project(project_id: str, service: ProjectServiceDependency) -> Response:
    try:
        service.delete(project_id)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProtectedProjectError as exc:
        raise DomainApiException(409, "protected_project", str(exc)) from exc
    except ProjectHasSuccessorsError as exc:
        raise DomainApiException(409, "project_has_successors", str(exc)) from exc
    return Response(status_code=204)


@router.get(
    "/api/projects/{project_id}/history",
    response_model=ProjectHistoryResponse,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectHistory",
)
def project_history(project_id: str, service: ProjectServiceDependency) -> ProjectHistoryResponse:
    try:
        return service.history(project_id)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProjectHistoryIntegrityError as exc:
        raise DomainApiException(409, "data_integrity_error", str(exc)) from exc


@router.put(
    "/api/projects/{project_id}",
    response_model=Project,
    responses=PROJECT_API_ERRORS,
    summary="Update Project By Id",
    operation_id="update_project_by_id_api_projects__project_id__put",
)
def update_project(project_id: str, payload: ProjectUpdateInput, service: ProjectServiceDependency) -> Project:
    try:
        return service.update(project_id, payload)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProjectTaskLockedError as exc:
        raise DomainApiException(409, "project_task_locked", str(exc)) from exc
    except ProjectValidationError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/api/projects/{project_id}/decision", response_model=Project)
def update_project_decision(project_id: str, payload: ProjectDecisionInput, service: ProjectServiceDependency) -> Project:
    try:
        return service.update_decision(project_id, payload)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProjectValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
