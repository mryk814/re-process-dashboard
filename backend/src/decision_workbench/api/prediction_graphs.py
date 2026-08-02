"""Dedicated transport for Prediction Graph v1 projects and evidence."""
from __future__ import annotations

from typing import Annotated, Callable, TypeVar

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from decision_workbench.application.chains import (
    ChainCandidateRevisionError,
    ChainConflictError,
    ChainNotFoundError,
    ChainValidationError,
)
from decision_workbench.application.prediction_graphs import (
    PredictionGraphUseCases,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    CandidateUpdate,
    Project,
)
from decision_workbench.contracts.chain_api_contracts import (
    ChainExecutionRequest,
    PredictionGraphCatalogResponse,
    PredictionGraphDraftValidation,
    PredictionGraphDraftValidationRequest,
    PredictionGraphPublishRequest,
    PredictionGraphPublishResponse,
    PredictionGraphProjectCreateRequest,
)
from decision_workbench.contracts.chain_execution_contracts import (
    PredictionGraphExecution,
    PredictionGraphSnapshot,
)
from decision_workbench.contracts.prediction_graph_draft_contracts import (
    PredictionGraphDraftConflictResponse,
    PredictionGraphDraftCreateRequest,
    PredictionGraphDraftDocument,
    PredictionGraphDraftUpdateRequest,
)
from decision_workbench.contracts.evidence_contracts import ApiError
from decision_workbench.persistence.prediction_graph_draft_repository import (
    PredictionGraphDraftConflictError,
    PredictionGraphDraftNotFoundError,
)

from .dependencies import get_prediction_graph_use_cases


router = APIRouter(prefix="/api/prediction-graphs", tags=["prediction-graphs"])
draft_router = APIRouter(
    prefix="/api/prediction-graph-drafts",
    tags=["prediction-graph-drafts"],
)
GraphDependency = Annotated[
    PredictionGraphUseCases,
    Depends(get_prediction_graph_use_cases),
]
T = TypeVar("T")


def _call(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except ChainCandidateRevisionError as exc:
        raise HTTPException(
            409,
            {
                "message": str(exc),
                "current": exc.current.model_dump(mode="json"),
            },
        ) from exc
    except ChainNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ChainValidationError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ChainConflictError as exc:
        raise HTTPException(409, str(exc)) from exc


def _call_draft(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except PredictionGraphDraftNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


DRAFT_NOT_FOUND_RESPONSE = {
    404: {"model": ApiError, "description": "Prediction Graph Draft Not Found"},
}
DRAFT_UPDATE_RESPONSES = {
    **DRAFT_NOT_FOUND_RESPONSE,
    409: {
        "model": PredictionGraphDraftConflictResponse,
        "description": "Prediction Graph Draft Revision Conflict",
    },
}


@draft_router.post(
    "",
    response_model=PredictionGraphDraftDocument,
    status_code=201,
    operation_id="createPredictionGraphDraft",
)
def create_draft(
    payload: PredictionGraphDraftCreateRequest,
    use_cases: GraphDependency,
) -> PredictionGraphDraftDocument:
    return _call_draft(lambda: use_cases.create_draft(payload))


@draft_router.get(
    "/{draft_id}",
    response_model=PredictionGraphDraftDocument,
    responses=DRAFT_NOT_FOUND_RESPONSE,
    operation_id="getPredictionGraphDraft",
)
def get_draft(
    draft_id: str,
    use_cases: GraphDependency,
) -> PredictionGraphDraftDocument:
    return _call_draft(lambda: use_cases.get_draft(draft_id))


@draft_router.put(
    "/{draft_id}",
    response_model=PredictionGraphDraftDocument,
    responses=DRAFT_UPDATE_RESPONSES,
    operation_id="updatePredictionGraphDraft",
)
def update_draft(
    draft_id: str,
    payload: PredictionGraphDraftUpdateRequest,
    use_cases: GraphDependency,
) -> PredictionGraphDraftDocument | JSONResponse:
    try:
        return _call_draft(lambda: use_cases.update_draft(draft_id, payload))
    except PredictionGraphDraftConflictError as exc:
        conflict = PredictionGraphDraftConflictResponse(
            message=str(exc),
            current=exc.current,
        )
        return JSONResponse(
            status_code=409,
            content=conflict.model_dump(mode="json"),
        )


@router.get(
    "/catalog",
    response_model=PredictionGraphCatalogResponse,
    operation_id="getPredictionGraphCatalog",
)
def catalog(
    use_cases: GraphDependency,
) -> PredictionGraphCatalogResponse:
    return _call(use_cases.catalog)


@router.post(
    "/validate",
    response_model=PredictionGraphDraftValidation,
    operation_id="validatePredictionGraph",
)
def validate(
    payload: PredictionGraphDraftValidationRequest,
    use_cases: GraphDependency,
) -> PredictionGraphDraftValidation:
    return _call(lambda: use_cases.validate(payload))


@router.post(
    "/projects",
    response_model=Project,
    status_code=201,
    operation_id="createPredictionGraphProject",
)
def create_project(
    payload: PredictionGraphProjectCreateRequest,
    use_cases: GraphDependency,
) -> Project:
    return _call(lambda: use_cases.create_project(payload))


@router.post(
    "/publish",
    response_model=PredictionGraphPublishResponse,
    status_code=201,
    operation_id="publishPredictionGraph",
)
def publish(
    payload: PredictionGraphPublishRequest,
    use_cases: GraphDependency,
) -> PredictionGraphPublishResponse:
    return _call(lambda: use_cases.publish(payload))


@router.post(
    "/projects/{project_id}/candidates",
    response_model=Candidate,
    status_code=201,
    operation_id="createPredictionGraphCandidate",
)
def create_candidate(
    project_id: str,
    payload: CandidateInput,
    use_cases: GraphDependency,
) -> Candidate:
    return _call(lambda: use_cases.create_candidate(project_id, payload))


@router.put(
    "/projects/{project_id}/candidates/{candidate_id}",
    response_model=Candidate,
    operation_id="updatePredictionGraphCandidate",
)
def update_candidate(
    project_id: str,
    candidate_id: str,
    payload: CandidateUpdate,
    use_cases: GraphDependency,
) -> Candidate:
    return _call(
        lambda: use_cases.update_candidate(project_id, candidate_id, payload)
    )


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/executions",
    response_model=PredictionGraphExecution,
    operation_id="executePredictionGraph",
)
def execute(
    project_id: str,
    candidate_id: str,
    payload: ChainExecutionRequest,
    use_cases: GraphDependency,
) -> PredictionGraphExecution:
    return _call(lambda: use_cases.execute(project_id, candidate_id, payload))


@router.get(
    "/projects/{project_id}/candidates/{candidate_id}/execution",
    response_model=PredictionGraphExecution,
    operation_id="getPredictionGraphExecution",
)
def latest_execution(
    project_id: str,
    candidate_id: str,
    use_cases: GraphDependency,
) -> PredictionGraphExecution:
    return _call(lambda: use_cases.latest_execution(project_id, candidate_id))


@router.post(
    "/projects/{project_id}/candidates/{candidate_id}/snapshots",
    response_model=PredictionGraphSnapshot,
    status_code=201,
    operation_id="createPredictionGraphSnapshot",
)
def create_snapshot(
    project_id: str,
    candidate_id: str,
    payload: ChainExecutionRequest,
    use_cases: GraphDependency,
) -> PredictionGraphSnapshot:
    return _call(
        lambda: use_cases.create_snapshot(project_id, candidate_id, payload)
    )


@router.get(
    "/projects/{project_id}/candidates/{candidate_id}/snapshots",
    response_model=list[PredictionGraphSnapshot],
    operation_id="listPredictionGraphSnapshots",
)
def list_snapshots(
    project_id: str,
    candidate_id: str,
    use_cases: GraphDependency,
) -> list[PredictionGraphSnapshot]:
    return _call(lambda: use_cases.list_snapshots(project_id, candidate_id))


@router.get(
    "/projects/{project_id}/snapshots/{snapshot_id}",
    response_model=PredictionGraphSnapshot,
    operation_id="getPredictionGraphSnapshot",
)
def get_snapshot(
    project_id: str,
    snapshot_id: str,
    use_cases: GraphDependency,
) -> PredictionGraphSnapshot:
    return _call(lambda: use_cases.snapshot(project_id, snapshot_id))
