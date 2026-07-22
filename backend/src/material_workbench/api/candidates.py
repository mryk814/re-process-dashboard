from __future__ import annotations

from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse

from .dependencies import get_store, get_task_registry
from .errors import DomainApiException, PROJECT_API_ERRORS
from ..application.candidates import (
    CandidateNotFoundError,
    CandidateProvenanceImmutableError,
    CandidateService,
    CandidateValidationError,
)
from ..schemas import Candidate, CandidateImportResponse, CandidateInput, CandidateUpdate
from ..store import (
    CandidateArchivedError,
    CandidateLimitError,
    CandidateRevisionConflictError,
    ProjectNotFoundError,
    Store,
    StoreDataIntegrityError,
)
from ..task_registry import TaskRegistry


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]


def get_candidate_service(store: StoreDependency, registry: RegistryDependency) -> CandidateService:
    return CandidateService(store, registry)


CandidateServiceDependency = Annotated[CandidateService, Depends(get_candidate_service)]
CANDIDATE_APPLICATION_ERRORS = (
    ProjectNotFoundError,
    CandidateNotFoundError,
    CandidateValidationError,
    CandidateLimitError,
    CandidateRevisionConflictError,
    CandidateArchivedError,
    CandidateProvenanceImmutableError,
    StoreDataIntegrityError,
)


def candidate_http_error(exc: Exception) -> Exception:
    if isinstance(exc, ProjectNotFoundError):
        return HTTPException(404, "プロジェクトが見つかりません")
    if isinstance(exc, CandidateNotFoundError):
        return HTTPException(404, "候補が見つかりません")
    if isinstance(exc, CandidateValidationError):
        return HTTPException(422, str(exc))
    if isinstance(exc, CandidateLimitError):
        return DomainApiException(409, "candidate_limit", str(exc))
    if isinstance(exc, CandidateRevisionConflictError):
        return DomainApiException(409, "revision_conflict", str(exc), current_candidate=exc.current)
    if isinstance(exc, CandidateArchivedError):
        return DomainApiException(409, "candidate_archived", str(exc))
    if isinstance(exc, CandidateProvenanceImmutableError):
        return DomainApiException(409, "candidate_provenance_immutable", str(exc))
    if isinstance(exc, StoreDataIntegrityError):
        return DomainApiException(409, "data_integrity_error", str(exc))
    return exc


def raise_candidate_http_error(exc: Exception) -> None:
    converted = candidate_http_error(exc)
    if converted is exc:
        raise exc
    raise converted from exc


@router.get("/api/projects/{project_id}/candidates", response_model=list[Candidate], responses=PROJECT_API_ERRORS)
def list_candidates(project_id: str, service: CandidateServiceDependency, include_archived: bool = False) -> list[Candidate]:
    try:
        return service.list(project_id, include_archived=include_archived)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)


@router.post("/api/projects/{project_id}/candidates", status_code=201, response_model=Candidate, responses=PROJECT_API_ERRORS)
def create_candidate(project_id: str, payload: CandidateInput, service: CandidateServiceDependency) -> Candidate:
    try:
        return service.create(project_id, payload)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)


@router.post("/api/projects/{project_id}/candidates/import", response_model=CandidateImportResponse, responses=PROJECT_API_ERRORS)
async def import_candidates(project_id: str, service: CandidateServiceDependency, file: UploadFile = File(...)) -> CandidateImportResponse:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(422, "Excel .xlsx ファイルを選択してください")
    try:
        return service.import_xlsx(project_id, await file.read())
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)


@router.get("/api/projects/{project_id}/candidates/export.xlsx")
def export_candidates(project_id: str, service: CandidateServiceDependency) -> StreamingResponse:
    try:
        contents = service.export_xlsx(project_id)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)
    return StreamingResponse(BytesIO(contents), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=candidates-with-predictions.xlsx"})


@router.get("/api/projects/{project_id}/candidates/{candidate_id}", response_model=Candidate, responses=PROJECT_API_ERRORS)
def get_candidate(project_id: str, candidate_id: str, service: CandidateServiceDependency, include_archived: bool = False) -> Candidate:
    try:
        return service.get(project_id, candidate_id, include_archived=include_archived)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)


@router.put("/api/projects/{project_id}/candidates/{candidate_id}", response_model=Candidate, responses=PROJECT_API_ERRORS)
def update_candidate(project_id: str, candidate_id: str, payload: CandidateUpdate, service: CandidateServiceDependency) -> Candidate:
    try:
        return service.update(project_id, candidate_id, payload)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)


@router.delete("/api/projects/{project_id}/candidates/{candidate_id}", status_code=204, responses=PROJECT_API_ERRORS)
def delete_candidate(project_id: str, candidate_id: str, expected_revision: int, service: CandidateServiceDependency) -> Response:
    try:
        service.delete(project_id, candidate_id, expected_revision)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)
    return Response(status_code=204)
