from __future__ import annotations

from pathlib import Path

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
from material_workbench.persistence.workspace_catalog import (
    CatalogConflictError as PersistenceCatalogConflictError,
    CatalogReferenceError,
    WorkspaceCatalog,
)
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.application.workspace_catalog_bootstrap import (
    WorkspaceCatalogBootstrapError,
    register_available_packages,
    task_definition_digest,
)
from material_workbench.data.profile_family_registry import supported_task_ids
from material_workbench.data.profile_workbench import profile_locator_for_digest
from material_workbench.modeling.model_packages import ModelPackageLoader, PackageContractError
from material_workbench.modeling.model_lifecycle import MODELS_ROOT


class DataLibraryUseCaseError(ValueError):
    """Base application error translated by the HTTP router."""


class DataLibraryNotFoundError(DataLibraryUseCaseError):
    pass


class DataLibraryConflictError(DataLibraryUseCaseError):
    pass


class DataLibraryValidationError(DataLibraryUseCaseError):
    pass


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
            profile_locator=(
                str(locator)
                if (locator := profile_locator_for_digest(profile.profile_digest))
                else None
            ),
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


def list_datasets(
    catalog: WorkspaceCatalog,
    store: Store,
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


def update_dataset(
    revision_id: str,
    payload: DatasetRevisionUpdateInput,
    catalog: WorkspaceCatalog,
) -> DataLibraryDataset:
    dataset = catalog.get_dataset_revision(revision_id, include_archived=True)
    if dataset is None:
        raise DataLibraryNotFoundError("Datasetが見つかりません")

    try:
        catalog.set_dataset_revision_availability(
            revision_id, archived=payload.archived
        )
    except CatalogReferenceError as exc:
        raise DataLibraryConflictError(str(exc)) from exc

    return next(
        item
        for item in _datasets(catalog, include_archived=True)
        if item.dataset_revision.id == revision_id
    )


def list_dataset_views(catalog: WorkspaceCatalog) -> list[DatasetViewRevision]:
    return _available_views(catalog)


def create_dataset_view(
    payload: DatasetViewRevisionCreateInput, catalog: WorkspaceCatalog
) -> DatasetViewRevision:
    try:
        return catalog.upsert_dataset_view_revision(payload)
    except PersistenceCatalogConflictError as exc:
        raise DataLibraryConflictError(str(exc)) from exc
    except CatalogReferenceError as exc:
        raise DataLibraryValidationError(str(exc)) from exc


def list_model_packages(
    catalog: WorkspaceCatalog,
    store: Store,
    package_origins: dict[str, str],
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


def refresh_model_packages(
    catalog: WorkspaceCatalog,
    registry: TaskRegistry,
    available_packages_paths: tuple[Path, ...],
    personal_available_packages_paths: tuple[Path, ...],
    package_origins: dict[str, str],
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
    except (WorkspaceCatalogBootstrapError, PersistenceCatalogConflictError) as exc:
        raise DataLibraryConflictError(str(exc)) from exc
    package_origins.clear()
    package_origins.update(refreshed_origins)
    return ModelPackageRefreshResult(
        model_packages=[
            _present_model_package(item, package_origins)
            for item in catalog.list_model_package_refs(include_archived=True)
        ],
        warnings=warnings,
    )


def update_model_package(
    reference_id: str,
    payload: ModelPackageRefUpdateInput,
    catalog: WorkspaceCatalog,
    registry: TaskRegistry,
    package_origins: dict[str, str],
) -> ModelPackageRef:
    package = catalog.get_model_package_ref(reference_id, include_archived=True)
    if package is None:
        raise DataLibraryNotFoundError("Model Packageが見つかりません")
    if not payload.archived:
        try:
            verified = ModelPackageLoader().load(Path(package.locator))
        except PackageContractError as exc:
            raise DataLibraryConflictError(
                f"Model Packageの実体を検証できません: {exc}"
            ) from exc
        expected_contract_digest = task_definition_digest(registry, package.task_id)
        if (
            verified.manifest_sha256 != package.manifest_digest
            or verified.manifest.package_id != package.package_id
            or verified.manifest.task_id != package.task_id
            or package.task_contract_digest != expected_contract_digest
        ):
            raise DataLibraryConflictError(
                "Model Packageの実体またはPrediction Task契約が登録時と一致しません",
            )
    try:
        updated = catalog.set_model_package_ref_availability(
            reference_id, archived=payload.archived
        )
    except CatalogReferenceError as exc:
        raise DataLibraryConflictError(str(exc)) from exc
    assert updated is not None
    return _present_model_package(updated, package_origins)


def project_creation_options(
    catalog: WorkspaceCatalog,
    registry: TaskRegistry,
    store: Store,
    package_origins: dict[str, str],
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


class DataLibraryUseCases:
    def __init__(
        self,
        *,
        catalog: WorkspaceCatalog,
        registry: TaskRegistry,
        store: Store,
        available_packages_paths: tuple[Path, ...],
        personal_available_packages_paths: tuple[Path, ...],
        package_origins: dict[str, str],
    ) -> None:
        self.catalog = catalog
        self.registry = registry
        self.store = store
        self.available_packages_paths = available_packages_paths
        self.personal_available_packages_paths = personal_available_packages_paths
        self.package_origins = package_origins

    def list_datasets(
        self,
        *,
        include_archived: bool = False,
        include_gallery: bool = False,
    ) -> list[DataLibraryDataset]:
        return list_datasets(
            self.catalog,
            self.store,
            include_archived,
            include_gallery,
        )

    def update_dataset(
        self,
        revision_id: str,
        payload: DatasetRevisionUpdateInput,
    ) -> DataLibraryDataset:
        return update_dataset(revision_id, payload, self.catalog)

    def list_dataset_views(self) -> list[DatasetViewRevision]:
        return list_dataset_views(self.catalog)

    def create_dataset_view(
        self,
        payload: DatasetViewRevisionCreateInput,
    ) -> DatasetViewRevision:
        return create_dataset_view(payload, self.catalog)

    def list_model_packages(
        self,
        *,
        include_archived: bool = False,
        include_gallery: bool = False,
    ) -> list[ModelPackageRef]:
        return list_model_packages(
            self.catalog,
            self.store,
            self.package_origins,
            include_archived,
            include_gallery,
        )

    def refresh_model_packages(self) -> ModelPackageRefreshResult:
        return refresh_model_packages(
            self.catalog,
            self.registry,
            self.available_packages_paths,
            self.personal_available_packages_paths,
            self.package_origins,
        )

    def update_model_package(
        self,
        reference_id: str,
        payload: ModelPackageRefUpdateInput,
    ) -> ModelPackageRef:
        return update_model_package(
            reference_id,
            payload,
            self.catalog,
            self.registry,
            self.package_origins,
        )

    def project_creation_options(self) -> ProjectCreationOptions:
        return project_creation_options(
            self.catalog,
            self.registry,
            self.store,
            self.package_origins,
        )
