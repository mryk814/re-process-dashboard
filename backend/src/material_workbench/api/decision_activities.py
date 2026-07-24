from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from .candidates import CANDIDATE_APPLICATION_ERRORS, candidate_http_error
from .dependencies import (
    get_inference_work_graph,
    get_project_runtime_resolver,
    get_store,
    get_task_registry,
)
from ..application.decision_activities import (
    DecisionActivityNotFoundError,
    DecisionActivityService,
    DecisionActivityValidationError,
)
from material_workbench.contracts.decision_activity_contracts import (
    DecisionActivityAvailability,
    DecisionActivityRun,
    DecisionActivityRunRequest,
)
from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.persistence.store import ProjectNotFoundError, Store
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver
from material_workbench.tasks.task_registry import TaskRegistry


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
GraphDependency = Annotated[InferenceWorkGraph, Depends(get_inference_work_graph)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


def get_decision_activity_service(
    store: StoreDependency,
    registry: RegistryDependency,
    graph: GraphDependency,
    resolver: ResolverDependency,
) -> DecisionActivityService:
    return DecisionActivityService(store, registry, graph, resolver)


ServiceDependency = Annotated[DecisionActivityService, Depends(get_decision_activity_service)]
ACTIVITY_ERRORS = (
    ProjectNotFoundError,
    DecisionActivityNotFoundError,
    DecisionActivityValidationError,
) + CANDIDATE_APPLICATION_ERRORS


def _raise_activity_error(exc: Exception) -> None:
    if isinstance(exc, ProjectNotFoundError):
        raise HTTPException(404, "プロジェクトが見つかりません") from exc
    if isinstance(exc, DecisionActivityNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, DecisionActivityValidationError):
        raise HTTPException(422, str(exc)) from exc
    converted = candidate_http_error(exc)
    if converted is exc:
        raise exc
    raise converted from exc


@router.get(
    "/api/projects/{project_id}/decision-activities",
    response_model=list[DecisionActivityAvailability],
)
def list_decision_activities(
    project_id: str,
    service: ServiceDependency,
    candidate_id: str | None = Query(None),
    expected_revision: int | None = Query(None, ge=1),
) -> list[DecisionActivityAvailability]:
    try:
        return service.availability(project_id, candidate_id, expected_revision)
    except ACTIVITY_ERRORS as exc:
        _raise_activity_error(exc)


@router.post(
    "/api/projects/{project_id}/candidates/{candidate_id}/decision-activities/{activity_id}/runs",
    status_code=201,
    response_model=DecisionActivityRun,
)
def run_decision_activity(
    project_id: str,
    candidate_id: str,
    activity_id: str,
    payload: DecisionActivityRunRequest,
    service: ServiceDependency,
) -> DecisionActivityRun:
    try:
        return service.run(project_id, candidate_id, activity_id, payload)
    except ACTIVITY_ERRORS as exc:
        _raise_activity_error(exc)


@router.get(
    "/api/projects/{project_id}/decision-activity-runs",
    response_model=list[DecisionActivityRun],
)
def list_decision_activity_runs(
    project_id: str,
    service: ServiceDependency,
    candidate_id: str | None = Query(None),
) -> list[DecisionActivityRun]:
    try:
        return service.list_runs(project_id, candidate_id)
    except ACTIVITY_ERRORS as exc:
        _raise_activity_error(exc)


@router.get(
    "/api/projects/{project_id}/decision-activity-runs/{run_id}",
    response_model=DecisionActivityRun,
)
def get_decision_activity_run(
    project_id: str,
    run_id: str,
    service: ServiceDependency,
) -> DecisionActivityRun:
    try:
        return service.get_run(project_id, run_id)
    except ACTIVITY_ERRORS as exc:
        _raise_activity_error(exc)
