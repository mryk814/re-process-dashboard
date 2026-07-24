from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .dependencies import get_task_registry, get_workspace_catalog
from material_workbench.contracts.schemas import (
    DataLibraryDataset,
    DatasetRevisionUpdateInput,
    DatasetViewRevision,
    DatasetViewRevisionCreateInput,
    ModelPackageRef,
    ModelPackageRefUpdateInput,
    ProjectCreationOptions,
)
from material_workbench.persistence.workspace_catalog import CatalogConflictError, CatalogReferenceError, WorkspaceCatalog
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.persistence.workspace_catalog_bootstrap import task_definition_digest
from material_workbench.data.profile_document import supported_task_ids
from material_workbench.modeling.model_packages import ModelPackageLoader, PackageContractError


router = APIRouter(prefix="/api")
CatalogDependency = Annotated[WorkspaceCatalog, Depends(get_workspace_catalog)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]


def _available_views(catalog: WorkspaceCatalog) -> list[DatasetViewRevision]:
    active_dataset_ids = {item.id for item in catalog.list_dataset_revisions()}
    return [
        view
        for view in catalog.list_dataset_view_revisions()
        if all(member.dataset_revision_id in active_dataset_ids for member in view.members)
    ]


def _datasets(catalog: WorkspaceCatalog, *, include_archived: bool = False) -> list[DataLibraryDataset]:
    views = (
        catalog.list_dataset_view_revisions(include_archived=True)
        if include_archived
        else _available_views(catalog)
    )
    result: list[DataLibraryDataset] = []
    for dataset in catalog.list_dataset_revisions(include_archived=include_archived):
        asset = catalog.get_data_asset(dataset.data_asset_id, include_archived=True)
        profile = catalog.get_profile_revision(dataset.profile_revision_id, include_archived=True)
        if asset is None or profile is None:
            continue
        result.append(DataLibraryDataset(
            dataset_revision=dataset,
            data_asset=asset,
            profile_revision=profile,
            supported_task_ids=list(supported_task_ids(profile.effective_profile_json)),
            dataset_views=[
                view for view in views
                if any(member.dataset_revision_id == dataset.id for member in view.members)
            ],
        ))
    return result


@router.get("/data-library/datasets", response_model=list[DataLibraryDataset])
def list_datasets(
    catalog: CatalogDependency,
    include_archived: bool = False,
) -> list[DataLibraryDataset]:
    return _datasets(catalog, include_archived=include_archived)


@router.patch("/data-library/datasets/{revision_id}", response_model=DataLibraryDataset)
def update_dataset(
    revision_id: str,
    payload: DatasetRevisionUpdateInput,
    catalog: CatalogDependency,
) -> DataLibraryDataset:
    dataset = catalog.get_dataset_revision(revision_id, include_archived=True)
    if dataset is None:
        raise HTTPException(404, "Datasetが見つかりません")

    try:
        catalog.set_dataset_revision_availability(
            revision_id, archived=payload.archived
        )
    except CatalogReferenceError as exc:
        raise HTTPException(409, str(exc)) from exc

    return next(
        item
        for item in _datasets(catalog, include_archived=True)
        if item.dataset_revision.id == revision_id
    )


@router.get("/data-library/views", response_model=list[DatasetViewRevision])
def list_dataset_views(catalog: CatalogDependency) -> list[DatasetViewRevision]:
    return _available_views(catalog)


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
def list_model_packages(
    catalog: CatalogDependency,
    include_archived: bool = False,
) -> list[ModelPackageRef]:
    return catalog.list_model_package_refs(include_archived=include_archived)


@router.patch("/data-library/model-packages/{reference_id}", response_model=ModelPackageRef)
def update_model_package(
    reference_id: str,
    payload: ModelPackageRefUpdateInput,
    catalog: CatalogDependency,
    registry: RegistryDependency,
) -> ModelPackageRef:
    package = catalog.get_model_package_ref(reference_id, include_archived=True)
    if package is None:
        raise HTTPException(404, "Model Packageが見つかりません")
    if not payload.archived:
        try:
            verified = ModelPackageLoader().load(Path(package.locator))
        except PackageContractError as exc:
            raise HTTPException(409, f"Model Packageの実体を検証できません: {exc}") from exc
        expected_contract_digest = task_definition_digest(registry, package.task_id)
        if (
            verified.manifest_sha256 != package.manifest_digest
            or verified.manifest.package_id != package.package_id
            or verified.manifest.task_id != package.task_id
            or package.task_contract_digest != expected_contract_digest
        ):
            raise HTTPException(
                409,
                "Model Packageの実体またはPrediction Task契約が登録時と一致しません",
            )
    try:
        updated = catalog.set_model_package_ref_availability(
            reference_id, archived=payload.archived
        )
    except CatalogReferenceError as exc:
        raise HTTPException(409, str(exc)) from exc
    assert updated is not None
    return updated


@router.get("/project-creation-options", response_model=ProjectCreationOptions)
def project_creation_options(
    catalog: CatalogDependency,
    registry: RegistryDependency,
) -> ProjectCreationOptions:
    return ProjectCreationOptions(
        datasets=_datasets(catalog),
        dataset_views=_available_views(catalog),
        model_packages=catalog.list_model_package_refs(),
        project_series=catalog.list_project_series(),
        task_contract_digests={
            task_id: task_definition_digest(registry, task_id)
            for task_id in registry.task_ids
        },
    )
