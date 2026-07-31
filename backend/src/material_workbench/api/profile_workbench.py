from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, AsyncIterator
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from openpyxl.utils.exceptions import InvalidFileException
from pydantic import TypeAdapter, ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse

from material_workbench.application.dataset_registration import register_managed_dataset
from material_workbench.contracts.schemas import (
    ApiError,
    ProfileWorkbenchBindingDraft,
    ProfileWorkbenchConfirmedBinding,
    ProfileWorkbenchDraftSave,
    ProfileWorkbenchInspection,
    ProfileWorkbenchProfileOption,
    ProfileWorkbenchRegistration,
    ProfileWorkbenchValidation,
)
from material_workbench.data.file_integrity import file_sha256
from material_workbench.data.profile_workbench import (
    create_source_binding_draft,
    inspect_workbook,
    personal_profile_paths,
    personal_profile_store_path,
    save_source_binding_profile,
    validate_personal_profile_store_path,
)
from material_workbench.data.profile_family_registry import (
    ProfileFamilyUnavailableError,
    load_profile_document,
    profile_registration_metadata,
)
from material_workbench.data.profiles.schema import DatasetProfileError
from material_workbench.modeling.model_lifecycle import dataset_profile_digest
from material_workbench.persistence.workspace_catalog import (
    CatalogConflictError,
    WorkspaceCatalog,
)

from .dependencies import get_data_library_root, get_workspace_catalog
from .errors import PROJECT_API_ERRORS

router = APIRouter(prefix="/api/profile-workbench", tags=["profile-workbench"])
PROFILE_WORKBENCH_API_ERRORS = {
    409: PROJECT_API_ERRORS[409],
    413: {"model": ApiError, "description": "Payload Too Large"},
    422: PROJECT_API_ERRORS[422],
}
MAX_WORKBOOK_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_WORKBOOK_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 10_000
_PROFILE_ROOT = Path(__file__).resolve().parent.parent / "data"


def _profile_registry() -> dict[str, Path]:
    result: dict[str, Path] = {}
    paths = [
        *_PROFILE_ROOT.glob("dataset-input-profile-*.json"),
        _PROFILE_ROOT / "welding-stage-b-profile-v1.json",
    ]
    for path in sorted(paths):
        if path.is_file():
            result[dataset_profile_digest(path)] = path.resolve()
    try:
        personal_paths = personal_profile_paths()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    for path in personal_paths:
        result.setdefault(dataset_profile_digest(path), path.resolve())
    return result


def _profile_path(profile_digest: str) -> Path:
    selected = _profile_registry().get(profile_digest)
    if selected is None:
        raise HTTPException(422, "許可されたDataset Profileを選択してください")
    return selected


def _registered_profile_digest(profile_path: str | Path | None) -> str | None:
    if profile_path is None:
        return None
    resolved = Path(profile_path).resolve()
    return next((digest for digest, path in _profile_registry().items() if path == resolved), None)


def _best_binding_draft(source: Path) -> dict[str, Any] | None:
    """Choose a semantic base for an unknown workbook without confirming guesses."""

    ranked: list[tuple[tuple[int, int, int, str], dict[str, Any]]] = []
    for digest, path in _profile_registry().items():
        try:
            draft = create_source_binding_draft(source, path)
        except (DatasetProfileError, OSError, ValueError):
            continue
        if draft is None:
            continue
        required = [slot for slot in draft["slots"] if slot["required"]]
        score = (
            sum(slot["state"] == "confirmed" for slot in required),
            sum(slot["state"] == "suggested" for slot in required),
            -len(required),
            digest,
        )
        ranked.append((score, draft))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


@asynccontextmanager
async def _uploaded_workbook(file: UploadFile) -> AsyncIterator[Path]:
    filename = Path(file.filename or "").name
    if not filename or Path(filename).suffix.lower() != ".xlsx":
        raise HTTPException(422, "Excel .xlsx ファイルを選択してください")
    temporary = TemporaryDirectory(prefix="material-workbench-profile-")
    target = Path(temporary.name) / filename
    size = 0
    try:
        with target.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_WORKBOOK_BYTES:
                    raise HTTPException(413, "Excelファイルは100 MB以下にしてください")
                stream.write(chunk)
        try:
            with ZipFile(target) as archive:
                members = archive.infolist()
                if len(members) > MAX_ARCHIVE_MEMBERS or sum(item.file_size for item in members) > MAX_EXPANDED_WORKBOOK_BYTES:
                    raise HTTPException(413, "展開後のExcelファイルが大きすぎます")
        except BadZipFile as exc:
            raise HTTPException(422, "Excelファイルを読み取れません。壊れていない.xlsxファイルか確認してください") from exc
        yield target
    finally:
        await file.close()
        temporary.cleanup()


def _validation(raw: object) -> ProfileWorkbenchValidation | None:
    if not isinstance(raw, dict):
        return None
    return ProfileWorkbenchValidation(
        registration_ready=bool(raw.get("registration_ready")),
        profile_id=str(raw["profile_id"]),
        profile_digest=str(raw["profile_digest"]),
        task_ids=[str(item) for item in raw.get("task_ids", [])],
        entities=int(raw.get("entities", 0)),
        relations=int(raw.get("relations", 0)),
        observations=int(raw.get("observations", 0)),
        observations_by_task={str(key): int(value) for key, value in dict(raw.get("observations_by_task", {})).items()},
        heat_series_parents=int(raw.get("heat_series_parents", 0)),
        unresolved_heat_series_by_task={
            str(key): int(value)
            for key, value in dict(raw.get("unresolved_heat_series_by_task", {})).items()
        },
        rejected_by_policy={str(key): int(value) for key, value in dict(raw.get("rejected_by_policy", {})).items()},
        entity_preview=[dict(item) for item in raw.get("entity_preview", []) if isinstance(item, dict)],
    )


def _inspection(
    source: Path,
    raw: dict[str, object],
    *,
    auto_detected: bool,
    binding_draft: dict[str, object] | None = None,
) -> ProfileWorkbenchInspection:
    selected_digest = _registered_profile_digest(raw.get("profile") if isinstance(raw.get("profile"), str) else None)
    return ProfileWorkbenchInspection(
        source_filename=source.name,
        source_sha256=str(raw["source_sha256"]),
        sheets=[
            {
                "name": str(sheet["name"]),
                "headers": [str(header) for header in sheet.get("headers", [])],
                "rows": int(sheet.get("rows", 0)),
            }
            for sheet in raw.get("sheets", [])
            if isinstance(sheet, dict)
        ],
        selected_profile_digest=selected_digest,
        auto_detected=auto_detected and selected_digest is not None,
        profile_error=str(raw["profile_error"]) if raw.get("profile_error") else None,
        validation=_validation(raw.get("canonicalization")),
        binding_draft=ProfileWorkbenchBindingDraft.model_validate(binding_draft) if binding_draft else None,
    )


def _validation_error(exc: Exception) -> HTTPException:
    if isinstance(exc, DatasetProfileError):
        return HTTPException(422, "\n".join(exc.errors))
    if isinstance(exc, (BadZipFile, InvalidFileException)):
        return HTTPException(422, "Excelファイルを読み取れません。壊れていない.xlsxファイルか確認してください")
    return HTTPException(422, str(exc) or "ExcelとDataset Profileの対応を確認してください")


def _reject_archived_registration(catalog: WorkspaceCatalog, source_sha256: str, profile_digest: str) -> None:
    asset = next((item for item in catalog.list_data_assets(include_archived=True) if item.sha256 == source_sha256), None)
    profile = next((item for item in catalog.list_profile_revisions(include_archived=True) if item.profile_digest == profile_digest), None)
    if (asset is not None and asset.archived_at is not None) or (profile is not None and profile.archived_at is not None):
        raise HTTPException(409, "同じExcelまたはProfileがarchiveされています。Data Libraryの管理状態を確認してください")
    if asset is None or profile is None:
        return
    dataset = next((
        item for item in catalog.list_dataset_revisions(include_archived=True)
        if item.data_asset_id == asset.id and item.profile_revision_id == profile.id
    ), None)
    if dataset is not None and dataset.archived_at is not None:
        raise HTTPException(409, "同じDatasetがarchiveされています。Data Libraryの管理状態を確認してください")
    if dataset is None:
        return
    archived_single_view = next((
        view for view in catalog.list_dataset_view_revisions(include_archived=True)
        if view.kind == "single"
        and any(member.dataset_revision_id == dataset.id for member in view.members)
        and view.archived_at is not None
    ), None)
    if archived_single_view is not None:
        raise HTTPException(409, "同じDataset Viewがarchiveされています。Data Libraryの管理状態を確認してください")


@router.get(
    "/profiles",
    response_model=list[ProfileWorkbenchProfileOption],
    responses=PROFILE_WORKBENCH_API_ERRORS,
)
def list_profile_options() -> list[ProfileWorkbenchProfileOption]:
    result: list[ProfileWorkbenchProfileOption] = []
    personal_store = personal_profile_store_path()
    for profile_digest, path in _profile_registry().items():
        try:
            metadata = profile_registration_metadata(load_profile_document(path))
        except ProfileFamilyUnavailableError as exc:
            raise HTTPException(422, str(exc)) from exc
        result.append(ProfileWorkbenchProfileOption(
            profile_id=metadata.profile_id,
            source_name=path.stem,
            profile_digest=profile_digest,
            task_ids=list(metadata.task_ids),
            personal=path.is_relative_to(personal_store),
        ))
    return result


@router.post(
    "/inspect",
    response_model=ProfileWorkbenchInspection,
    responses=PROFILE_WORKBENCH_API_ERRORS,
)
async def inspect_uploaded_workbook(
    file: UploadFile = File(...),
    profile_digest: str | None = Form(None),
) -> ProfileWorkbenchInspection:
    selected = _profile_path(profile_digest) if profile_digest else None
    async with _uploaded_workbook(file) as source:
        try:
            raw = await run_in_threadpool(inspect_workbook, source, selected)
            detected_profile = (
                Path(str(raw["profile"])).resolve()
                if selected is None and isinstance(raw.get("profile"), str)
                else selected
            )
            binding_draft = (
                await run_in_threadpool(create_source_binding_draft, source, detected_profile)
                if detected_profile is not None
                else await run_in_threadpool(_best_binding_draft, source)
            )
        except (DatasetProfileError, BadZipFile, InvalidFileException, OSError, ValueError) as exc:
            raise _validation_error(exc) from exc
        return _inspection(
            source,
            raw,
            auto_detected=selected is None,
            binding_draft=binding_draft,
        )


@router.post(
    "/profiles/drafts",
    response_model=ProfileWorkbenchDraftSave,
    responses=PROFILE_WORKBENCH_API_ERRORS,
)
async def save_profile_draft(
    file: UploadFile = File(...),
    base_profile_digest: str = Form(...),
    expected_source_sha256: str = Form(...),
    bindings_json: str = Form("[]"),
) -> ProfileWorkbenchDraftSave:
    selected = _profile_path(base_profile_digest)
    if re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256) is None:
        raise HTTPException(422, "確認時のExcel SHA-256が不正です")
    try:
        decoded = json.loads(bindings_json)
        bindings = TypeAdapter(list[ProfileWorkbenchConfirmedBinding]).validate_python(decoded)
        store = validate_personal_profile_store_path()
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc) or "対応付けJSONを確認してください") from exc
    async with _uploaded_workbook(file) as source:
        try:
            raw = await run_in_threadpool(
                save_source_binding_profile,
                source=source,
                base_profile_path=selected,
                expected_source_sha256=expected_source_sha256,
                bindings=[item.model_dump() for item in bindings],
                store_path=store,
            )
        except RuntimeError as exc:
            if str(exc) == "source_changed":
                raise HTTPException(409, "確認後にExcelの内容が変わりました。もう一度内容を確認してください") from exc
            raise _validation_error(exc) from exc
        except (DatasetProfileError, BadZipFile, InvalidFileException, OSError, ValueError) as exc:
            raise _validation_error(exc) from exc
    return ProfileWorkbenchDraftSave(
        profile_id=str(raw["profile_id"]),
        profile_digest=str(raw["profile_digest"]),
        profile_locator=str(raw["profile_locator"]),
        source_sha256=str(raw["source_sha256"]),
        base_profile_digest=str(raw["base_profile_digest"]),
        validation=_validation(raw["validation"]),
    )


@router.get(
    "/profiles/{profile_digest}/export",
    responses={
        **PROFILE_WORKBENCH_API_ERRORS,
        200: {
            "content": {"application/json": {}},
            "description": "Standalone effective Dataset Profile",
        },
    },
)
def export_profile(profile_digest: str) -> FileResponse:
    selected = _profile_path(profile_digest)
    return FileResponse(
        selected,
        media_type="application/json",
        filename=f"dataset-profile-{profile_digest[:12]}.json",
    )


@router.post(
    "/register",
    response_model=ProfileWorkbenchRegistration,
    responses=PROFILE_WORKBENCH_API_ERRORS,
)
async def register_uploaded_workbook(
    file: UploadFile = File(...),
    profile_digest: str = Form(...),
    expected_source_sha256: str = Form(...),
    name: str | None = Form(None),
    base_dataset_revision_id: str | None = Form(None),
    catalog: WorkspaceCatalog = Depends(get_workspace_catalog),
    library_root: Path = Depends(get_data_library_root),
) -> ProfileWorkbenchRegistration:
    selected = _profile_path(profile_digest)
    if re.fullmatch(r"[0-9a-f]{64}", expected_source_sha256) is None:
        raise HTTPException(422, "確認時のExcel SHA-256が不正です")
    dataset_name = (name or "").strip()
    if len(dataset_name) > 160:
        raise HTTPException(422, "Dataset名は160文字以下にしてください")
    before = {item.id for item in catalog.list_dataset_revisions(include_archived=True)}
    previous_dataset = None
    previous_asset = None
    if base_dataset_revision_id:
        previous_dataset = catalog.get_dataset_revision(
            base_dataset_revision_id,
            include_archived=True,
        )
        if previous_dataset is None:
            raise HTTPException(422, "更新元Dataset Revisionが見つかりません")
        previous_profile = catalog.get_profile_revision(
            previous_dataset.profile_revision_id,
            include_archived=True,
        )
        if previous_profile is None or previous_profile.profile_digest != profile_digest:
            raise HTTPException(
                422,
                "更新版は元Datasetと同じDataset Profileを選択してください",
            )
        previous_asset = catalog.get_data_asset(
            previous_dataset.data_asset_id,
            include_archived=True,
        )
        if previous_asset is None:
            raise HTTPException(422, "更新元のSourceが見つかりません")
    async with _uploaded_workbook(file) as source:
        actual_source_sha256 = await run_in_threadpool(file_sha256, source)
        if actual_source_sha256 != expected_source_sha256:
            raise HTTPException(409, "確認後にExcelの内容が変わりました。もう一度内容を確認してください")
        if previous_asset is not None and previous_asset.sha256 == actual_source_sha256:
            raise HTTPException(409, "更新元と内容が同じです。Source差分のあるファイルを選択してください")
        _reject_archived_registration(catalog, actual_source_sha256, profile_digest)
        try:
            result = await run_in_threadpool(
                register_managed_dataset,
                database=Path(catalog.path),
                source=source,
                library_root=library_root,
                profile_path=selected,
                name=dataset_name or None,
                member_provenance=(
                    {
                        "lineage_kind": "dataset_revision_update",
                        "previous_dataset_revision_id": previous_dataset.id,
                        "previous_source_sha256": previous_asset.sha256,
                    }
                    if previous_dataset is not None and previous_asset is not None
                    else None
                ),
            )
        except CatalogConflictError as exc:
            raise HTTPException(409, str(exc) or "同じDatasetの登録内容が競合しました") from exc
        except (DatasetProfileError, BadZipFile, InvalidFileException, OSError, ValueError) as exc:
            raise _validation_error(exc) from exc
    return ProfileWorkbenchRegistration(
        reused_existing=result.dataset_revision_id in before,
        data_asset_id=result.data_asset_id,
        profile_revision_id=result.profile_revision_id,
        dataset_revision_id=result.dataset_revision_id,
        dataset_view_revision_id=result.dataset_view_revision_id,
        source_sha256=result.source_sha256,
        profile_id=result.profile_id,
        task_ids=list(result.task_ids),
        previous_dataset_revision_id=(
            previous_dataset.id if previous_dataset is not None else None
        ),
        previous_source_sha256=(
            previous_asset.sha256 if previous_asset is not None else None
        ),
    )
