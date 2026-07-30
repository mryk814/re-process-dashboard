from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from material_workbench.application.catalog import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogUseCases,
    CatalogValidationError,
)
from material_workbench.contracts.schemas import (
    InputSpaceEmbeddingResponse,
    ModelPackageStatus,
    ModelTrainingDataPage,
    OutputSpaceEvidenceResponse,
    TaskCatalogItem,
)
from material_workbench.contracts.subsystem_availability import SubsystemAvailability
from material_workbench.contracts.task_contracts import ResolvedTaskDefinition

from .dependencies import get_catalog_use_cases
from .errors import PROJECT_API_ERRORS


router = APIRouter()
CatalogDependency = Annotated[CatalogUseCases, Depends(get_catalog_use_cases)]


def _translate_catalog_error(exc: Exception) -> HTTPException:
    if isinstance(exc, CatalogNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, CatalogValidationError):
        return HTTPException(422, str(exc))
    if isinstance(exc, CatalogConflictError):
        return HTTPException(409, str(exc))
    raise exc


@router.get("/api/health")
@router.get("/health", include_in_schema=False)
def health(use_cases: CatalogDependency) -> dict[str, Any]:
    return use_cases.health()


@router.get("/api/readiness", operation_id="getReadiness")
def readiness(use_cases: CatalogDependency) -> dict[str, Any]:
    return use_cases.readiness()


@router.get(
    "/api/subsystem-availability",
    response_model=list[SubsystemAvailability],
    operation_id="listSubsystemAvailability",
)
def subsystem_availability(
    use_cases: CatalogDependency,
) -> tuple[SubsystemAvailability, ...]:
    return use_cases.subsystem_availability()


@router.get(
    "/api/projects/{project_id}/model-package",
    response_model=ModelPackageStatus,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectModelPackage",
)
def model_package(project_id: str, use_cases: CatalogDependency) -> dict[str, Any]:
    try:
        return use_cases.model_package(project_id)
    except (CatalogNotFoundError, CatalogValidationError, CatalogConflictError) as exc:
        raise _translate_catalog_error(exc) from exc


@router.get(
    "/api/projects/{project_id}/model-package/training-data",
    response_model=ModelTrainingDataPage,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectModelTrainingData",
)
def model_training_data(
    project_id: str,
    use_cases: CatalogDependency,
    stage: Annotated[Literal["curation", "selected", "features"], Query()] = "selected",
    target: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    try:
        return use_cases.model_training_data(
            project_id,
            stage=stage,
            target=target,
            offset=offset,
            limit=limit,
        )
    except (CatalogNotFoundError, CatalogValidationError, CatalogConflictError) as exc:
        raise _translate_catalog_error(exc) from exc


@router.get(
    "/api/projects/{project_id}/model-package/output-space-evidence",
    response_model=OutputSpaceEvidenceResponse,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectOutputSpaceEvidence",
)
def output_space_evidence(
    project_id: str,
    use_cases: CatalogDependency,
    x_target: Annotated[str, Query(min_length=1)],
    y_target: Annotated[str, Query(min_length=1)],
    candidate_id: Annotated[str, Query(min_length=1)],
    expected_revision: Annotated[int, Query(ge=1)],
    distance_filter: Literal["supported", "caution", "all"] = "supported",
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> dict[str, Any]:
    try:
        return use_cases.output_space_evidence(
            project_id,
            x_target=x_target,
            y_target=y_target,
            candidate_id=candidate_id,
            expected_revision=expected_revision,
            distance_filter=distance_filter,
            limit=limit,
        )
    except (CatalogNotFoundError, CatalogValidationError, CatalogConflictError) as exc:
        raise _translate_catalog_error(exc) from exc


@router.get(
    "/api/projects/{project_id}/model-package/input-space",
    response_model=InputSpaceEmbeddingResponse,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectInputSpace",
)
def input_space_embedding(
    project_id: str,
    use_cases: CatalogDependency,
    candidate_id: Annotated[str, Query(min_length=1)],
    expected_revision: Annotated[int, Query(ge=1)],
) -> dict[str, Any]:
    try:
        return use_cases.input_space_embedding(
            project_id,
            candidate_id=candidate_id,
            expected_revision=expected_revision,
        )
    except (CatalogNotFoundError, CatalogValidationError, CatalogConflictError) as exc:
        raise _translate_catalog_error(exc) from exc


@router.get(
    "/api/task-definitions",
    response_model=list[TaskCatalogItem],
    operation_id="listTaskDefinitions",
)
def task_definitions(use_cases: CatalogDependency) -> list[dict[str, Any]]:
    return use_cases.task_definitions()


@router.get(
    "/api/projects/{project_id}/task-definition",
    response_model=ResolvedTaskDefinition,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectTaskDefinition",
)
def task_definition(
    project_id: str,
    use_cases: CatalogDependency,
) -> ResolvedTaskDefinition:
    try:
        return use_cases.task_definition(project_id)
    except (CatalogNotFoundError, CatalogValidationError, CatalogConflictError) as exc:
        raise _translate_catalog_error(exc) from exc
