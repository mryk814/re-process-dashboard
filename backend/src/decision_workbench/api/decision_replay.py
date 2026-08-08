from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from decision_workbench.api.dependencies import (
    get_inference_work_graph,
    get_project_runtime_resolver,
    get_store,
    get_task_registry,
)
from decision_workbench.application.decision_replay import (
    DecisionReplayNotFoundError,
    DecisionReplayService,
    DecisionReplayValidationError,
)
from decision_workbench.application.inference import InferenceService
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.contracts.decision_replay_contracts import (
    DecisionCase,
    DecisionCaseActualAttachment,
    DecisionCaseActualAttachmentCreateRequest,
    DecisionCaseCreateRequest,
    DecisionCaseDraftContext,
    DecisionReplayRequest,
    DecisionReplayRun,
    HindsightProjectOption,
)
from decision_workbench.execution.inference_work_graph import InferenceWorkGraph
from decision_workbench.persistence.store import ProjectNotFoundError, Store
from decision_workbench.tasks.task_registry import TaskRegistry


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
GraphDependency = Annotated[InferenceWorkGraph, Depends(get_inference_work_graph)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


def get_decision_replay_service(
    store: StoreDependency,
    registry: RegistryDependency,
    graph: GraphDependency,
    resolver: ResolverDependency,
) -> DecisionReplayService:
    return DecisionReplayService(
        store,
        registry,
        InferenceService(store, registry, graph, resolver),
    )


ServiceDependency = Annotated[DecisionReplayService, Depends(get_decision_replay_service)]


def _raise_error(exc: Exception) -> None:
    if isinstance(exc, ProjectNotFoundError):
        raise HTTPException(404, "プロジェクトが見つかりません") from exc
    if isinstance(exc, DecisionReplayNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, DecisionReplayValidationError):
        raise HTTPException(422, str(exc)) from exc
    raise exc


@router.post(
    "/api/projects/{project_id}/decision-cases",
    status_code=201,
    response_model=DecisionCase,
    operation_id="createDecisionCase",
)
def create_decision_case(
    project_id: str,
    payload: DecisionCaseCreateRequest,
    service: ServiceDependency,
    human_actor_id: Annotated[
        str | None,
        Header(
            alias="X-Workbench-Human-Actor",
            min_length=1,
            max_length=128,
            description=(
                "Trusted local attribution identifier. This header is not authentication."
            ),
        ),
    ] = None,
) -> DecisionCase:
    try:
        return service.create_case(
            project_id, payload, human_actor_id=human_actor_id
        )
    except (ProjectNotFoundError, DecisionReplayValidationError) as exc:
        _raise_error(exc)


@router.get(
    "/api/projects/{project_id}/decision-case-draft-context",
    response_model=DecisionCaseDraftContext,
    operation_id="getDecisionCaseDraftContext",
)
def get_decision_case_draft_context(
    project_id: str, service: ServiceDependency
) -> DecisionCaseDraftContext:
    try:
        return service.draft_context(project_id)
    except ProjectNotFoundError as exc:
        _raise_error(exc)


@router.get(
    "/api/projects/{project_id}/decision-cases",
    response_model=list[DecisionCase],
    operation_id="listDecisionCases",
)
def list_decision_cases(
    project_id: str, service: ServiceDependency
) -> list[DecisionCase]:
    try:
        return service.list_cases(project_id)
    except ProjectNotFoundError as exc:
        _raise_error(exc)


@router.get(
    "/api/projects/{project_id}/decision-cases/{case_id}",
    response_model=DecisionCase,
    operation_id="getDecisionCase",
)
def get_decision_case(
    project_id: str, case_id: str, service: ServiceDependency
) -> DecisionCase:
    try:
        return service.get_case(project_id, case_id)
    except (ProjectNotFoundError, DecisionReplayNotFoundError) as exc:
        _raise_error(exc)


@router.post(
    "/api/projects/{project_id}/decision-cases/{case_id}/actual-attachments",
    status_code=201,
    response_model=DecisionCaseActualAttachment,
    operation_id="attachDecisionCaseActual",
)
def attach_decision_case_actual(
    project_id: str,
    case_id: str,
    payload: DecisionCaseActualAttachmentCreateRequest,
    service: ServiceDependency,
) -> DecisionCaseActualAttachment:
    try:
        return service.attach_actual(project_id, case_id, payload)
    except (
        ProjectNotFoundError,
        DecisionReplayNotFoundError,
        DecisionReplayValidationError,
    ) as exc:
        _raise_error(exc)


@router.get(
    "/api/projects/{project_id}/decision-cases/{case_id}/actual-attachments",
    response_model=list[DecisionCaseActualAttachment],
    operation_id="listDecisionCaseActualAttachments",
)
def list_decision_case_actual_attachments(
    project_id: str, case_id: str, service: ServiceDependency
) -> list[DecisionCaseActualAttachment]:
    try:
        return service.list_actual_attachments(project_id, case_id)
    except (ProjectNotFoundError, DecisionReplayNotFoundError) as exc:
        _raise_error(exc)


@router.get(
    "/api/projects/{project_id}/decision-cases/{case_id}/hindsight-project-options",
    response_model=list[HindsightProjectOption],
    operation_id="listDecisionReplayHindsightProjectOptions",
)
def list_hindsight_project_options(
    project_id: str, case_id: str, service: ServiceDependency
) -> list[HindsightProjectOption]:
    try:
        return service.hindsight_project_options(project_id, case_id)
    except (ProjectNotFoundError, DecisionReplayNotFoundError) as exc:
        _raise_error(exc)


@router.post(
    "/api/projects/{project_id}/decision-cases/{case_id}/replay-runs",
    status_code=201,
    response_model=DecisionReplayRun,
    operation_id="runDecisionReplay",
)
def run_decision_replay(
    project_id: str,
    case_id: str,
    payload: DecisionReplayRequest,
    service: ServiceDependency,
) -> DecisionReplayRun:
    try:
        return service.run(project_id, case_id, payload)
    except (
        ProjectNotFoundError,
        DecisionReplayNotFoundError,
        DecisionReplayValidationError,
    ) as exc:
        _raise_error(exc)


@router.get(
    "/api/projects/{project_id}/decision-replay-runs",
    response_model=list[DecisionReplayRun],
    operation_id="listDecisionReplayRuns",
)
def list_decision_replay_runs(
    project_id: str,
    service: ServiceDependency,
    case_id: str | None = Query(None),
) -> list[DecisionReplayRun]:
    try:
        return service.list_runs(project_id, case_id)
    except ProjectNotFoundError as exc:
        _raise_error(exc)
