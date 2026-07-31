from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from decision_workbench.application.data_library import (
    DataLibraryConflictError,
    DataLibraryNotFoundError,
    DataLibraryUseCases,
    DataLibraryValidationError,
)
from decision_workbench.contracts.data_library_contracts import (
    DataLibraryDataset,
    DataLibraryModelPackage,
    DatasetRevisionUpdateInput,
    DatasetViewRevision,
    DatasetViewRevisionCreateInput,
    ModelPackageRefreshResult,
    ModelPackageRefUpdateInput,
    TaskResourceRefreshResult,
)
from decision_workbench.contracts.candidate_project_contracts import ProjectCreationOptions
from .dependencies import get_data_library_use_cases


router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)
DataLibraryDependency = Annotated[
    DataLibraryUseCases,
    Depends(get_data_library_use_cases),
]


def _translate_data_library_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DataLibraryNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, DataLibraryValidationError):
        return HTTPException(422, str(exc))
    if isinstance(exc, DataLibraryConflictError):
        return HTTPException(409, str(exc))
    raise exc


@router.get("/data-library/datasets", response_model=list[DataLibraryDataset])
def list_datasets(
    use_cases: DataLibraryDependency,
    include_archived: bool = False,
    include_gallery: bool = False,
) -> list[DataLibraryDataset]:
    return use_cases.list_datasets(
        include_archived=include_archived,
        include_gallery=include_gallery,
    )


@router.patch(
    "/data-library/datasets/{revision_id}",
    response_model=DataLibraryDataset,
)
def update_dataset(
    revision_id: str,
    payload: DatasetRevisionUpdateInput,
    use_cases: DataLibraryDependency,
) -> DataLibraryDataset:
    try:
        return use_cases.update_dataset(revision_id, payload)
    except (
        DataLibraryNotFoundError,
        DataLibraryValidationError,
        DataLibraryConflictError,
    ) as exc:
        raise _translate_data_library_error(exc) from exc


@router.get("/data-library/views", response_model=list[DatasetViewRevision])
def list_dataset_views(
    use_cases: DataLibraryDependency,
) -> list[DatasetViewRevision]:
    return use_cases.list_dataset_views()


@router.post(
    "/data-library/views",
    status_code=201,
    response_model=DatasetViewRevision,
)
def create_dataset_view(
    payload: DatasetViewRevisionCreateInput,
    use_cases: DataLibraryDependency,
) -> DatasetViewRevision:
    try:
        return use_cases.create_dataset_view(payload)
    except (DataLibraryValidationError, DataLibraryConflictError) as exc:
        raise _translate_data_library_error(exc) from exc


@router.get("/data-library/model-packages", response_model=list[DataLibraryModelPackage])
def list_model_packages(
    use_cases: DataLibraryDependency,
    include_archived: bool = False,
    include_gallery: bool = False,
) -> list[DataLibraryModelPackage]:
    return use_cases.list_model_packages(
        include_archived=include_archived,
        include_gallery=include_gallery,
    )


@router.post(
    "/data-library/model-packages/refresh",
    response_model=ModelPackageRefreshResult,
)
def refresh_model_packages(
    use_cases: DataLibraryDependency,
) -> ModelPackageRefreshResult:
    """Import the trusted allow-list without replacing existing Project bindings."""

    try:
        return use_cases.refresh_model_packages()
    except DataLibraryConflictError as exc:
        raise _translate_data_library_error(exc) from exc


@router.post(
    "/data-library/tasks/refresh",
    response_model=TaskResourceRefreshResult,
)
async def refresh_task_resources(request: Request) -> TaskResourceRefreshResult:
    """Atomically load validated personal Task bundles without restarting."""

    refresh = getattr(request.app.state, "refresh_task_resources", None)
    if refresh is None:
        raise HTTPException(503, "Task resourceの再読込を準備できていません")
    try:
        return await refresh()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.exception("PERSONAL_TASK_REFRESH_FAILED")
        raise HTTPException(
            409,
            "個人Taskを再読込できません。保存先とTask定義を確認してもう一度試してください。",
        ) from exc


@router.patch(
    "/data-library/model-packages/{reference_id}",
    response_model=DataLibraryModelPackage,
)
def update_model_package(
    reference_id: str,
    payload: ModelPackageRefUpdateInput,
    use_cases: DataLibraryDependency,
) -> DataLibraryModelPackage:
    try:
        return use_cases.update_model_package(reference_id, payload)
    except (DataLibraryNotFoundError, DataLibraryConflictError) as exc:
        raise _translate_data_library_error(exc) from exc


@router.get("/project-creation-options", response_model=ProjectCreationOptions)
def project_creation_options(
    use_cases: DataLibraryDependency,
) -> ProjectCreationOptions:
    return use_cases.project_creation_options()
