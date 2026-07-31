from collections.abc import Callable
import re
from typing import Annotated, Any
from uuid import uuid4

from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    Query,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg import Error as PsycopgError
from psycopg.errors import UniqueViolation

from decision_workbench.shared_lab.config import SharedLabConfig
from decision_workbench.shared_lab.contracts import (
    ActivityRun,
    ActivityRunCreate,
    ApiError,
    ArtifactReference,
    AuditEvent,
    CandidateCreate,
    CandidateRevision,
    CandidateUpdate,
    Identifier,
    Project,
    ProjectCreate,
    SharedContext,
)
from decision_workbench.shared_lab.repository import (
    RequestIdentity,
    SharedCapabilityDenied,
    SharedIdentityInvalid,
    SharedLabError,
    SharedLabRepository,
    SharedResourceNotFound,
    SharedRevisionConflict,
)
from decision_workbench.shared_lab.service import (
    ArtifactDigestMismatch,
    ArtifactRegistrationFailed,
    ArtifactService,
    ArtifactTooLarge,
    ObjectStorageUnavailable,
)


HEADER_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,119}$")
ERROR_RESPONSES = {
    400: {"model": ApiError},
    403: {"model": ApiError},
    404: {"model": ApiError},
    409: {"model": ApiError},
    503: {"model": ApiError},
}


class IdentityHeaderError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _error(
    status: int,
    code: str,
    message: str,
    *,
    current: CandidateRevision | None = None,
) -> JSONResponse:
    payload: dict[str, Any] = {"code": code, "message": message}
    if current is not None:
        payload["current_candidate"] = current.model_dump(mode="json")
    return JSONResponse(status_code=status, content=payload)


def create_shared_lab_app(
    config: SharedLabConfig | None = None,
    *,
    repository_factory: Callable[[SharedLabConfig], SharedLabRepository] | None = None,
    artifact_service_factory: Callable[
        [SharedLabConfig, SharedLabRepository], ArtifactService
    ]
    | None = None,
) -> FastAPI:
    """Create the explicit shared-mode app without changing the local app."""

    resolved_config = config or SharedLabConfig.from_env()
    repository = (repository_factory or SharedLabRepository)(resolved_config)
    artifact_service = (artifact_service_factory or ArtifactService)(
        resolved_config, repository
    )
    app = FastAPI(
        title="Evidence Decision Workbench Shared Lab",
        version="1.0.0-experimental",
    )
    app.state.shared_lab_config = resolved_config
    app.state.shared_lab_repository = repository
    app.state.shared_lab_artifacts = artifact_service

    def identity(
        workspace_id: Annotated[
            str | None, Header(alias="X-Workbench-Workspace")
        ] = None,
        actor_id: Annotated[str | None, Header(alias="X-Workbench-Actor")] = None,
        request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
        correlation_id: Annotated[
            str | None, Header(alias="X-Correlation-ID")
        ] = None,
    ) -> RequestIdentity:
        if not workspace_id or not actor_id:
            raise IdentityHeaderError(
                "identity_missing",
                "X-Workbench-Workspace and X-Workbench-Actor are required",
            )
        resolved_request_id = request_id or f"request-{uuid4().hex}"
        resolved_correlation_id = correlation_id or resolved_request_id
        if not all(
            HEADER_ID.fullmatch(value)
            for value in (
                workspace_id,
                actor_id,
                resolved_request_id,
                resolved_correlation_id,
            )
        ):
            raise IdentityHeaderError(
                "identity_invalid",
                "shared request identity contains an invalid identifier",
            )
        return repository.resolve_identity(
            workspace_id,
            actor_id,
            resolved_request_id,
            resolved_correlation_id,
        )

    Identity = Annotated[RequestIdentity, Depends(identity)]

    @app.exception_handler(IdentityHeaderError)
    async def identity_header_error(_request: Any, exc: IdentityHeaderError):
        return _error(400, exc.code, str(exc))

    @app.exception_handler(SharedResourceNotFound)
    async def resource_not_found(_request: Any, _exc: SharedResourceNotFound):
        return _error(404, "not_found", "the requested shared resource was not found")

    @app.exception_handler(SharedIdentityInvalid)
    async def invalid_identity(_request: Any, _exc: SharedIdentityInvalid):
        return _error(
            403,
            "identity_invalid",
            "the workspace or actor is not enabled for this shared lab",
        )

    @app.exception_handler(SharedCapabilityDenied)
    async def capability_denied(_request: Any, _exc: SharedCapabilityDenied):
        return _error(
            403,
            "capability_denied",
            "the current actor cannot perform this shared operation",
        )

    @app.exception_handler(SharedRevisionConflict)
    async def revision_conflict(_request: Any, exc: SharedRevisionConflict):
        return _error(
            409,
            "revision_conflict",
            "the candidate was updated by another actor",
            current=exc.current,
        )

    @app.exception_handler(UniqueViolation)
    async def unique_conflict(_request: Any, _exc: UniqueViolation):
        return _error(
            409,
            "resource_conflict",
            "a shared resource with this identifier already exists",
        )

    @app.exception_handler(ArtifactDigestMismatch)
    async def digest_mismatch(_request: Any, _exc: ArtifactDigestMismatch):
        return _error(
            409,
            "artifact_digest_mismatch",
            "the stored artifact does not match its registered digest",
        )

    @app.exception_handler(ArtifactTooLarge)
    async def artifact_too_large(_request: Any, exc: ArtifactTooLarge):
        return _error(400, "validation_error", str(exc))

    @app.exception_handler(ObjectStorageUnavailable)
    async def storage_unavailable(_request: Any, _exc: ObjectStorageUnavailable):
        return _error(
            503,
            "object_storage_unavailable",
            "object storage is unavailable; no artifact metadata was committed",
        )

    @app.exception_handler(ArtifactRegistrationFailed)
    async def registration_failed(_request: Any, _exc: ArtifactRegistrationFailed):
        return _error(
            503,
            "object_storage_unavailable",
            "artifact registration failed and storage cleanup is required",
        )

    @app.exception_handler(PsycopgError)
    async def persistence_unavailable(_request: Any, _exc: PsycopgError):
        return _error(
            503,
            "persistence_unavailable",
            "shared persistence is unavailable",
        )

    @app.exception_handler(SharedLabError)
    async def shared_failure(_request: Any, _exc: SharedLabError):
        return _error(
            503,
            "persistence_unavailable",
            "the shared operation could not be completed",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_failure(_request: Any, _exc: RequestValidationError):
        return _error(400, "validation_error", "the shared request is invalid")

    @app.get("/api/shared/context", response_model=SharedContext)
    def context(current: Identity) -> SharedContext:
        return SharedContext(
            workspace_id=current.workspace_id,
            actor=current.actor,
            request_id=current.request_id,
        )

    @app.post(
        "/api/shared/projects",
        response_model=Project,
        status_code=201,
        responses=ERROR_RESPONSES,
    )
    def create_project(payload: ProjectCreate, current: Identity) -> Project:
        return repository.create_project(current, payload)

    @app.get("/api/shared/projects", response_model=list[Project])
    def list_projects(current: Identity) -> list[Project]:
        return repository.list_projects(current)

    @app.get(
        "/api/shared/projects/{project_id}",
        response_model=Project,
        responses=ERROR_RESPONSES,
    )
    def get_project(project_id: Identifier, current: Identity) -> Project:
        return repository.get_project(current, project_id)

    @app.post(
        "/api/shared/projects/{project_id}/candidates",
        response_model=CandidateRevision,
        status_code=201,
        responses=ERROR_RESPONSES,
    )
    def create_candidate(
        project_id: Identifier, payload: CandidateCreate, current: Identity
    ) -> CandidateRevision:
        return repository.create_candidate(current, project_id, payload)

    @app.get(
        "/api/shared/projects/{project_id}/candidates/{candidate_id}",
        response_model=CandidateRevision,
        responses=ERROR_RESPONSES,
    )
    def get_candidate(
        project_id: Identifier, candidate_id: Identifier, current: Identity
    ) -> CandidateRevision:
        return repository.get_candidate(current, project_id, candidate_id)

    @app.put(
        "/api/shared/projects/{project_id}/candidates/{candidate_id}",
        response_model=CandidateRevision,
        responses=ERROR_RESPONSES,
    )
    def update_candidate(
        project_id: Identifier,
        candidate_id: Identifier,
        payload: CandidateUpdate,
        current: Identity,
    ) -> CandidateRevision:
        return repository.update_candidate(
            current, project_id, candidate_id, payload
        )

    @app.get(
        "/api/shared/projects/{project_id}/candidates/{candidate_id}/revisions",
        response_model=list[CandidateRevision],
        responses=ERROR_RESPONSES,
    )
    def candidate_history(
        project_id: Identifier, candidate_id: Identifier, current: Identity
    ) -> list[CandidateRevision]:
        return repository.list_candidate_history(
            current, project_id, candidate_id
        )

    @app.post(
        "/api/shared/projects/{project_id}/runs",
        response_model=ActivityRun,
        status_code=201,
        responses=ERROR_RESPONSES,
    )
    def create_run(
        project_id: Identifier, payload: ActivityRunCreate, current: Identity
    ) -> ActivityRun:
        return repository.create_run(current, project_id, payload)

    @app.get(
        "/api/shared/projects/{project_id}/runs",
        response_model=list[ActivityRun],
    )
    def list_runs(project_id: Identifier, current: Identity) -> list[ActivityRun]:
        return repository.list_runs(current, project_id)

    @app.post(
        "/api/shared/projects/{project_id}/artifacts/{artifact_id}",
        response_model=ArtifactReference,
        status_code=201,
        responses=ERROR_RESPONSES,
    )
    async def put_artifact(
        project_id: Identifier,
        artifact_id: Identifier,
        current: Identity,
        upload: Annotated[UploadFile, File()],
    ) -> ArtifactReference:
        content = await upload.read(resolved_config.max_artifact_bytes + 1)
        return artifact_service.put_and_register(
            current,
            artifact_id=artifact_id,
            project_id=project_id,
            content=content,
            content_type=upload.content_type or "application/octet-stream",
            metadata={"filename": upload.filename or ""},
        )

    @app.get(
        "/api/shared/artifacts/{artifact_id}",
        responses=ERROR_RESPONSES,
        response_class=Response,
    )
    def get_artifact(artifact_id: Identifier, current: Identity) -> Response:
        reference, content = artifact_service.get_verified(current, artifact_id)
        return Response(
            content=content,
            media_type=reference.content_type,
            headers={
                "Digest": reference.content_digest,
                "X-Artifact-ID": reference.artifact_id,
            },
        )

    @app.get(
        "/api/shared/projects/{project_id}/audit",
        response_model=list[AuditEvent],
    )
    def audit_events(
        project_id: Identifier,
        current: Identity,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> list[AuditEvent]:
        return repository.list_audit_events(current, project_id, limit)

    return app


def build_app() -> FastAPI:
    """Uvicorn factory entry point: ``uvicorn ...app:build_app --factory``."""

    return create_shared_lab_app()
