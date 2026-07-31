from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from .dependencies import get_store, get_task_registry
from .errors import DomainApiException
from decision_workbench.contracts.candidate_project_contracts import (
    CandidateInput,
    Project,
    ProjectInput,
)
from decision_workbench.contracts.data_library_contracts import (
    SampleGalleryInstallInput,
    SampleGalleryItem,
)
from decision_workbench.persistence.demo_seed import (
    QUICKSTART_PROJECT_ID,
    gallery_project_ids,
    install_starter_projects,
    starter_project_ids,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.store import (
    ProjectHasDerivedCandidatesError,
    ProjectHasSuccessorsError,
)
from decision_workbench.application.workspace_catalog_bootstrap import (
    bootstrap_workspace_catalog,
)
from decision_workbench.tasks.task_registry import TaskRegistry


router = APIRouter(prefix="/api/sample-gallery")
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]


def _removal_blocker(
    store: Store,
    registry: TaskRegistry,
    task_id: str,
    project_id: str,
) -> str:
    project = store.get_project(project_id, include_archived=True)
    if project is None:
        return ""
    starter = registry.module_for(task_id).starter_project
    if starter is None or starter.project_id != project_id or not project.starter:
        return "同梱サンプルとして確認できないため取り除けません"
    expected_project = ProjectInput(name=starter.name, task_id=task_id)
    current_project = ProjectInput.model_validate(project.model_dump())
    if current_project != expected_project:
        return "プロジェクト設定が変更されています。必要ならアーカイブしてください"
    current_candidates = store.list_candidates(project_id, include_archived=True)
    expected_candidates = starter.candidate_factory(
        registry.runtime_for(task_id),
        registry.contract_for(task_id).task_definition,
    )
    current_payloads = sorted(
        CandidateInput.model_validate(candidate.model_dump()).model_dump_json()
        for candidate in current_candidates
    )
    expected_payloads = sorted(candidate.model_dump_json() for candidate in expected_candidates)
    if (
        any(candidate.revision != 1 or candidate.archived_at is not None for candidate in current_candidates)
        or current_payloads != expected_payloads
    ):
        return "候補が編集・追加・削除されています。必要ならアーカイブしてください"
    if store.project_has_persisted_evidence(project_id):
        return "保存した予測・実測・探索結果またはレビュー記録があります。アーカイブして保持してください"
    return ""


def _items(store: Store, registry: TaskRegistry) -> list[SampleGalleryItem]:
    result: list[SampleGalleryItem] = []
    for task_id in registry.task_ids:
        starter = registry.module_for(task_id).starter_project
        if starter is None or starter.project_id == QUICKSTART_PROJECT_ID:
            continue
        installed = store.get_project(
            starter.project_id,
            include_archived=True,
        ) is not None
        if starter.distribution == "legacy_hidden" and not installed:
            continue
        availability = registry.availability_for(task_id)
        removal_blocker = (
            _removal_blocker(store, registry, task_id, starter.project_id)
            if installed
            else ""
        )
        result.append(SampleGalleryItem(
            project_id=starter.project_id,
            task_id=task_id,
            name=starter.name,
            installed=installed,
            available=availability.status != "unavailable",
            unavailable_reason=(
                availability.message
                if availability.status == "unavailable"
                else ""
            ),
            removable=installed and not removal_blocker,
            remove_blocked_reason=removal_blocker,
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
    modules = {
        task_id: registry.module_for(task_id)
        for task_id in registry.task_ids
    }
    gallery_ids = gallery_project_ids(modules)
    starter_ids = starter_project_ids(modules)
    requested = (
        set(payload.project_ids)
        if payload.project_ids
        else {
            item.project_id
            for item in items
            if item.project_id in gallery_ids and item.available
        }
    )
    unknown = sorted(requested - starter_ids)
    if unknown:
        raise DomainApiException(
            404,
            "not_found",
            f"サンプルが見つかりません: {', '.join(unknown)}",
        )
    retired = sorted(requested - gallery_ids)
    if retired:
        raise DomainApiException(
            409,
            "validation_error",
            f"現在は新規追加できない旧サンプルです: {', '.join(retired)}",
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


@router.delete("/{project_id}", status_code=204)
def remove_sample_gallery(
    project_id: str,
    store: StoreDependency,
    registry: RegistryDependency,
) -> Response:
    items = {item.project_id: item for item in _items(store, registry)}
    item = items.get(project_id)
    if item is None:
        raise DomainApiException(404, "not_found", "同梱サンプルが見つかりません")
    if not item.installed:
        raise DomainApiException(404, "not_found", "このサンプルはWorkspaceにありません")
    if not item.removable:
        raise DomainApiException(
            409,
            "sample_has_saved_work",
            item.remove_blocked_reason or "保存済みの作業があるため取り除けません",
        )
    try:
        # Sample removal is one atomic purge. Archiving first would leave a
        # Project hidden when a successor or derived candidate blocks deletion.
        store.purge_project(project_id, allow_active=True)
    except (ProjectHasSuccessorsError, ProjectHasDerivedCandidatesError) as exc:
        raise DomainApiException(409, "sample_has_saved_work", str(exc)) from exc
    return Response(status_code=204)
