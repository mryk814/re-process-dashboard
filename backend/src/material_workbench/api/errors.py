from __future__ import annotations

import sqlite3

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from material_workbench.contracts.schemas import ApiError, Candidate
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolutionError
from material_workbench.tasks.task_registry import TaskUnavailableError
from material_workbench.contracts.subsystem_availability import (
    SubsystemUnavailableError,
)
from material_workbench.persistence.data_lifecycle_payload_storage import (
    LifecyclePayloadUnavailableError,
)


PROJECT_API_ERRORS = {
    404: {"model": ApiError, "description": "Not Found"},
    409: {"model": ApiError, "description": "Conflict"},
    422: {"model": ApiError, "description": "Validation Error"},
    503: {"model": ApiError, "description": "Task Runtime Unavailable"},
}


class DomainApiException(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        current_candidate: Candidate | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.current_candidate = current_candidate


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(LifecyclePayloadUnavailableError)
    async def lifecycle_payload_unavailable(
        _: Request, exc: LifecyclePayloadUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={
                "code": "lifecycle_payload_unavailable",
                "message": str(exc),
                "resource_kind": exc.resource_kind,
                "resource_id": exc.resource_id,
            },
        )

    @app.exception_handler(sqlite3.IntegrityError)
    async def sqlite_integrity_error(
        _: Request, exc: sqlite3.IntegrityError
    ) -> JSONResponse:
        if "project_archived" not in str(exc):
            raise exc
        payload = ApiError(
            code="project_archived",
            message="アーカイブ済みProjectは変更できません",
        )
        return JSONResponse(
            status_code=409,
            content=payload.model_dump(
                mode="json", exclude={"current_candidate", "availability"}
            ),
        )

    @app.exception_handler(TaskUnavailableError)
    async def task_unavailable_error(_: Request, exc: TaskUnavailableError) -> JSONResponse:
        payload = ApiError(code="runtime_unavailable", message=str(exc))
        return JSONResponse(
            status_code=503,
            content=payload.model_dump(
                mode="json", exclude={"current_candidate", "availability"}
            ),
        )

    @app.exception_handler(ProjectRuntimeResolutionError)
    async def project_runtime_error(_: Request, exc: ProjectRuntimeResolutionError) -> JSONResponse:
        payload = ApiError(code="runtime_unavailable", message=str(exc))
        return JSONResponse(
            status_code=503,
            content=payload.model_dump(
                mode="json", exclude={"current_candidate", "availability"}
            ),
        )

    @app.exception_handler(SubsystemUnavailableError)
    async def subsystem_unavailable_error(
        _: Request, exc: SubsystemUnavailableError
    ) -> JSONResponse:
        payload = ApiError(
            code="subsystem_unavailable",
            message=exc.availability.message,
            availability=exc.availability,
        )
        return JSONResponse(
            status_code=503,
            content=payload.model_dump(
                mode="json", exclude={"current_candidate"}
            ),
        )

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = {
            404: "not_found",
            422: "validation_error",
            503: "runtime_unavailable",
        }.get(exc.status_code, "validation_error")
        message = exc.detail if isinstance(exc.detail, str) else "リクエストを処理できません"
        payload = ApiError(code=code, message=message)
        return JSONResponse(
            status_code=exc.status_code,
            content=payload.model_dump(
                mode="json", exclude={"current_candidate", "availability"}
            ),
        )

    @app.exception_handler(DomainApiException)
    async def domain_error(_: Request, exc: DomainApiException) -> JSONResponse:
        payload = ApiError(
            code=exc.code,  # type: ignore[arg-type]
            message=exc.message,
            current_candidate=exc.current_candidate,
        )
        excluded = (
            {"current_candidate", "availability"}
            if exc.current_candidate is None
            else {"availability"}
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=payload.model_dump(mode="json", exclude=excluded),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        payload = ApiError(
            code="validation_error",
            message="入力内容を確認してください",
            field_errors=[
                {"path": ".".join(str(part) for part in error["loc"]), "message": error["msg"]}
                for error in exc.errors()
            ],
        )
        return JSONResponse(
            status_code=422,
            content=payload.model_dump(
                mode="json", exclude={"current_candidate", "availability"}
            ),
        )
