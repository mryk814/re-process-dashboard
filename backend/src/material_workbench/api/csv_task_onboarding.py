"""UI-only onboarding for a new tabular CSV prediction task.

The browser never receives a path or a CLI command.  The uploaded CSV is copied
only into the user-owned Task store and then into the managed Data Library.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from material_workbench.application.dataset_registration import (
    DatasetRegistrationResult,
    ManagedDatasetRegistrationCheckpoint,
    managed_dataset_registration_checkpoint,
    register_managed_dataset,
    rollback_managed_dataset_registration,
)
from material_workbench.application.personal_task_packages import (
    build_standard_package,
    promote_personal_package,
    rollback_personal_package_attempt,
)
from material_workbench.developer_experience.task_scaffolding import (
    ScaffoldField,
    create_task_scaffold,
    inspect_task_source,
    validate_personal_task_store_path,
)
from material_workbench.modeling.model_lifecycle import validate_personal_model_store_path


router = APIRouter(prefix="/api/data-library/csv-onboarding", tags=["csv-onboarding"])
MAX_CSV_BYTES = 100 * 1024 * 1024
logger = logging.getLogger(__name__)

_FAILURE_MESSAGES = {
    "inspect": "CSVを確認できませんでした。文字コード、ヘッダー、行数を確認してもう一度試してください。",
    "fields": "列の設定を確認してください。",
    "storage": "個人TaskまたはModelの保存先を確認してください。",
    "prepare": "Taskとモデルを準備できませんでした。列の役割と範囲を確認してもう一度試してください。",
    "dataset": "DatasetをData Libraryへ登録できませんでした。準備した内容は取り消しました。もう一度試してください。",
    "refresh": "新しいTaskを再読込できませんでした。準備した内容は取り消しました。もう一度試してください。",
}


async def _uploaded_csv(file: UploadFile) -> tuple[TemporaryDirectory[str], Path]:
    filename = Path(file.filename or "").name
    if not filename or Path(filename).suffix.lower() != ".csv":
        raise HTTPException(422, "CSVファイルを選択してください")
    temporary = TemporaryDirectory(prefix="material-workbench-csv-")
    target = Path(temporary.name) / filename
    size = 0
    try:
        with target.open("wb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_CSV_BYTES:
                    raise HTTPException(413, "CSVファイルは100 MB以下にしてください")
                stream.write(chunk)
        return temporary, target
    except Exception:
        temporary.cleanup()
        raise
    finally:
        await file.close()


def _inspection_payload(source: Path) -> dict[str, Any]:
    inspected = inspect_task_source(source)
    return {
        "source_filename": inspected.source.name,
        "source_sha256": inspected.source_sha256,
        "rows": inspected.row_count,
        "relations": 0,
        "grain": "one-row-one-observation",
        "columns": [
            {
                "name": item.name,
                "kind": item.kind,
                "non_empty": item.non_empty,
                "observed_min": item.minimum,
                "observed_max": item.maximum,
                "choices": list(item.choices),
            }
            for item in inspected.columns
        ],
        "notice": "観測最小値・最大値は要約です。物理的な許容範囲や目標値には自動で使いません。",
    }


@router.post("/inspect")
async def inspect_csv(file: UploadFile = File(...)) -> dict[str, Any]:
    temporary, source = await _uploaded_csv(file)
    try:
        return await run_in_threadpool(_inspection_payload, source)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("CSV_ONBOARDING_FAILED stage=inspect")
        raise HTTPException(422, _FAILURE_MESSAGES["inspect"]) from exc
    finally:
        temporary.cleanup()


def _fields_from_json(fields_json: str) -> tuple[ScaffoldField, ...]:
    raw_fields = json.loads(fields_json)
    if not isinstance(raw_fields, list):
        raise ValueError("fields must be a list")
    return tuple(
        ScaffoldField(
            **{
                **item,
                **{
                    key: tuple(item[key])
                    for key in (
                        "allowed_range",
                        "default_range",
                        "training_range",
                        "plausible_range",
                        "display_range",
                    )
                    if item.get(key) is not None
                },
            }
        )
        for item in raw_fields
    )


def _new_onboarding_paths(
    *,
    task_id: str,
    model_store_path: Path,
) -> tuple[Path, Path, str]:
    """Reserve paths that are absent before this browser-owned attempt."""

    task_store = validate_personal_task_store_path()
    task_root = (task_store / task_id).resolve()
    if task_root.parent != task_store.resolve() or task_root.exists():
        raise ValueError("task root is not available for onboarding")
    model_store = validate_personal_model_store_path(model_store_path)
    package_id = f"{task_id}-personal-1"
    packages_root = (model_store / "packages").resolve()
    package_root = (packages_root / package_id).resolve()
    if package_root.parent != packages_root or package_root.exists():
        raise ValueError("package root is not available for onboarding")
    return task_root, model_store, package_id


async def _rollback_prepare_attempt(
    *,
    request: Request,
    task_root: Path | None,
    model_store: Path | None,
    package_id: str | None,
    registration: DatasetRegistrationResult | None,
    registration_checkpoint: ManagedDatasetRegistrationCheckpoint | None,
    refresh: Any,
    runtime_published: bool,
) -> None:
    """Compensate only paths and catalog records reserved by this request."""

    if registration is not None and registration_checkpoint is not None:
        try:
            await run_in_threadpool(
                rollback_managed_dataset_registration,
                database=Path(request.app.state.workspace_database),
                registration=registration,
                checkpoint=registration_checkpoint,
            )
        except Exception:
            logger.exception("CSV_ONBOARDING_ROLLBACK_FAILED stage=dataset")
    if model_store is not None and package_id is not None:
        try:
            await run_in_threadpool(
                rollback_personal_package_attempt,
                package_id,
                store=model_store,
            )
        except Exception:
            logger.exception("CSV_ONBOARDING_ROLLBACK_FAILED stage=package")
    if task_root is not None and task_root.exists():
        try:
            await run_in_threadpool(shutil.rmtree, task_root)
        except Exception:
            logger.exception("CSV_ONBOARDING_ROLLBACK_FAILED stage=task")
    if runtime_published and refresh is not None:
        try:
            await refresh()
        except Exception:
            logger.exception("CSV_ONBOARDING_ROLLBACK_FAILED stage=runtime")


@router.post("/prepare")
async def prepare_csv_task(
    request: Request,
    file: UploadFile = File(...),
    task_id: str = Form(...),
    label: str = Form(...),
    estimator_id: str = Form("ridge.v1"),
    fields_json: str = Form(...),
    grain_confirmation: str = Form(...),
    relation_confirmation: str = Form(...),
) -> dict[str, Any]:
    """Create, verify, promote, register, and reload one reviewed CSV Task."""

    try:
        fields = _fields_from_json(fields_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.info("CSV_ONBOARDING_FAILED stage=fields error_type=%s", type(exc).__name__)
        raise HTTPException(422, _FAILURE_MESSAGES["fields"]) from exc
    try:
        temporary, source = await _uploaded_csv(file)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("CSV_ONBOARDING_FAILED stage=inspect")
        raise HTTPException(422, _FAILURE_MESSAGES["inspect"]) from exc
    try:
        lock = request.app.state.csv_onboarding_lock
        async with lock:
            task_root: Path | None = None
            model_store: Path | None = None
            package_id: str | None = None
            registration: DatasetRegistrationResult | None = None
            registration_checkpoint: ManagedDatasetRegistrationCheckpoint | None = None
            refresh = getattr(request.app.state, "refresh_task_resources", None)
            runtime_published = False
            stage = "storage"
            try:
                task_root, model_store, package_id = _new_onboarding_paths(
                    task_id=task_id,
                    model_store_path=Path(request.app.state.model_store_path),
                )
                stage = "prepare"
                result = await run_in_threadpool(
                    create_task_scaffold,
                    source=source,
                    task_id=task_id,
                    label=label,
                    fields=fields,
                    grain_confirmation=grain_confirmation,
                    relation_confirmation=relation_confirmation,
                    estimator_id=estimator_id,
                )
                if result.state != "ready" or result.profile_path is None:
                    raise ValueError("CSV onboarding requires a complete Task definition")
                candidate = result.root / "candidate-package"
                feature_dataset = result.root / "feature-dataset.json"
                await run_in_threadpool(
                    build_standard_package,
                    task_id,
                    result.source_path,
                    candidate,
                    feature_dataset,
                    package_id=package_id,
                    package_version="1.0.0",
                    replace=False,
                    estimator=estimator_id,
                    profile=result.profile_path,
                )
                await run_in_threadpool(
                    promote_personal_package,
                    task_id,
                    candidate,
                    result.source_path,
                    model_store,
                    profile=result.profile_path,
                )
                stage = "dataset"
                registration_checkpoint = await run_in_threadpool(
                    managed_dataset_registration_checkpoint,
                    Path(request.app.state.workspace_database),
                )
                registration = await run_in_threadpool(
                    register_managed_dataset,
                    database=Path(request.app.state.workspace_database),
                    source=result.source_path,
                    library_root=Path(request.app.state.data_library_root),
                    profile_path=result.profile_path,
                    name=label,
                    promote_existing_bundled=False,
                )
                if refresh is None:
                    raise RuntimeError("task resource refresh is not available")
                stage = "refresh"
                await refresh()
                runtime_published = True
                runtime_catalog = request.app.state.runtime_context.workspace_catalog
                dataset_view = next(
                    (
                        item
                        for item in runtime_catalog.list_dataset_view_revisions()
                        if item.id == registration.dataset_view_revision_id
                    ),
                    None,
                )
                package = next(
                    (
                        item
                        for item in runtime_catalog.list_model_package_refs(
                            include_archived=False,
                        )
                        if item.task_id == task_id and item.package_id == package_id
                    ),
                    None,
                )
                if dataset_view is None or package is None:
                    raise RuntimeError("refreshed resources do not match the registered Dataset")
                return {
                    "state": "ready",
                    "task_id": task_id,
                    "dataset_view_revision_id": dataset_view.id,
                    "dataset_revision_id": registration.dataset_revision_id,
                    "source_sha256": registration.source_sha256,
                    "model_package_ref_id": package.id,
                }
            except Exception as exc:
                logger.exception(
                    "CSV_ONBOARDING_FAILED stage=%s task_id=%s",
                    stage,
                    task_id,
                )
                await _rollback_prepare_attempt(
                    request=request,
                    task_root=task_root,
                    model_store=model_store,
                    package_id=package_id,
                    registration=registration,
                    registration_checkpoint=registration_checkpoint,
                    refresh=refresh,
                    runtime_published=runtime_published,
                )
                raise HTTPException(422, _FAILURE_MESSAGES[stage]) from exc
    finally:
        temporary.cleanup()
