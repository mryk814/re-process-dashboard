from __future__ import annotations

import os
import secrets
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def configure_local_access(app: FastAPI) -> None:
    configured_origins = [
        origin.strip()
        for origin in os.getenv("WORKBENCH_CORS_ORIGINS", "").split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["null", *configured_origins],
        allow_origin_regex=r"^http://(127\.0\.0\.1|localhost):\d+$",
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Workbench-Launch-Token"],
        allow_credentials=False,
    )
    launch_token = os.getenv("WORKBENCH_LAUNCH_TOKEN", "")

    @app.middleware("http")
    async def require_launch_token(request: Request, call_next: Any) -> Any:
        if (
            launch_token
            and request.method != "OPTIONS"
            and (request.url.path == "/health" or request.url.path.startswith("/api/"))
            and not secrets.compare_digest(
                request.headers.get("X-Workbench-Launch-Token", ""),
                launch_token,
            )
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "launch_token_required",
                    "message": "このAPIは起動中のデスクトップアプリ専用です。",
                    "field_errors": [],
                },
            )
        return await call_next(request)
