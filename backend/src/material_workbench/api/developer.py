from __future__ import annotations

from fastapi import APIRouter, Depends

from material_workbench.api.dependencies import (
    get_project_runtime_resolver,
    get_store,
    get_task_registry,
    get_workspace_catalog,
)
from material_workbench.developer_experience.change_guide import change_guide_entries
from material_workbench.developer_experience.runtime_diagnostics import run_runtime_diagnostics
from material_workbench.developer_experience.schemas import (
    ChangeGuideEntry,
    DeveloperOverview,
    DeveloperOverviewItem,
    RuntimeDiagnosticsReport,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver
from material_workbench.tasks.task_registry import TaskRegistry


router = APIRouter(prefix="/api/developer", tags=["developer"])


@router.get("/change-guide", response_model=list[ChangeGuideEntry])
def get_change_guide() -> list[ChangeGuideEntry]:
    return change_guide_entries()


@router.get("/diagnostics", response_model=RuntimeDiagnosticsReport)
def get_diagnostics(
    store: Store = Depends(get_store),
    registry: TaskRegistry = Depends(get_task_registry),
    catalog: WorkspaceCatalog = Depends(get_workspace_catalog),
    resolver: ProjectRuntimeResolver = Depends(get_project_runtime_resolver),
) -> RuntimeDiagnosticsReport:
    return run_runtime_diagnostics(
        store=store,
        registry=registry,
        catalog=catalog,
        resolver=resolver,
    )


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
        package_ref = (
            catalog.get_model_package_ref(project.model_package_ref_id, include_archived=True)
            if project.model_package_ref_id
            else None
        )
        manifest = package_ref.manifest_json if package_ref else {}
        pipeline = manifest.get("feature_pipeline", {}) if isinstance(manifest.get("feature_pipeline"), dict) else {}
        predictors = manifest.get("predictors", []) if isinstance(manifest.get("predictors"), list) else []
        runtime_types = sorted({
            str(predictor.get("runtime_type"))
            for predictor in predictors
            if isinstance(predictor, dict) and predictor.get("runtime_type")
        })
        archived_references = [
            name
            for name, archived_at in (
                ("Dataset View", view.archived_at if view else None),
                ("Dataset Revision", dataset.archived_at if dataset else None),
                ("Data Asset", asset.archived_at if asset else None),
                ("Profile Revision", profile.archived_at if profile else None),
                ("Model Package", package_ref.archived_at if package_ref else None),
            )
            if archived_at is not None
        ]
        validation_status = "error" if not view or not dataset or not package_ref else "warning" if archived_references else "ok"
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
            package_id=package_ref.package_id if package_ref else None,
            package_manifest_digest=project.model_package_manifest_digest,
            feature_pipeline_id=str(pipeline.get("id")) if pipeline.get("id") else None,
            feature_pipeline_version=str(pipeline.get("version")) if pipeline.get("version") else None,
            runtime_type="+".join(runtime_types) or None,
            active_package=bool(
                package_ref
                and package_ref.manifest_digest.replace("sha256:", "")
                == entry.model_package.manifest_sha256
            ),
            archived_references=archived_references,
            validation_status=validation_status,
        ))
    return DeveloperOverview(items=items)
