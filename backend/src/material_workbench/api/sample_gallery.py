from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from .dependencies import get_store, get_task_registry
from .errors import DomainApiException
from material_workbench.contracts.schemas import (
    Project,
    SampleGalleryInstallInput,
    SampleGalleryItem,
)
from material_workbench.persistence.demo_seed import (
    QUICKSTART_PROJECT_ID,
    install_starter_projects,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog_bootstrap import (
    bootstrap_workspace_catalog,
)
from material_workbench.tasks.task_registry import TaskRegistry


router = APIRouter(prefix="/api/sample-gallery")
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]


def _items(store: Store, registry: TaskRegistry) -> list[SampleGalleryItem]:
    result: list[SampleGalleryItem] = []
    for task_id in registry.task_ids:
        starter = registry.module_for(task_id).starter_project
        if starter is None or starter.project_id == QUICKSTART_PROJECT_ID:
            continue
        availability = registry.availability_for(task_id)
        result.append(SampleGalleryItem(
            project_id=starter.project_id,
            task_id=task_id,
            name=starter.name,
            installed=store.get_project(
                starter.project_id,
                include_archived=True,
            ) is not None,
            available=availability.status != "unavailable",
            unavailable_reason=(
                availability.message
                if availability.status == "unavailable"
                else ""
            ),
        ))
    return sorted(result, key=lambda item: (item.name, item.project_id))


@router.get("", response_model=list[SampleGalleryItem])
def list_sample_gallery(
    store: StoreDependency,
    registry: RegistryDependency,
) -> list[SampleGalleryItem]:
    return _items(store, registry)


@router.post("", response_model=list[Project])
def install_sample_gallery(
    payload: SampleGalleryInstallInput,
    store: StoreDependency,
    registry: RegistryDependency,
) -> list[Project]:
    items = _items(store, registry)
    by_project_id = {item.project_id: item for item in items}
    requested = (
        set(payload.project_ids)
        if payload.project_ids
        else {item.project_id for item in items if item.available}
    )
    unknown = sorted(requested - set(by_project_id))
    if unknown:
        raise DomainApiException(
            404,
            "not_found",
            f"サンプルが見つかりません: {', '.join(unknown)}",
        )
    unavailable = sorted(
        project_id
        for project_id in requested
        if not by_project_id[project_id].available
    )
    if unavailable:
        raise DomainApiException(
            409,
            "subsystem_unavailable",
            f"現在追加できないサンプルです: {', '.join(unavailable)}",
        )
    installed = install_starter_projects(store, registry, requested)
    bootstrap_workspace_catalog(store.path, registry)
    return [
        project
        for project_id in installed
        if (project := store.get_project(project_id, include_archived=True))
        is not None
    ]
