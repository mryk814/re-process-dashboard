from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .dependencies import (
    get_available_packages_paths,
    get_model_package_origins,
    get_personal_available_packages_paths,
    get_store,
    get_task_registry,
    get_workspace_catalog,
)
from material_workbench.contracts.schemas import (
    DataLibraryDataset,
    DatasetRevisionUpdateInput,
    DatasetViewRevision,
    DatasetViewRevisionCreateInput,
    ModelPackageRef,
    ModelPackageRefreshResult,
    ModelPackageRefUpdateInput,
    ProjectCreationOptions,
)
from material_workbench.persistence.workspace_catalog import CatalogConflictError, CatalogReferenceError, WorkspaceCatalog
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.persistence.workspace_catalog_bootstrap import (
    WorkspaceCatalogBootstrapError,
    register_available_packages,
    task_definition_digest,
)
from material_workbench.data.profile_document import supported_task_ids
from material_workbench.modeling.model_packages import ModelPackageLoader, PackageContractError
from material_workbench.modeling.model_lifecycle import MODELS_ROOT
router = APIRouter(prefix="/api")
CatalogDependency = Annotated[WorkspaceCatalog, Depends(get_workspace_catalog)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
StoreDependency = Annotated[Store, Depends(get_store)]
AvailablePackagesPathsDependency = Annotated[
    tuple[Path, ...],
    Depends(get_available_packages_paths),
]
PersonalAvailablePackagesPathsDependency = Annotated[
    tuple[Path, ...],
    Depends(get_personal_available_packages_paths),
]
ModelPackageOriginsDependency = Annotated[
    dict[str, str],
    Depends(get_model_package_origins),
]


def _present_model_package(
    item: ModelPackageRef,
    package_origins: dict[str, str],
) -> ModelPackageRef:
    storage_scope = package_origins.get(item.id)
    if storage_scope is None:
        locator = Path(item.locator).resolve()
        bundled_root = MODELS_ROOT.resolve()
        storage_scope = (
            "bundled"
            if locator == bundled_root or bundled_root in locator.parents
            else "personal"
        )
    return item.model_copy(update={
        "storage_scope": storage_scope,
    })


def _available_views(catalog: WorkspaceCatalog) -> list[DatasetViewRevision]:
    active_dataset_ids = {item.id for item in catalog.list_dataset_revisions()}
    return [
        view
        for view in catalog.list_dataset_view_revisions()
        if all(member.dataset_revision_id in active_dataset_ids for member in view.members)
    ]


def _datasets(
    catalog: WorkspaceCatalog,
    *,
    include_archived: bool = False,
    visible_dataset_ids: set[str] | None = None,
) -> list[DataLibraryDataset]:
    views = (
        catalog.list_dataset_view_revisions(include_archived=True)
        if include_archived
        else _available_views(catalog)
    )
    result: list[DataLibraryDataset] = []
    for dataset in catalog.list_dataset_revisions(include_archived=include_archived):
        if visible_dataset_ids is not None and dataset.id not in visible_dataset_ids:
            continue
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


def _visible_dataset_ids(catalog: WorkspaceCatalog, store: Store) -> set[str]:
    referenced_view_ids = {
        project.dataset_view_revision_id
        for project in store.list_projects(include_archived=True)
        if project.dataset_view_revision_id
    }
    referenced_dataset_ids = {
        member.dataset_revision_id
        for view in catalog.list_dataset_view_revisions(include_archived=True)
        if view.id in referenced_view_ids
        for member in view.members
    }
    return {
        dataset.id
        for dataset in catalog.list_dataset_revisions(include_archived=True)
        if (
            dataset.id in referenced_dataset_ids
            or (
                (asset := catalog.get_data_asset(
                    dataset.data_asset_id,
                    include_archived=True,
                ))
                is not None
                and asset.locator_kind == "managed"
            )
        )
    }


def _visible_model_packages(
    catalog: WorkspaceCatalog,
    datasets: list[DataLibraryDataset],
    *,
    include_archived: bool,
) -> list[ModelPackageRef]:
    visible_bindings = {
        (
            f"sha256:{dataset.data_asset.sha256}",
            dataset.profile_revision.profile_digest,
        )
        for dataset in datasets
    }
    return [
        package
        for package in catalog.list_model_package_refs(
            include_archived=include_archived
        )
        if (
            package.manifest_json.get("provenance", {}).get("training_data_id"),
            package.manifest_json.get("provenance", {}).get("dataset_profile_id"),
        )
        in visible_bindings
    ]


@router.get("/data-library/datasets", response_model=list[DataLibraryDataset])
def list_datasets(
    catalog: CatalogDependency,
    store: StoreDependency,
    include_archived: bool = False,
    include_gallery: bool = False,
) -> list[DataLibraryDataset]:
    return _datasets(
        catalog,
        include_archived=include_archived,
        visible_dataset_ids=(
            None if include_gallery else _visible_dataset_ids(catalog, store)
        ),
    )


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
    store: StoreDependency,
    package_origins: ModelPackageOriginsDependency,
    include_archived: bool = False,
    include_gallery: bool = False,
) -> list[ModelPackageRef]:
    if include_gallery:
        return [
            _present_model_package(item, package_origins)
            for item in catalog.list_model_package_refs(
                include_archived=include_archived
            )
        ]
    datasets = _datasets(
        catalog,
        include_archived=True,
        visible_dataset_ids=_visible_dataset_ids(catalog, store),
    )
    return [
        _present_model_package(item, package_origins)
        for item in _visible_model_packages(
            catalog,
            datasets,
            include_archived=include_archived,
        )
    ]


@router.post(
    "/data-library/model-packages/refresh",
    response_model=ModelPackageRefreshResult,
)
def refresh_model_packages(
    catalog: CatalogDependency,
    registry: RegistryDependency,
    available_packages_paths: AvailablePackagesPathsDependency,
    personal_available_packages_paths: PersonalAvailablePackagesPathsDependency,
    package_origins: ModelPackageOriginsDependency,
) -> ModelPackageRefreshResult:
    """Import the trusted allow-list without replacing existing Project bindings."""

    warnings = []
    personal_paths = {
        path.resolve()
        for path in personal_available_packages_paths
    }
    bundled_origins = {
        reference_id: scope
        for reference_id, scope in package_origins.items()
        if scope == "bundled"
    }
    refreshed_origins = dict(bundled_origins)
    try:
        for path in available_packages_paths:
            is_personal = path.resolve() in personal_paths
            register_available_packages(
                catalog,
                registry,
                path,
                storage_scope="personal" if is_personal else "bundled",
                package_origins=refreshed_origins,
                warnings=warnings,
                strict=not is_personal,
            )
    except (WorkspaceCatalogBootstrapError, CatalogConflictError) as exc:
        raise HTTPException(409, str(exc)) from exc
    package_origins.clear()
    package_origins.update(refreshed_origins)
    return ModelPackageRefreshResult(
        model_packages=[
            _present_model_package(item, package_origins)
            for item in catalog.list_model_package_refs(include_archived=True)
        ],
        warnings=warnings,
    )


@router.patch("/data-library/model-packages/{reference_id}", response_model=ModelPackageRef)
def update_model_package(
    reference_id: str,
    payload: ModelPackageRefUpdateInput,
    catalog: CatalogDependency,
    registry: RegistryDependency,
    package_origins: ModelPackageOriginsDependency,
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
    return _present_model_package(updated, package_origins)


@router.get("/project-creation-options", response_model=ProjectCreationOptions)
def project_creation_options(
    catalog: CatalogDependency,
    registry: RegistryDependency,
    store: StoreDependency,
    package_origins: ModelPackageOriginsDependency,
) -> ProjectCreationOptions:
    visible_task_ids = {
        task_id
        for task_id in registry.available_task_ids
        if registry.entry_for(task_id).application_capability.project_creation
    }
    datasets = _datasets(
        catalog,
        visible_dataset_ids=_visible_dataset_ids(catalog, store),
    )
    visible_dataset_ids = {
        item.dataset_revision.id
        for item in datasets
    }
    visible_views = [
        view
        for view in _available_views(catalog)
        if all(
            member.dataset_revision_id in visible_dataset_ids
            for member in view.members
        )
    ]
    return ProjectCreationOptions(
        datasets=datasets,
        dataset_views=visible_views,
        model_packages=[
            _present_model_package(package, package_origins)
            for package in _visible_model_packages(
                catalog,
                datasets,
                include_archived=False,
            )
            if (
                package.task_id in visible_task_ids
                and package.id in package_origins
            )
        ],
        project_series=catalog.list_project_series(),
        task_contract_digests={
            task_id: task_definition_digest(registry, task_id)
            for task_id in visible_task_ids
        },
    )
