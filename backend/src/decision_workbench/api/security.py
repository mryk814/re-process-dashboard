from __future__ import annotations

import os
import re
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
    launch_token = os.getenv("WORKBENCH_LAUNCH_TOKEN", "")
    allowed_origins = [*configured_origins]
    if launch_token:
        allowed_origins.insert(0, "null")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_origin_regex=r"^http://(127\.0\.0\.1|localhost):\d+$",
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "X-Workbench-Human-Actor",
            "X-Workbench-Launch-Token",
        ],
        allow_credentials=False,
    )
    loopback_origin = re.compile(r"^http://(127\.0\.0\.1|localhost):\d+$")

    @app.middleware("http")
    async def require_launch_token(request: Request, call_next: Any) -> Any:
        origin = request.headers.get("Origin")
        browser_origin_allowed = (
            origin is None
            or origin in configured_origins
            or loopback_origin.fullmatch(origin) is not None
            or (origin == "null" and bool(launch_token))
        )
        if not browser_origin_allowed:
            return JSONResponse(
                status_code=403,
                content={
                    "code": "origin_not_allowed",
                    "message": "このAPIは起動中のローカルアプリからのみ利用できます。",
                    "field_errors": [],
                },
            )
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
