from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from material_workbench.api.dependencies import get_store, get_workspace_catalog
from material_workbench.application.data_lifecycle import (
    DataLifecycleService,
    SourceFetchFailedError,
)
from material_workbench.contracts.data_lifecycle_contracts import (
    ApprovedTrainingSnapshot,
    CanonicalDatasetRevision,
    ConnectorLifecycleDetail,
    CurationRecipe,
    CurationRecipeCreateInput,
    CurationRun,
    CurationRunCreateInput,
    DataLifecycleActor,
    DataLifecycleCatalog,
    DatasetApprovalInput,
    DatasetApprovalRequest,
    SourceConnector,
    SourceConnectorCreateInput,
    SourceFetchRequest,
    SourceFetchResult,
    TrainingSnapshotCreateInput,
    TrainingSnapshotCreateRequest,
)
from material_workbench.domain.data_lifecycle import LifecycleConflictError
from material_workbench.persistence.data_lifecycle_repository import (
    LifecycleResourceConflictError,
    LifecycleResourceNotFoundError,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog


router = APIRouter(prefix="/api/data-lifecycle")
LOCAL_WORKSPACE_ACTOR = DataLifecycleActor(
    id="local-workspace-user",
    label="このローカルワークスペースの利用者",
)


def get_data_lifecycle_service(
    store: Annotated[Store, Depends(get_store)],
) -> DataLifecycleService:
    return DataLifecycleService(store.path)


ServiceDependency = Annotated[
    DataLifecycleService,
    Depends(get_data_lifecycle_service),
]
CatalogDependency = Annotated[WorkspaceCatalog, Depends(get_workspace_catalog)]


def _raise_lifecycle_error(exc: Exception) -> None:
    if isinstance(exc, LifecycleResourceNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    raise HTTPException(409, str(exc)) from exc


@router.get("", response_model=DataLifecycleCatalog)
def lifecycle_catalog(service: ServiceDependency) -> DataLifecycleCatalog:
    return DataLifecycleCatalog(
        current_actor=LOCAL_WORKSPACE_ACTOR,
        connectors=service.list_connectors(),
        recipes=service.list_recipes(),
    )


@router.post("/connectors", response_model=SourceConnector, status_code=201)
def create_connector(
    payload: SourceConnectorCreateInput,
    service: ServiceDependency,
) -> SourceConnector:
    return service.create_connector(payload)


@router.get(
    "/connectors/{connector_id}",
    response_model=ConnectorLifecycleDetail,
)
def connector_detail(
    connector_id: str,
    service: ServiceDependency,
) -> ConnectorLifecycleDetail:
    try:
        return service.detail(connector_id)
    except LifecycleResourceNotFoundError as exc:
        _raise_lifecycle_error(exc)


@router.post(
    "/connectors/{connector_id}/fetch",
    response_model=SourceFetchResult,
    status_code=201,
)
def fetch_source(
    connector_id: str,
    payload: SourceFetchRequest,
    service: ServiceDependency,
    source_credential: Annotated[
        str | None,
        Header(alias="X-Source-Credential", include_in_schema=False),
    ] = None,
) -> SourceFetchResult:
    # The connector adapter receives a one-request credential boundary. The
    # current JSON object adapter does not need it, and it is never passed to a
    # persisted application contract.
    del source_credential
    try:
        snapshot, attempt = service.fetch(connector_id, payload)
        return SourceFetchResult(snapshot=snapshot, attempt=attempt)
    except SourceFetchFailedError as exc:
        raise HTTPException(
            422,
            {
                "message": str(exc),
                "attempt_id": exc.attempt.id,
                "error_code": exc.attempt.error_code,
            },
        ) from exc
    except LifecycleResourceNotFoundError as exc:
        _raise_lifecycle_error(exc)


@router.post("/recipes", response_model=CurationRecipe, status_code=201)
def create_recipe(
    payload: CurationRecipeCreateInput,
    service: ServiceDependency,
) -> CurationRecipe:
    try:
        return service.create_recipe(payload)
    except LifecycleResourceConflictError as exc:
        _raise_lifecycle_error(exc)


@router.post(
    "/raw-snapshots/{snapshot_id}/curation-runs",
    response_model=CurationRun,
    status_code=201,
)
def create_curation_run(
    snapshot_id: str,
    payload: CurationRunCreateInput,
    service: ServiceDependency,
    catalog: CatalogDependency,
) -> CurationRun:
    profile = catalog.get_profile_revision(
        payload.profile_revision_id,
        include_archived=True,
    )
    if profile is None:
        raise HTTPException(422, "Dataset Profile Revisionが見つかりません")
    if profile.profile_digest != payload.profile_digest:
        raise HTTPException(409, "Dataset Profile digestが登録済みrevisionと一致しません")
    try:
        return service.curate(snapshot_id, payload)
    except (LifecycleResourceNotFoundError, LifecycleConflictError) as exc:
        _raise_lifecycle_error(exc)


@router.post(
    "/curation-runs/{run_id}/approve",
    response_model=CanonicalDatasetRevision,
    status_code=201,
)
def approve_curation_run(
    run_id: str,
    payload: DatasetApprovalRequest,
    service: ServiceDependency,
) -> CanonicalDatasetRevision:
    try:
        return service.approve(
            run_id,
            DatasetApprovalInput(
                actor=LOCAL_WORKSPACE_ACTOR.id,
                reason=payload.reason,
                overrides=payload.overrides,
            ),
        )
    except (LifecycleResourceNotFoundError, LifecycleConflictError) as exc:
        _raise_lifecycle_error(exc)


@router.post(
    "/canonical-dataset-revisions/{revision_id}/training-snapshots",
    response_model=ApprovedTrainingSnapshot,
    status_code=201,
)
def create_training_snapshot(
    revision_id: str,
    payload: TrainingSnapshotCreateRequest,
    service: ServiceDependency,
) -> ApprovedTrainingSnapshot:
    try:
        return service.create_training_snapshot(
            revision_id,
            TrainingSnapshotCreateInput(
                actor=LOCAL_WORKSPACE_ACTOR.id,
                purpose=payload.purpose,
                targets=payload.targets,
                split=payload.split,
                selection_policy=payload.selection_policy,
            ),
        )
    except (LifecycleResourceNotFoundError, LifecycleConflictError) as exc:
        _raise_lifecycle_error(exc)
