from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from decision_workbench.api.dependencies import (
    get_ai_review_provider,
    get_store,
)
from decision_workbench.api.errors import DomainApiException, PROJECT_API_ERRORS
from decision_workbench.application.ai_review_provider import AiReviewProvider
from decision_workbench.application.ai_reviews import (
    AiReviewNotFoundError,
    AiReviewService,
    AiReviewUnavailableError,
    AiReviewValidationError,
    HUMAN_ACTOR_ID_PATTERN,
)
from decision_workbench.application.ai_review_tools import AiReviewToolError
from decision_workbench.contracts.ai_review_contracts import (
    AiReviewAvailability,
    AiReviewDisposition,
    AiReviewDispositionInput,
    AiReviewRun,
    AiReviewRunRequest,
)
from decision_workbench.persistence.store import (
    CandidateRevisionConflictError,
    ProjectNotFoundError,
    Store,
)


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
ProviderDependency = Annotated[AiReviewProvider | None, Depends(get_ai_review_provider)]


def get_ai_review_service(
    store: StoreDependency,
    provider: ProviderDependency,
) -> AiReviewService:
    return AiReviewService(store, provider)


ServiceDependency = Annotated[AiReviewService, Depends(get_ai_review_service)]


def _raise_ai_review_error(exc: Exception) -> None:
    if isinstance(exc, ProjectNotFoundError):
        raise HTTPException(404, "プロジェクトが見つかりません") from exc
    if isinstance(exc, AiReviewNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, AiReviewUnavailableError):
        raise HTTPException(503, str(exc)) from exc
    if isinstance(exc, CandidateRevisionConflictError):
        raise DomainApiException(
            409, "candidate_revision_conflict", "候補revisionが更新されています"
        ) from exc
    if isinstance(exc, (AiReviewValidationError, AiReviewToolError)):
        raise HTTPException(422, str(exc)) from exc
    raise exc


@router.get(
    "/api/ai-review/availability",
    response_model=AiReviewAvailability,
)
def ai_review_availability(service: ServiceDependency) -> AiReviewAvailability:
    return service.availability()


@router.post(
    "/api/projects/{project_id}/candidates/{candidate_id}/ai-review-runs",
    status_code=201,
    response_model=AiReviewRun,
    responses=PROJECT_API_ERRORS,
)
def run_ai_candidate_review(
    project_id: str,
    candidate_id: str,
    payload: AiReviewRunRequest,
    service: ServiceDependency,
) -> AiReviewRun:
    try:
        return service.run_candidate_review(
            project_id, candidate_id, payload.expected_revision
        )
    except (
        ProjectNotFoundError,
        AiReviewUnavailableError,
        AiReviewValidationError,
        AiReviewToolError,
        CandidateRevisionConflictError,
    ) as exc:
        _raise_ai_review_error(exc)


@router.get(
    "/api/projects/{project_id}/ai-review-runs",
    response_model=list[AiReviewRun],
    responses=PROJECT_API_ERRORS,
)
def list_ai_review_runs(
    project_id: str,
    service: ServiceDependency,
    candidate_id: str | None = Query(None),
) -> list[AiReviewRun]:
    try:
        return service.list_runs(project_id, candidate_id)
    except (ProjectNotFoundError, AiReviewValidationError) as exc:
        _raise_ai_review_error(exc)


@router.get(
    "/api/projects/{project_id}/ai-review-runs/{review_run_id}",
    response_model=AiReviewRun,
    responses=PROJECT_API_ERRORS,
)
def get_ai_review_run(
    project_id: str,
    review_run_id: str,
    service: ServiceDependency,
) -> AiReviewRun:
    try:
        return service.get_run(project_id, review_run_id)
    except (ProjectNotFoundError, AiReviewNotFoundError) as exc:
        _raise_ai_review_error(exc)


@router.post(
    "/api/projects/{project_id}/ai-review-runs/{review_run_id}/dispositions",
    status_code=201,
    response_model=AiReviewDisposition,
    responses=PROJECT_API_ERRORS,
)
def record_ai_review_disposition(
    project_id: str,
    review_run_id: str,
    payload: AiReviewDispositionInput,
    service: ServiceDependency,
    human_actor_id: Annotated[
        str,
        Header(
            alias="X-Workbench-Human-Actor",
            min_length=1,
            max_length=128,
            pattern=HUMAN_ACTOR_ID_PATTERN,
            description=(
                "Development attribution identifier supplied by the trusted local "
                "application boundary. This header is not authentication."
            ),
        ),
    ],
) -> AiReviewDisposition:
    try:
        return service.record_disposition(
            project_id,
            review_run_id,
            payload,
            human_actor_id=human_actor_id,
        )
    except (
        ProjectNotFoundError,
        AiReviewNotFoundError,
        AiReviewValidationError,
    ) as exc:
        _raise_ai_review_error(exc)


@router.get(
    "/api/projects/{project_id}/ai-review-runs/{review_run_id}/dispositions",
    response_model=list[AiReviewDisposition],
    responses=PROJECT_API_ERRORS,
)
def list_ai_review_dispositions(
    project_id: str,
    review_run_id: str,
    service: ServiceDependency,
) -> list[AiReviewDisposition]:
    try:
        return service.dispositions(project_id, review_run_id)
    except (ProjectNotFoundError, AiReviewNotFoundError) as exc:
        _raise_ai_review_error(exc)
