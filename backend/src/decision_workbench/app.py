"""FastAPI transport composition root."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from decision_workbench.api.ai_reviews import router as ai_reviews_router
from decision_workbench.api.candidates import router as candidates_router
from decision_workbench.api.catalog import router as catalog_router
from decision_workbench.api.data_exploration import (
    router as data_exploration_router,
)
from decision_workbench.api.historical_observations import (
    router as historical_observations_router,
)
from decision_workbench.api.data_library import router as data_library_router
from decision_workbench.api.csv_task_onboarding import router as csv_task_onboarding_router
from decision_workbench.api.data_lifecycle import (
    router as data_lifecycle_router,
)
from decision_workbench.api.decision_activities import (
    router as decision_activities_router,
)
from decision_workbench.api.developer import router as developer_router
from decision_workbench.api.errors import (
    PROJECT_API_ERRORS,
    install_exception_handlers,
)
from decision_workbench.api.inference import router as inference_router
from decision_workbench.api.model_library import router as model_library_router
from decision_workbench.api.profile_workbench import (
    router as profile_workbench_router,
)
from decision_workbench.api.project_series import (
    router as project_series_router,
)
from decision_workbench.api.projects import router as projects_router
from decision_workbench.api.records import router as records_router
from decision_workbench.api.sample_gallery import router as sample_gallery_router
from decision_workbench.api.screening import router as screening_router
from decision_workbench.api.security import configure_local_access
from decision_workbench.api.series_assets import router as series_assets_router
from decision_workbench.bootstrap.contributions import (
    ApplicationContributionConfig,
    builtin_application_contributions,
)
from decision_workbench.bootstrap.resources import (
    AppResources,
    default_personal_model_store_path,
)
from decision_workbench.bootstrap.startup import create_lifespan

if TYPE_CHECKING:
    from decision_workbench.application.ai_review_provider import AiReviewProvider


def create_app(
    db_path: str | Path | None = None,
    *,
    source_overrides: Mapping[str, str | Path] | None = None,
    package_roots: Mapping[str, str | Path] | None = None,
    active_packages_path: str | Path | None = None,
    model_store_path: str | Path | None = None,
    task_store_path: str | Path | None = None,
    data_library_path: str | Path | None = None,
    contribution_configs: Mapping[str, ApplicationContributionConfig] | None = None,
    ai_review_provider: AiReviewProvider | None = None,
    _resources: AppResources | None = None,
) -> FastAPI:
    contributions = builtin_application_contributions(contribution_configs)
    lifespan = create_lifespan(
        db_path,
        source_overrides=source_overrides,
        package_roots=package_roots,
        active_packages_path=active_packages_path,
        model_store_path=model_store_path,
        task_store_path=task_store_path,
        data_library_path=data_library_path,
        contributions=contributions,
        ai_review_provider=ai_review_provider,
        resources=_resources,
    )
    app = FastAPI(
        title="Evidence Decision Workbench API",
        version="0.1.0",
        lifespan=lifespan,
        responses={422: PROJECT_API_ERRORS[422]},
    )

    @app.middleware("http")
    async def gate_resource_promotion(request: Request, call_next):
        is_catalog_health = request.url.path in {"/health", "/api/health"}
        is_readiness = request.url.path == "/api/readiness"
        is_resource_refresh = (
            request.url.path == "/api/data-library/tasks/refresh"
            or request.url.path == "/api/data-library/csv-onboarding/prepare"
        )
        if is_readiness:
            return await call_next(request)
        if getattr(request.app.state, "resources_promoting", False):
            if is_catalog_health:
                await request.app.state.resource_promotion_complete.wait()
            else:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": (
                            "追加TaskをWorkspaceへ安全に登録しています。"
                            "完了後に自動で再試行してください。"
                        )
                    },
                )

        request.state.runtime_context = request.app.state.runtime_context
        if is_resource_refresh:
            return await call_next(request)

        request.app.state.active_resource_requests += 1
        request.app.state.resource_requests_idle.clear()
        try:
            return await call_next(request)
        finally:
            request.app.state.active_resource_requests -= 1
            if request.app.state.active_resource_requests == 0:
                request.app.state.resource_requests_idle.set()

    configure_local_access(app)
    install_exception_handlers(app)
    app.include_router(catalog_router)
    app.include_router(data_library_router)
    app.include_router(model_library_router)
    app.include_router(csv_task_onboarding_router)
    app.include_router(sample_gallery_router)
    app.include_router(data_lifecycle_router)
    app.include_router(series_assets_router)
    app.include_router(ai_reviews_router)
    app.include_router(developer_router)
    app.include_router(project_series_router)
    app.include_router(profile_workbench_router)
    app.include_router(projects_router)
    app.include_router(candidates_router)
    app.include_router(data_exploration_router)
    app.include_router(historical_observations_router)
    app.include_router(screening_router)
    app.include_router(decision_activities_router)
    app.include_router(inference_router)
    app.include_router(records_router)
    for contribution in contributions:
        for router in contribution.routers:
            app.include_router(router)
    return app


app = create_app(model_store_path=default_personal_model_store_path())
