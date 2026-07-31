from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from decision_workbench.application.catalog.errors import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogValidationError,
)
from decision_workbench.application.catalog.feature_inspector import FeatureInspector
from decision_workbench.application.catalog.task_package_catalog import (
    TaskPackageCatalog,
)
from decision_workbench.application.catalog.training_inspector import (
    TrainingInspector,
)
from decision_workbench.contracts.prediction_catalog_contracts import (
    InputSpaceEmbeddingResponse,
    ModelPackageStatus,
    ModelTrainingDataPage,
    OutputSpaceEvidenceResponse,
)
from decision_workbench.contracts.evidence_contracts import TaskCatalogItem
from decision_workbench.contracts.subsystem_availability import SubsystemAvailability
from decision_workbench.contracts.task_contracts import ResolvedTaskDefinition

from .dependencies import (
    get_feature_inspector,
    get_task_package_catalog,
    get_training_inspector,
)
from .errors import PROJECT_API_ERRORS


router = APIRouter()
TaskPackageCatalogDependency = Annotated[
    TaskPackageCatalog,
    Depends(get_task_package_catalog),
]
TrainingInspectorDependency = Annotated[
    TrainingInspector,
    Depends(get_training_inspector),
]
FeatureInspectorDependency = Annotated[
    FeatureInspector,
    Depends(get_feature_inspector),
]


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
def health(catalog: TaskPackageCatalogDependency) -> dict[str, Any]:
    return catalog.health()


@router.get("/api/readiness", operation_id="getReadiness")
def readiness(catalog: TaskPackageCatalogDependency) -> dict[str, Any]:
    return catalog.readiness()


@router.get(
    "/api/subsystem-availability",
    response_model=list[SubsystemAvailability],
    operation_id="listSubsystemAvailability",
)
def subsystem_availability(
    catalog: TaskPackageCatalogDependency,
) -> tuple[SubsystemAvailability, ...]:
    return catalog.subsystem_availability()


@router.get(
    "/api/projects/{project_id}/model-package",
    response_model=ModelPackageStatus,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectModelPackage",
)
def model_package(
    project_id: str,
    catalog: TaskPackageCatalogDependency,
) -> dict[str, Any]:
    try:
        return catalog.model_package(project_id)
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
    inspector: TrainingInspectorDependency,
    stage: Annotated[Literal["curation", "selected", "features"], Query()] = "selected",
    target: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    try:
        return inspector.model_training_data(
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
    inspector: FeatureInspectorDependency,
    x_target: Annotated[str, Query(min_length=1)],
    y_target: Annotated[str, Query(min_length=1)],
    candidate_id: Annotated[str, Query(min_length=1)],
    expected_revision: Annotated[int, Query(ge=1)],
    distance_filter: Literal["supported", "caution", "all"] = "supported",
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
) -> dict[str, Any]:
    try:
        return inspector.output_space_evidence(
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
    inspector: FeatureInspectorDependency,
    candidate_id: Annotated[str, Query(min_length=1)],
    expected_revision: Annotated[int, Query(ge=1)],
) -> dict[str, Any]:
    try:
        return inspector.input_space_embedding(
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
def task_definitions(
    catalog: TaskPackageCatalogDependency,
) -> list[dict[str, Any]]:
    return catalog.task_definitions()


@router.get(
    "/api/projects/{project_id}/task-definition",
    response_model=ResolvedTaskDefinition,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectTaskDefinition",
)
def task_definition(
    project_id: str,
    catalog: TaskPackageCatalogDependency,
) -> ResolvedTaskDefinition:
    try:
        return catalog.task_definition(project_id)
    except (CatalogNotFoundError, CatalogValidationError, CatalogConflictError) as exc:
        raise _translate_catalog_error(exc) from exc
