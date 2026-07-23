from __future__ import annotations

from fastapi import APIRouter, Depends

from material_workbench.api.dependencies import get_store, get_task_registry, get_workspace_catalog
from material_workbench.developer_experience.change_guide import change_guide_entries
from material_workbench.developer_experience.diagnostics import run_developer_doctor
from material_workbench.developer_experience.schemas import (
    ChangeGuideEntry,
    DeveloperDoctorReport,
    DeveloperOverview,
    DeveloperOverviewItem,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.tasks.task_registry import TaskRegistry


router = APIRouter(prefix="/api/developer", tags=["developer"])


@router.get("/change-guide", response_model=list[ChangeGuideEntry])
def get_change_guide() -> list[ChangeGuideEntry]:
    return change_guide_entries()


@router.get("/diagnostics", response_model=DeveloperDoctorReport)
def get_diagnostics() -> DeveloperDoctorReport:
    return run_developer_doctor(include_generated_checks=True)


@router.get("/overview", response_model=DeveloperOverview)
def get_overview(
    store: Store = Depends(get_store),
    registry: TaskRegistry = Depends(get_task_registry),
    catalog: WorkspaceCatalog = Depends(get_workspace_catalog),
) -> DeveloperOverview:
    items: list[DeveloperOverviewItem] = []
    for project in store.list_projects():
        entry = registry.entry_for(project.task_id)
        view = (
            catalog.get_dataset_view_revision(project.dataset_view_revision_id, include_archived=True)
            if project.dataset_view_revision_id
            else None
        )
        dataset_ids = [member.dataset_revision_id for member in view.members] if view else []
        dataset = catalog.get_dataset_revision(dataset_ids[0], include_archived=True) if dataset_ids else None
        asset = catalog.get_data_asset(dataset.data_asset_id, include_archived=True) if dataset else None
        profile = catalog.get_profile_revision(dataset.profile_revision_id, include_archived=True) if dataset else None
        package = entry.model_package.manifest
        items.append(DeveloperOverviewItem(
            project_id=project.id,
            project_name=project.name,
            dataset_view_revision_id=project.dataset_view_revision_id,
            dataset_revision_ids=dataset_ids,
            source_filename=asset.original_filename if asset else None,
            source_sha256=asset.sha256 if asset else entry.predictor_runtime.data.source_sha256,
            profile_id=profile.profile_id if profile else entry.predictor_runtime.data.profile_id,
            profile_digest=profile.profile_digest if profile else None,
            task_id=project.task_id,
            task_contract_digest=project.task_contract_digest,
            package_id=package.package_id,
            package_manifest_digest=project.model_package_manifest_digest,
            feature_pipeline_id=package.feature_pipeline.id,
            feature_pipeline_version=package.feature_pipeline.version,
            runtime_type=entry.runtime_type,
            validation_status="ok",
        ))
    return DeveloperOverview(items=items)
