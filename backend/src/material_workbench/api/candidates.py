from __future__ import annotations

from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse

from .dependencies import (
    get_blend_contract_registry,
    get_project_runtime_resolver,
    get_store,
    get_task_registry,
)
from .errors import DomainApiException, PROJECT_API_ERRORS
from ..application.candidates import (
    CandidateNotFoundError,
    CandidateProjectKindError,
    CandidateProvenanceImmutableError,
    CandidateService,
    CandidateValidationError,
)
from material_workbench.contracts.schemas import (
    Candidate,
    CandidateCapacity,
    CandidateImportResponse,
    CandidateInput,
    CandidateUpdate,
)
from material_workbench.contracts.blend_contracts import (
    BlendContractRegistry,
    BlendMaterialDescriptor,
)
from material_workbench.domain.candidate_policy import MAX_CANDIDATES_PER_PROJECT
from material_workbench.persistence.store import (
    CandidateArchivedError,
    CandidateLimitError,
    CandidateRevisionConflictError,
    ProjectNotFoundError,
    Store,
    StoreDataIntegrityError,
)
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver


XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLSX_RESPONSE = {200: {"content": {XLSX_MEDIA_TYPE: {}}}}


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]
BlendContractRegistryDependency = Annotated[
    BlendContractRegistry, Depends(get_blend_contract_registry)
]


def get_candidate_service(
    request: Request,
    store: StoreDependency,
    registry: RegistryDependency,
    resolver: ResolverDependency,
    blend_contracts: BlendContractRegistryDependency,
) -> CandidateService:
    return CandidateService(
        store,
        registry,
        resolver,
        blend_contracts,
        request.app.state.deterministic_transform_catalog,
    )


CandidateServiceDependency = Annotated[CandidateService, Depends(get_candidate_service)]
CANDIDATE_APPLICATION_ERRORS = (
    ProjectNotFoundError,
    CandidateNotFoundError,
    CandidateProjectKindError,
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
    if isinstance(exc, CandidateProjectKindError):
        return DomainApiException(
            409,
            "chain_project_requires_chain_candidate_api",
            str(exc),
        )
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


@router.get(
    "/api/projects/{project_id}/candidate-capacity",
    response_model=CandidateCapacity,
    responses=PROJECT_API_ERRORS,
)
def get_candidate_capacity(
    project_id: str,
    service: CandidateServiceDependency,
) -> CandidateCapacity:
    try:
        used = len(service.list(project_id))
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)
    return CandidateCapacity(
        limit=MAX_CANDIDATES_PER_PROJECT,
        used=used,
        remaining=MAX_CANDIDATES_PER_PROJECT - used,
    )


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


@router.get("/api/projects/{project_id}/candidates/export.xlsx", response_class=Response, responses=XLSX_RESPONSE)
def export_candidates(project_id: str, service: CandidateServiceDependency) -> StreamingResponse:
    try:
        contents = service.export_xlsx(project_id)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)
    return StreamingResponse(BytesIO(contents), media_type=XLSX_MEDIA_TYPE, headers={"Content-Disposition": "attachment; filename=candidates-with-predictions.xlsx"})


@router.get("/api/projects/{project_id}/candidates/template.xlsx", response_class=Response, responses=XLSX_RESPONSE)
def candidate_import_template(project_id: str, service: CandidateServiceDependency) -> StreamingResponse:
    try:
        contents = service.template_xlsx(project_id)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)
    return StreamingResponse(BytesIO(contents), media_type=XLSX_MEDIA_TYPE, headers={"Content-Disposition": "attachment; filename=candidate-import-template.xlsx"})


@router.get("/api/projects/{project_id}/candidates/{candidate_id}", response_model=Candidate, responses=PROJECT_API_ERRORS)
def get_candidate(project_id: str, candidate_id: str, service: CandidateServiceDependency, include_archived: bool = False) -> Candidate:
    try:
        return service.get(project_id, candidate_id, include_archived=include_archived)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)


@router.get(
    "/api/projects/{project_id}/candidates/{candidate_id}/revisions/{revision}",
    response_model=Candidate,
    responses=PROJECT_API_ERRORS,
)
def get_candidate_revision(
    project_id: str,
    candidate_id: str,
    revision: int,
    service: CandidateServiceDependency,
) -> Candidate:
    try:
        return service.historical_revision(project_id, candidate_id, revision)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)


@router.get(
    "/api/projects/{project_id}/candidates/{candidate_id}/blend-materials",
    response_model=list[BlendMaterialDescriptor],
    responses=PROJECT_API_ERRORS,
)
def get_candidate_blend_materials(
    project_id: str,
    candidate_id: str,
    service: CandidateServiceDependency,
    revision: int | None = None,
) -> tuple[BlendMaterialDescriptor, ...]:
    try:
        return service.blend_materials(project_id, candidate_id, revision)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)


@router.get(
    "/api/projects/{project_id}/candidates/{candidate_id}/derivation-chain",
    response_model=list[Candidate],
    responses=PROJECT_API_ERRORS,
)
def get_candidate_derivation_chain(
    project_id: str,
    candidate_id: str,
    service: CandidateServiceDependency,
) -> list[Candidate]:
    try:
        return service.derivation_chain(project_id, candidate_id)
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
