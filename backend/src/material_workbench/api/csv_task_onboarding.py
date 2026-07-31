"""UI-only onboarding for a new tabular CSV prediction task.

The browser never receives a path or a CLI command.  The uploaded CSV is copied
only into the user-owned Task store and then into the managed Data Library.
"""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from material_workbench.application.dataset_registration import register_managed_dataset
from material_workbench.developer_experience.task_scaffolding import (
    ScaffoldField,
    create_task_scaffold,
    inspect_task_source,
)
from material_workbench.application.personal_task_packages import (
    build_standard_package,
    promote_personal_package,
)


router = APIRouter(prefix="/api/data-library/csv-onboarding", tags=["csv-onboarding"])
MAX_CSV_BYTES = 100 * 1024 * 1024


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
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(422, str(exc) or "CSVを確認できませんでした") from exc
    finally:
        temporary.cleanup()


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
        raw_fields = json.loads(fields_json)
        if not isinstance(raw_fields, list):
            raise ValueError("列の設定が不正です")
        fields = tuple(
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
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(422, f"列の設定を確認してください: {exc}") from exc
    temporary, source = await _uploaded_csv(file)
    try:
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
            return {"state": "draft", "unresolved": list(result.unresolved)}
        package_id = f"{task_id}-personal-1"
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
            Path(request.app.state.model_store_path),
            profile=result.profile_path,
        )
        registration = await run_in_threadpool(
            register_managed_dataset,
            database=Path(request.app.state.workspace_database),
            source=result.source_path,
            library_root=Path(request.app.state.data_library_root),
            profile_path=result.profile_path,
            name=label,
        )
        refresh = getattr(request.app.state, "refresh_task_resources", None)
        if refresh is None:
            raise RuntimeError("個人Taskの再読込を準備できていません")
        refreshed = await refresh()
        package = next(
            (
                item for item in request.app.state.runtime_context.workspace_catalog
                .list_model_package_refs(include_archived=False)
                if item.task_id == task_id and item.package_id == package_id
            ),
            None,
        )
        if package is None:
            raise RuntimeError("再読込後にModel Packageを確認できません")
        return {
            "state": "ready",
            "task_id": task_id,
            "dataset_view_revision_id": registration.dataset_view_revision_id,
            "dataset_revision_id": registration.dataset_revision_id,
            "source_sha256": registration.source_sha256,
            "model_package_ref_id": package.id,
            "refreshed_task_ids": refreshed.added_task_ids,
            "refreshed_model_package_ids": refreshed.added_model_package_ids,
        }
    except (OSError, RuntimeError, ValueError) as exc:
        raise HTTPException(422, str(exc) or "CSV Taskを準備できませんでした") from exc
    finally:
        temporary.cleanup()
