from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from material_workbench.api.candidates import (
    CandidateServiceDependency,
    CANDIDATE_APPLICATION_ERRORS,
    raise_candidate_http_error,
)
from material_workbench.api.dependencies import (
    get_deterministic_transform_catalog,
)
from material_workbench.application.blend_optimization import BlendOptimizationService
from material_workbench.contracts.blend_contracts import (
    BlendStructuralError,
)
from material_workbench.contracts.blend_optimization import (
    BlendOptimizationContext,
    BlendOptimizationRequest,
    BlendOptimizationResult,
)
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from material_workbench.persistence.store import CandidateRevisionConflictError


router = APIRouter()
TransformCatalogDependency = Annotated[
    DeterministicTransformCatalog, Depends(get_deterministic_transform_catalog)
]


def get_blend_optimization_service(
    candidates: CandidateServiceDependency,
    transforms: TransformCatalogDependency,
) -> BlendOptimizationService:
    return BlendOptimizationService(candidates, transforms)


OptimizationServiceDependency = Annotated[
    BlendOptimizationService, Depends(get_blend_optimization_service)
]


@router.get(
    "/api/projects/{project_id}/candidates/{candidate_id}/blend-optimization",
    response_model=BlendOptimizationContext,
)
def get_blend_optimization_context(
    project_id: str,
    candidate_id: str,
    service: OptimizationServiceDependency,
    expected_revision: int | None = None,
) -> BlendOptimizationContext:
    try:
        return service.context(project_id, candidate_id, expected_revision)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)
    except BlendStructuralError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post(
    "/api/projects/{project_id}/candidates/{candidate_id}/blend-optimization",
    response_model=BlendOptimizationResult,
)
def run_blend_optimization(
    project_id: str,
    candidate_id: str,
    payload: BlendOptimizationRequest,
    service: OptimizationServiceDependency,
) -> BlendOptimizationResult:
    try:
        return service.run(project_id, candidate_id, payload)
    except CandidateRevisionConflictError as exc:
        raise_candidate_http_error(exc)
    except CANDIDATE_APPLICATION_ERRORS as exc:
        raise_candidate_http_error(exc)
    except BlendStructuralError as exc:
        raise HTTPException(422, str(exc)) from exc
