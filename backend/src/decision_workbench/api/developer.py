from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated
from zipfile import BadZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from openpyxl.utils.exceptions import InvalidFileException

from decision_workbench.api.dependencies import (
    get_project_runtime_resolver,
    get_store,
    get_task_registry,
    get_workspace_catalog,
    get_subsystem_availability,
)
from decision_workbench.developer_experience.change_guide import change_guide_entries
from decision_workbench.developer_experience.runtime_diagnostics import run_runtime_diagnostics
from decision_workbench.developer_experience.readiness import (
    ReadinessCatalog,
    ReadinessPreflight,
    preflight_source,
    readiness_catalog,
)
from decision_workbench.developer_experience.schemas import (
    ChangeGuideEntry,
    DeveloperOverview,
    DeveloperOverviewItem,
    RuntimeDiagnosticsReport,
)
from decision_workbench.data.observation_profile import (
    ObservationTrainingDataset,
    ObservationTrainingInspectionPage,
    ObservationTrainingProfileSummary,
    ObservationProfileError,
    inspect_observation_training_view,
)
from decision_workbench.data.profile_family_registry import (
    ProfileFamilyUnavailableError,
    load_inspection_descriptor,
)
from decision_workbench.contracts.evidence_contracts import ApiError
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.contracts.subsystem_availability import (
    SubsystemAvailabilityRegistry,
)
from decision_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError


router = APIRouter(prefix="/api/developer", tags=["developer"])
_ROOT = Path(__file__).resolve().parents[4]
_WELDING_PROFILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "observation-profile-welding-consumable-stage-c-v1.json"
)
_WELDING_SOURCE_RELATIVE = Path("data/source/welding_consumable_multistage_synthetic_dataset.xlsx")
_WELDING_STAGE_B_PROFILE = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "welding-stage-b-profile-v1.json"
)


@dataclass(frozen=True)
class _ObservationProfileRegistration:
    profile_id: str
    source_relative: Path
    profile_path: Path


_OBSERVATION_PROFILE_REGISTRY = (
    _ObservationProfileRegistration(
        profile_id="welding-consumable-stage-c-observations-v1",
        source_relative=_WELDING_SOURCE_RELATIVE,
        profile_path=_WELDING_PROFILE,
    ),
    _ObservationProfileRegistration(
        profile_id="welding-consumable-stage-b-v1",
        source_relative=_WELDING_SOURCE_RELATIVE,
        profile_path=_WELDING_STAGE_B_PROFILE,
    ),
)
_OBSERVATION_API_ERRORS = {
    503: {"model": ApiError, "description": "Observation Profile Unavailable"},
}
_READINESS_MAX_BYTES = 100 * 1024 * 1024


def _resource_root() -> Path:
    configured = os.getenv("WORKBENCH_RESOURCE_ROOT")
    return Path(configured) if configured else _ROOT


def _source_path(registration: _ObservationProfileRegistration) -> Path:
    return _resource_root() / registration.source_relative


@lru_cache(maxsize=8)
def _load_observation_dataset(
    source_path: str,
    source_mtime_ns: int,
    profile_path: str,
    profile_mtime_ns: int,
) -> ObservationTrainingDataset:
    del source_mtime_ns, profile_mtime_ns
    return load_inspection_descriptor(Path(source_path), Path(profile_path))


def _observation_dataset(
    registration: _ObservationProfileRegistration,
) -> ObservationTrainingDataset:
    source = _source_path(registration)
    try:
        dataset = _load_observation_dataset(
            str(source.resolve()),
            source.stat().st_mtime_ns,
            str(registration.profile_path.resolve()),
            registration.profile_path.stat().st_mtime_ns,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"観測Profile「{registration.profile_id}」の配布データが見つかりません。"
                "アプリを再インストールするか、配布データを確認してください。"
            ),
        ) from exc
    except (
        BadZipFile,
        InvalidFileException,
        ObservationProfileError,
        ProfileFamilyUnavailableError,
        OSError,
        KeyError,
        ValueError,
    ) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"観測Profile「{registration.profile_id}」の元データを読み取れません。"
                "ExcelとProfileの内容を確認し、正しい配布データへ差し替えてください。"
            ),
        ) from exc
    if dataset.profile_id != registration.profile_id:
        raise HTTPException(
            status_code=503,
            detail=(
                f"観測Profile「{registration.profile_id}」の登録内容が一致しません。"
                "Profile registryと配布Profileを確認してください。"
            ),
        )
    return dataset


def _observation_registration(profile_id: str) -> _ObservationProfileRegistration:
    registration = next(
        (item for item in _OBSERVATION_PROFILE_REGISTRY if item.profile_id == profile_id),
        None,
    )
    if registration is None:
        raise HTTPException(status_code=422, detail=f"unknown observation profile: {profile_id}")
    return registration


@router.get("/change-guide", response_model=list[ChangeGuideEntry])
def get_change_guide() -> list[ChangeGuideEntry]:
    return change_guide_entries()


@router.get("/readiness/catalog", response_model=ReadinessCatalog)
def get_readiness_catalog() -> ReadinessCatalog:
    """Return the shipped shape catalog without changing Workspace state."""

    return readiness_catalog()


@router.post("/readiness/preflight", response_model=ReadinessPreflight)
async def post_readiness_preflight(
    file: UploadFile = File(...),
    target_columns_json: str = Form("[]"),
) -> ReadinessPreflight:
    """Classify an uploaded source in a temporary directory only.

    This endpoint intentionally has no Workspace, Dataset, Profile, or Package
    dependency.  A successful response is advice, not a registration.
    """

    try:
        target_columns = json.loads(target_columns_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "target_columns_json must be a JSON string array") from exc
    if not isinstance(target_columns, list) or any(
        not isinstance(name, str) or not name.strip() for name in target_columns
    ):
        raise HTTPException(422, "target_columns_json must be a JSON string array")
    filename = Path(file.filename or "").name
    if not filename or Path(filename).suffix.lower() not in {".csv", ".xlsx"}:
        raise HTTPException(422, "CSVまたはExcel .xlsxファイルを選択してください")
    temporary = TemporaryDirectory(prefix="decision-workbench-readiness-")
    source = Path(temporary.name) / filename
    try:
        size = 0
        with source.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _READINESS_MAX_BYTES:
                    raise HTTPException(413, "CSVまたはExcelファイルは100 MB以下にしてください")
                stream.write(chunk)
        return preflight_source(source, target_columns=tuple(name.strip() for name in target_columns))
    except HTTPException:
        raise
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise HTTPException(
            422,
            {
                "code": "validation_error",
                "message": str(exc),
                "next_action": "形式が不明なsourceは標準Tabularとして続行せず、Profile WorkbenchまたはTask設計で確認してください。",
            },
        ) from exc
    finally:
        await file.close()
        temporary.cleanup()


@router.get(
    "/observation-training-profiles",
    response_model=list[ObservationTrainingProfileSummary],
    responses=_OBSERVATION_API_ERRORS,
)
def get_observation_training_profiles() -> list[ObservationTrainingProfileSummary]:
    return [
        ObservationTrainingProfileSummary(
            profile_id=dataset.profile_id,
            profile_digest=dataset.profile_digest,
            source_filename=_source_path(registration).name,
            source_sha256=dataset.source_sha256,
            families=tuple(view.summary for view in dataset.views.values()),
        )
        for registration in _OBSERVATION_PROFILE_REGISTRY
        for dataset in (_observation_dataset(registration),)
    ]


@router.get(
    "/observation-training-data",
    response_model=ObservationTrainingInspectionPage,
    responses=_OBSERVATION_API_ERRORS,
)
def get_observation_training_data(
    profile_id: Annotated[str, Query()],
    family: Annotated[str, Query()],
    target: Annotated[str, Query()],
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ObservationTrainingInspectionPage:
    dataset = _observation_dataset(_observation_registration(profile_id))
    try:
        return inspect_observation_training_view(
            dataset,
            family=family,
            target=target,
            offset=offset,
            limit=limit,
        )
    except KeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/diagnostics", response_model=RuntimeDiagnosticsReport)
def get_diagnostics(
    store: Store = Depends(get_store),
    registry: TaskRegistry = Depends(get_task_registry),
    catalog: WorkspaceCatalog = Depends(get_workspace_catalog),
    resolver: ProjectRuntimeResolver = Depends(get_project_runtime_resolver),
    subsystem_registry: SubsystemAvailabilityRegistry = Depends(
        get_subsystem_availability
    ),
) -> RuntimeDiagnosticsReport:
    return run_runtime_diagnostics(
        store=store,
        registry=registry,
        catalog=catalog,
        resolver=resolver,
        subsystem_registry=subsystem_registry,
    )


@router.get("/overview", response_model=DeveloperOverview)
def get_overview(
    store: Store = Depends(get_store),
    registry: TaskRegistry = Depends(get_task_registry),
    catalog: WorkspaceCatalog = Depends(get_workspace_catalog),
) -> DeveloperOverview:
    items: list[DeveloperOverviewItem] = []
    for project in store.list_projects():
        identity = project.scientific_identity
        # Chain Projectは単一Taskを持たず、利用停止中のTaskはentryを返さない。
        # どちらも一覧の1行として出す。1件のためにページ全体を落とさない。
        entry = None
        if identity.identity_kind == "single_task" and project.task_id:
            try:
                entry = registry.entry_for(project.task_id)
            except TaskRegistryError:
                entry = None
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
        chain_revision_id = (
            identity.chain_revision_id if identity.identity_kind == "chain" else None
        )
        if identity.identity_kind == "chain":
            revision = store.get_chain_revision(identity.chain_revision_id)
            validation_status = (
                "ok"
                if revision is not None
                and revision.revision_digest == identity.chain_revision_digest
                else "error"
            )
        else:
            validation_status = (
                "error"
                if not view or not dataset or not package_ref
                else "warning" if archived_references else "ok"
            )
        items.append(DeveloperOverviewItem(
            project_id=project.id,
            project_name=project.name,
            identity_kind=identity.identity_kind,
            chain_revision_id=chain_revision_id,
            dataset_view_revision_id=project.dataset_view_revision_id,
            dataset_revision_ids=dataset_ids,
            source_filename=asset.original_filename if asset else None,
            source_sha256=asset.sha256 if asset else entry.predictor_runtime.data.source_sha256 if entry else None,
            profile_id=profile.profile_id if profile else entry.predictor_runtime.data.profile_id if entry else None,
            profile_digest=profile.profile_digest if profile else None,
            task_id=project.task_id,
            task_contract_digest=project.task_contract_digest,
            package_id=package_ref.package_id if package_ref else None,
            package_manifest_digest=project.model_package_manifest_digest,
            feature_pipeline_id=str(pipeline.get("id")) if pipeline.get("id") else None,
            feature_pipeline_version=str(pipeline.get("version")) if pipeline.get("version") else None,
            runtime_type="+".join(runtime_types) or None,
            active_package=bool(
                entry
                and package_ref
                and package_ref.manifest_digest.replace("sha256:", "")
                == entry.model_package.manifest_sha256
            ),
            archived_references=archived_references,
            validation_status=validation_status,
        ))
    return DeveloperOverview(items=items)
