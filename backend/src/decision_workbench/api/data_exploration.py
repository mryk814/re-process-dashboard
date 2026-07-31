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
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.prediction_catalog_contracts import CandidateOriginEvidence
from decision_workbench.contracts.evidence_contracts import (
    LineageIndexResponse,
    LineageNodeReview,
    LineageNodeReviewInput,
    LineageNodeReviewList,
)
from decision_workbench.contracts.data_exploration_contracts import (
    LineageResponse,
    QualityResponse,
)
from decision_workbench.persistence.store import ProjectNotFoundError, Store
from decision_workbench.tasks.task_registry import TaskRegistry
from decision_workbench.application.project_runtime import ProjectRuntimeResolver


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
    include_hidden: bool = False,
    limit: int = Query(default=200, ge=1, le=500),
) -> LineageIndexResponse:
    try:
        return service.lineage_index(
            project_id,
            query=query,
            entity_type=entity_type,
            issue_filter=issue_filter,
            include_hidden=include_hidden,
            limit=limit,
        )
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)


@router.get(
    "/api/projects/{project_id}/lineage-reviews",
    response_model=LineageNodeReviewList,
    responses=PROJECT_API_ERRORS,
)
def lineage_reviews(
    project_id: str,
    service: DataExplorationServiceDependency,
) -> LineageNodeReviewList:
    try:
        return service.lineage_reviews(project_id)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)


@router.get(
    "/api/projects/{project_id}/lineage-reviews/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {"schema": {"type": "string"}}}}},
)
def export_lineage_reviews(
    project_id: str,
    service: DataExplorationServiceDependency,
) -> Response:
    try:
        contents = service.lineage_reviews_csv(project_id)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)
    return Response(
        content=contents,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=lineage-node-reviews.csv"},
    )


@router.put(
    "/api/projects/{project_id}/lineage-reviews/{entity_key}",
    response_model=LineageNodeReview,
    responses=PROJECT_API_ERRORS,
)
def save_lineage_review(
    project_id: str,
    entity_key: str,
    payload: LineageNodeReviewInput,
    service: DataExplorationServiceDependency,
) -> LineageNodeReview:
    try:
        return service.save_lineage_review(project_id, entity_key, payload)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)


@router.delete(
    "/api/projects/{project_id}/lineage-reviews/{entity_key}",
    status_code=204,
    responses=PROJECT_API_ERRORS,
)
def delete_lineage_review(
    project_id: str,
    entity_key: str,
    service: DataExplorationServiceDependency,
) -> Response:
    try:
        deleted = service.delete_lineage_review(project_id, entity_key)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)
    if not deleted:
        raise HTTPException(404, "確認メモが見つかりません")
    return Response(status_code=204)


@router.get("/api/projects/{project_id}/lineage/{entity_key}", response_model=LineageResponse, responses=PROJECT_API_ERRORS)
def lineage(
    project_id: str,
    entity_key: str,
    service: DataExplorationServiceDependency,
    limit: int = Query(default=40, ge=1, le=200),
    all_reachable: bool = Query(default=False),
) -> LineageResponse:
    try:
        return service.lineage(
            project_id,
            entity_key,
            limit=limit,
            all_reachable=all_reachable,
        )
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)


@router.get(
    "/api/projects/{project_id}/candidates/{candidate_id}/origin-evidence",
    response_model=CandidateOriginEvidence,
    responses=PROJECT_API_ERRORS,
)
def candidate_origin_evidence(
    project_id: str,
    candidate_id: str,
    service: DataExplorationServiceDependency,
) -> CandidateOriginEvidence:
    try:
        return service.candidate_origin_evidence(project_id, candidate_id)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)


@router.get(
    "/api/projects/{project_id}/lineage/{entity_key}/evidence-image",
    response_class=Response,
    operation_id="getLineageEvidenceImage",
    responses={200: {"content": {"image/png": {}}}, **PROJECT_API_ERRORS},
)
def lineage_evidence_image(
    project_id: str,
    entity_key: str,
    service: DataExplorationServiceDependency,
) -> Response:
    """観測が参照している顕微鏡写真。データセット配下の画像だけを返す。"""

    try:
        payload, media_type = service.lineage_evidence_image(project_id, entity_key)
    except DATA_EXPLORATION_ERRORS as exc:
        _raise_data_error(exc)
    return Response(
        content=payload,
        media_type=media_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


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
