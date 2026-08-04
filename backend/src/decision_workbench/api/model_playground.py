from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from decision_workbench.api.dependencies import get_model_playground_use_cases
from decision_workbench.application.model_playground import (
    ModelPlaygroundError,
    ModelPlaygroundUseCases,
)
from decision_workbench.contracts.model_playground_contracts import (
    ComputeBudgetPreset,
    ModelExplorationAdoptionMemoRequest,
    ModelExplorationAttemptRequest,
    ModelExplorationRegistrationRequest,
    ModelExplorationRun,
    ModelExplorationRunCreateRequest,
    ModelPlaygroundContextPreview,
)
from decision_workbench.persistence.model_playground_repository import (
    ModelExplorationRunConflictError,
    ModelExplorationRunNotFoundError,
)


router = APIRouter(prefix="/api/model-playground", tags=["model-playground"])
UseCases = Annotated[ModelPlaygroundUseCases, Depends(get_model_playground_use_cases)]


def _raise_application_error(exc: Exception) -> None:
    if isinstance(exc, ModelExplorationRunNotFoundError):
        raise HTTPException(404, "Model Playground Runが見つかりません") from exc
    if isinstance(exc, ModelExplorationRunConflictError):
        raise HTTPException(
            409,
            detail={
                "message": str(exc),
                "current": exc.current.model_dump(mode="json"),
            },
        ) from exc
    raise HTTPException(422, str(exc)) from exc


@router.get("/preview", response_model=ModelPlaygroundContextPreview)
def preview_context(
    task_id: str,
    profile_revision_id: str,
    training_snapshot_id: str,
    service: UseCases,
    compute_budget: ComputeBudgetPreset = Query(default="standard"),
) -> ModelPlaygroundContextPreview:
    try:
        return service.preview(
            task_id=task_id,
            profile_revision_id=profile_revision_id,
            training_snapshot_id=training_snapshot_id,
            compute_budget=compute_budget,
        )
    except (ModelPlaygroundError, LookupError, ValueError) as exc:
        _raise_application_error(exc)
        raise AssertionError("unreachable")


@router.post("/runs", response_model=ModelExplorationRun)
def create_run(
    payload: ModelExplorationRunCreateRequest,
    service: UseCases,
) -> ModelExplorationRun:
    try:
        return service.create_run(payload)
    except (ModelPlaygroundError, LookupError, ValueError) as exc:
        _raise_application_error(exc)
        raise AssertionError("unreachable")


@router.get("/runs", response_model=tuple[ModelExplorationRun, ...])
def list_runs(service: UseCases) -> tuple[ModelExplorationRun, ...]:
    return service.list_runs()


@router.get("/runs/{run_id}", response_model=ModelExplorationRun)
def get_run(run_id: str, service: UseCases) -> ModelExplorationRun:
    try:
        return service.get_run(run_id)
    except (ModelExplorationRunNotFoundError, ValueError) as exc:
        _raise_application_error(exc)
        raise AssertionError("unreachable")


@router.post(
    "/runs/{run_id}/recipes/{recipe_id}/attempts",
    response_model=ModelExplorationRun,
)
def execute_recipe(
    run_id: str,
    recipe_id: str,
    payload: ModelExplorationAttemptRequest,
    service: UseCases,
) -> ModelExplorationRun:
    try:
        return service.execute_recipe(
            run_id,
            recipe_id,
            expected_revision=payload.expected_revision,
        )
    except (
        ModelPlaygroundError,
        ModelExplorationRunNotFoundError,
        ModelExplorationRunConflictError,
        ValueError,
    ) as exc:
        _raise_application_error(exc)
        raise AssertionError("unreachable")


@router.put("/runs/{run_id}/adoption-memo", response_model=ModelExplorationRun)
def record_adoption_memo(
    run_id: str,
    payload: ModelExplorationAdoptionMemoRequest,
    service: UseCases,
) -> ModelExplorationRun:
    try:
        return service.record_adoption_memo(
            run_id,
            expected_revision=payload.expected_revision,
            decision=payload.decision,
            rationale=payload.rationale,
            adopted_recipe_id=payload.adopted_recipe_id,
        )
    except (
        ModelPlaygroundError,
        ModelExplorationRunNotFoundError,
        ModelExplorationRunConflictError,
        ValueError,
    ) as exc:
        _raise_application_error(exc)
        raise AssertionError("unreachable")


@router.post(
    "/runs/{run_id}/attempts/{attempt_id}/register",
    response_model=ModelExplorationRun,
)
def register_attempt(
    run_id: str,
    attempt_id: str,
    payload: ModelExplorationRegistrationRequest,
    service: UseCases,
) -> ModelExplorationRun:
    try:
        return service.register_attempt(
            run_id,
            attempt_id,
            expected_revision=payload.expected_revision,
        )
    except (
        ModelPlaygroundError,
        ModelExplorationRunNotFoundError,
        ModelExplorationRunConflictError,
        ValueError,
    ) as exc:
        _raise_application_error(exc)
        raise AssertionError("unreachable")
