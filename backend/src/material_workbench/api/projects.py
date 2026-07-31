from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .dependencies import (
    get_model_package_origins,
    get_store,
    get_task_registry,
    get_workspace_catalog,
)
from .errors import DomainApiException, PROJECT_API_ERRORS
from ..application.projects import (
    ProjectHistoryIntegrityError,
    ProjectService,
    ProjectTaskLockedError,
    ProjectValidationError,
)
from material_workbench.contracts.candidate_project_contracts import (
    Project,
    ProjectCreateInput,
    ProjectDecisionInput,
    ProjectGroupMoveInput,
    ProjectUpdateInput,
)
from material_workbench.contracts.evidence_contracts import ProjectHistoryResponse
from material_workbench.contracts.objective_contracts import ObjectiveDefinitionRevision
from material_workbench.persistence.store import (
    ActiveProjectPurgeError,
    ProjectGroupConflictError,
    ProjectHasDerivedCandidatesError,
    ProjectHasSuccessorsError,
    ProjectNotFoundError,
    ProtectedProjectError,
    Store,
)
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
CatalogDependency = Annotated[WorkspaceCatalog, Depends(get_workspace_catalog)]
ModelPackageOriginsDependency = Annotated[
    dict[str, str],
    Depends(get_model_package_origins),
]


def get_project_service(
    store: StoreDependency,
    registry: RegistryDependency,
    catalog: CatalogDependency,
    package_origins: ModelPackageOriginsDependency,
) -> ProjectService:
    return ProjectService(
        store,
        registry,
        catalog,
        available_model_package_ids=set(package_origins),
    )


ProjectServiceDependency = Annotated[ProjectService, Depends(get_project_service)]


def _not_found(exc: ProjectNotFoundError) -> HTTPException:
    return HTTPException(404, "プロジェクトが見つかりません")


@router.get("/api/projects", response_model=list[Project])
def list_projects(
    service: ProjectServiceDependency,
    include_archived: bool = False,
) -> list[Project]:
    return service.list(include_archived=include_archived)


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
def get_project(
    project_id: str,
    service: ProjectServiceDependency,
    include_archived: bool = False,
) -> Project:
    try:
        return service.require(project_id, include_archived=include_archived)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/api/projects/{project_id}/objectives",
    response_model=list[ObjectiveDefinitionRevision],
    responses=PROJECT_API_ERRORS,
    operation_id="listProjectObjectiveRevisions",
)
def list_project_objective_revisions(
    project_id: str,
    service: ProjectServiceDependency,
) -> list[ObjectiveDefinitionRevision]:
    try:
        return service.objective_revisions(project_id)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc


@router.delete(
    "/api/projects/{project_id}",
    status_code=204,
    responses=PROJECT_API_ERRORS,
    summary="Archive Project By Id",
    operation_id="archiveProject",
)
def archive_project(project_id: str, service: ProjectServiceDependency) -> Response:
    try:
        service.archive(project_id)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProtectedProjectError as exc:
        raise DomainApiException(409, "protected_project", str(exc)) from exc
    except ProjectHasSuccessorsError as exc:
        raise DomainApiException(409, "project_has_successors", str(exc)) from exc
    except ProjectHasDerivedCandidatesError as exc:
        raise DomainApiException(409, "project_has_derived_candidates", str(exc)) from exc
    return Response(status_code=204)


@router.post(
    "/api/projects/{project_id}/restore",
    response_model=Project,
    responses=PROJECT_API_ERRORS,
    operation_id="restoreProject",
)
def restore_project(
    project_id: str, service: ProjectServiceDependency
) -> Project:
    try:
        return service.restore(project_id)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProtectedProjectError as exc:
        raise DomainApiException(409, "protected_project", str(exc)) from exc


@router.delete(
    "/api/projects/{project_id}/purge",
    status_code=204,
    responses=PROJECT_API_ERRORS,
    operation_id="purgeProject",
)
def purge_project(
    project_id: str,
    service: ProjectServiceDependency,
    confirm_project_id: str = Query(...),
) -> Response:
    if confirm_project_id != project_id:
        raise DomainApiException(
            409,
            "project_purge_confirmation_mismatch",
            "完全削除の確認用Project IDが一致しません",
        )
    try:
        service.purge(project_id)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProtectedProjectError as exc:
        raise DomainApiException(409, "protected_project", str(exc)) from exc
    except ActiveProjectPurgeError as exc:
        raise DomainApiException(409, "active_project_purge", str(exc)) from exc
    except ProjectHasSuccessorsError as exc:
        raise DomainApiException(409, "project_has_successors", str(exc)) from exc
    except ProjectHasDerivedCandidatesError as exc:
        raise DomainApiException(
            409, "project_has_derived_candidates", str(exc)
        ) from exc
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


@router.put(
    "/api/projects/{project_id}/group",
    response_model=Project,
    responses=PROJECT_API_ERRORS,
    operation_id="moveProjectToGroup",
)
def move_project_to_group(
    project_id: str,
    payload: ProjectGroupMoveInput,
    service: ProjectServiceDependency,
) -> Project:
    try:
        return service.move_to_group(project_id, payload)
    except ProjectNotFoundError as exc:
        raise _not_found(exc) from exc
    except ProjectGroupConflictError as exc:
        raise DomainApiException(409, "project_group_conflict", str(exc)) from exc
    except ProjectValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
