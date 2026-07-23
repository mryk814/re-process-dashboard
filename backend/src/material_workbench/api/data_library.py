from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .dependencies import get_task_registry, get_workspace_catalog
from material_workbench.contracts.schemas import (
    DataLibraryDataset,
    DatasetViewRevision,
    DatasetViewRevisionCreateInput,
    ModelPackageRef,
    ProjectCreationOptions,
)
from material_workbench.persistence.workspace_catalog import CatalogConflictError, CatalogReferenceError, WorkspaceCatalog
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.persistence.workspace_catalog_bootstrap import task_definition_digest


router = APIRouter(prefix="/api")
CatalogDependency = Annotated[WorkspaceCatalog, Depends(get_workspace_catalog)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]


def _datasets(catalog: WorkspaceCatalog) -> list[DataLibraryDataset]:
    views = catalog.list_dataset_view_revisions()
    result: list[DataLibraryDataset] = []
    for dataset in catalog.list_dataset_revisions():
        asset = catalog.get_data_asset(dataset.data_asset_id)
        profile = catalog.get_profile_revision(dataset.profile_revision_id)
        if asset is None or profile is None:
            continue
        tasks = profile.effective_profile_json.get("tasks", {})
        supported = sorted(tasks) if isinstance(tasks, dict) else []
        result.append(DataLibraryDataset(
            dataset_revision=dataset,
            data_asset=asset,
            profile_revision=profile,
            supported_task_ids=supported,
            dataset_views=[
                view for view in views
                if any(member.dataset_revision_id == dataset.id for member in view.members)
            ],
        ))
    return result


@router.get("/data-library/datasets", response_model=list[DataLibraryDataset])
def list_datasets(catalog: CatalogDependency) -> list[DataLibraryDataset]:
    return _datasets(catalog)


@router.get("/data-library/views", response_model=list[DatasetViewRevision])
def list_dataset_views(catalog: CatalogDependency) -> list[DatasetViewRevision]:
    return catalog.list_dataset_view_revisions()


@router.post("/data-library/views", status_code=201, response_model=DatasetViewRevision)
def create_dataset_view(
    payload: DatasetViewRevisionCreateInput, catalog: CatalogDependency
) -> DatasetViewRevision:
    try:
        return catalog.upsert_dataset_view_revision(payload)
    except CatalogConflictError as exc:
        raise HTTPException(409, str(exc)) from exc
    except CatalogReferenceError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/data-library/model-packages", response_model=list[ModelPackageRef])
def list_model_packages(catalog: CatalogDependency) -> list[ModelPackageRef]:
    return catalog.list_model_package_refs()


@router.get("/project-creation-options", response_model=ProjectCreationOptions)
def project_creation_options(
    catalog: CatalogDependency,
    registry: RegistryDependency,
) -> ProjectCreationOptions:
    return ProjectCreationOptions(
        datasets=_datasets(catalog),
        dataset_views=catalog.list_dataset_view_revisions(),
        model_packages=catalog.list_model_package_refs(),
        project_series=catalog.list_project_series(),
        task_contract_digests={
            task_id: task_definition_digest(registry, task_id)
            for task_id in registry.task_ids
        },
    )
