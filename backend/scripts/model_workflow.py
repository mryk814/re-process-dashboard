from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shutil
import sys
from typing import Any


BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.modeling.active_package_history import (  # noqa: E402
    check_active_package_history,
    classify_rollback_target,
    current_task_input_contract_digests,
    rollback_target_note,
    rollback_target_reason,
)
from material_workbench.modeling.model_lifecycle import (  # noqa: E402
    ACTIVE_PACKAGES_PATH,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    dataset_profile_digest,
    load_active_packages,
    resolve_configured_package,
    rollback_active_package,
    set_active_package,
    staged_package_destination,
    validate_active_package_task_set,
    validate_lifecycle_metadata,
)
from material_workbench.modeling.model_package_verify import verify_model_package  # noqa: E402
from material_workbench.modeling.model_packages import MissingOptionalDependency, ModelPackageLoader, PackageContractError  # noqa: E402
from material_workbench.data.profile_document import supported_task_ids  # noqa: E402
from material_workbench.tasks.task_registry import load_task_contracts  # noqa: E402
from material_workbench.task_modules import PRIMARY_DEFAULT_SOURCE, registered_task_modules, resolve_task_source, task_module  # noqa: E402


TASKS = tuple(registered_task_modules())
DEFAULT_SOURCE = PRIMARY_DEFAULT_SOURCE


def _task_source(task_id: str, source: Path) -> Path:
    return resolve_task_source(task_id, source)


def _load_task_data(task_id: str, source: Path):
    return task_module(task_id).data_loader(resolve_task_source(task_id, source))


def _write_json(path: Path, payload: Any, *, replace: bool) -> None:
    if path.exists() and not replace:
        try:
            if json.loads(path.read_text(encoding="utf-8")) == payload:
                return
        except (OSError, ValueError):
            pass
        raise FileExistsError(f"refusing to replace different existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def export_dataset(task_id: str, source: Path, output: Path, *, replace: bool) -> dict[str, Any]:
    source = _task_source(task_id, source)
    data = _load_task_data(task_id, source)
    payload = canonical_training_dataset(task_id, data, load_task_contracts()[task_id])
    _write_json(output, payload, replace=replace)
    return {
        "path": str(output.resolve()),
        "rows": len(payload["rows"]),
        "feature_dataset_id": canonical_training_dataset_digest(payload),
    }


def diagnose_source(
    source: Path,
    *,
    task_id: str | None,
    profile: Path | None,
) -> dict[str, Any]:
    source = source.resolve(strict=True)
    profile_tasks: tuple[str, ...] = ()
    selected_profile_digest: str | None = None
    if profile is not None:
        profile = profile.resolve(strict=True)
        document = json.loads(profile.read_text(encoding="utf-8"))
        profile_tasks = supported_task_ids(document)
        selected_profile_digest = dataset_profile_digest(profile)

    if task_id is None:
        matching = [item for item in profile_tasks if item in TASKS]
        if len(matching) != 1:
            return {
                "route": "new_task_or_profile",
                "source": str(source),
                "profile": str(profile) if profile else None,
                "profile_task_ids": list(profile_tasks),
                "reason": "既存Taskを一意に特定できません。",
                "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
            }
        task_id = matching[0]

    common = {
        "task_id": task_id,
        "source": str(source),
        "profile": str(profile) if profile else None,
        "profile_task_ids": list(profile_tasks),
    }
    if task_id not in TASKS:
        return {
            "route": "new_task_or_profile",
            **common,
            "reason": "登録済みTaskではありません。",
            "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
        }
    if profile_tasks and task_id not in profile_tasks:
        return {
            "route": "new_task_or_profile",
            **common,
            "reason": "選択したProfileは指定Taskを宣言していません。",
            "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
        }

    try:
        data = _load_task_data(task_id, source)
    except (OSError, ValueError) as exc:
        return {
            "route": "new_task_or_profile",
            **common,
            "reason": f"登録済みProfileで読み込めません: {exc}",
            "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
        }

    active_profile_path = Path(data.profile_path)
    active_profile_digest = (
        dataset_profile_digest(active_profile_path)
        if active_profile_path.is_file()
        else None
    )
    if (
        selected_profile_digest is not None
        and active_profile_digest != selected_profile_digest
    ):
        return {
            "route": "new_task_or_profile",
            **common,
            "reason": (
                "選択したProfileは登録済みTaskの有効Profileと異なります。"
                "暗黙に置き換えず、Profile追加手順へ分岐します。"
            ),
            "active_profile": str(active_profile_path),
            "active_profile_digest": active_profile_digest,
            "selected_profile_digest": selected_profile_digest,
            "next": "docs/operations/dataset-input-profile.md#新しいデータフローを追加する場合",
        }
    return {
        "route": "existing_task_replacement",
        "task_id": task_id,
        "source": str(source),
        "source_sha256": data.source_sha256,
        "profile": str(active_profile_path),
        "profile_digest": active_profile_digest,
        "eligible_rows": sum(
            1
            for row in data.observations
            if row.get("eligible")
            and row.get("task_id", task_id) == task_id
        ),
        "next": (
            "npm run model:build -- --task "
            f"{task_id} --source \"{source}\" --package-id <new-id> "
            "--package-version <new-version>"
        ),
    }


def build_package(
    task_id: str,
    source: Path,
    output: Path,
    dataset_output: Path,
    *,
    package_id: str,
    package_version: str,
    replace: bool,
) -> dict[str, Any]:
    if output.exists() and not replace:
        raise FileExistsError(f"refusing to replace existing model package: {output}")
    source = _task_source(task_id, source)
    dataset = export_dataset(task_id, source, dataset_output, replace=replace)
    task_module(task_id).model_builder(
        source,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
    )
    report = verify_model_package(output, task_id=task_id, source=source)
    return {"dataset": dataset, "package": report.model_dump()}


def promote_package(
    task_id: str,
    package: Path,
    source: Path,
    config: Path,
    *,
    activate: bool,
) -> dict[str, Any]:
    package = package.resolve(strict=True)
    source = _task_source(task_id, source)
    verify_model_package(package, task_id=task_id, source=source)
    loaded = ModelPackageLoader().load(package)
    package_id = loaded.manifest.package_id
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", package_id):
        raise PackageContractError(
            "package_id must be a filesystem-safe immutable identifier"
        )
    trusted_root = config.resolve().parent / "packages"
    destination = trusted_root / package_id
    promoted = False
    if destination.exists():
        existing = ModelPackageLoader().load(destination)
        if existing.manifest_sha256 != loaded.manifest_sha256:
            raise FileExistsError(
                f"trusted package ID already exists with different content: {destination}"
            )
    else:
        with staged_package_destination(destination, replace=False) as staging:
            shutil.copytree(package, staging)
        promoted = True
    trusted_report = verify_model_package(
        destination,
        task_id=task_id,
        source=source,
    )
    selection = None
    if activate:
        updated = set_active_package(task_id, destination, config_path=config)
        selection = updated.tasks[task_id].model_dump()
    return {
        "task_id": task_id,
        "promoted": promoted,
        "trusted_package": str(destination),
        "package": trusted_report.model_dump(),
        "activation": selection,
        "restart_required": activate,
        "next": (
            "npm run dev を停止して再実行し、データライブラリから"
            "このDataset/TaskでProjectを作成または切り替えてください。"
            if activate
            else (
                "npm run model:activate -- --task "
                f"{task_id} --source \"{source}\" --package \"{destination}\""
            )
        ),
    }


def activate_package(task_id: str, package: Path, source: Path, config: Path) -> dict[str, Any]:
    report = verify_model_package(package, task_id=task_id, source=_task_source(task_id, source))
    updated = set_active_package(task_id, package, config_path=config)
    selection = updated.tasks[task_id]
    return {
        "task_id": task_id,
        "active": selection.active,
        "previous": selection.previous,
        "package": report.model_dump(),
        "restart_required": True,
    }


def rollback_package(task_id: str, source: Path, config: Path) -> dict[str, Any]:
    before = load_active_packages(config)
    selection = before.tasks[task_id]
    if selection.previous is None:
        note = rollback_target_note(task_id, check_active_package_history(config_path=config))
        raise PackageContractError(
            f"no previous active package is recorded for task {task_id}"
            + (
                f" — {note}"
                if note
                else "; activeの切替は npm run model:activate を通す（JSONを直接編集するとpreviousが記録されない）"
            )
        )
    models_root = config.resolve().parent
    status = classify_rollback_target(
        task_id,
        selection.previous,
        models_root=models_root,
        contract_digests=current_task_input_contract_digests(),
    )
    if status != "available":
        raise PackageContractError(
            f"previous active package {selection.previous} cannot be activated for task {task_id}"
            f" — {rollback_target_reason(status)}"
        )
    previous = (models_root / selection.previous).resolve(strict=True)
    report = verify_model_package(previous, task_id=task_id, source=_task_source(task_id, source))
    updated = rollback_active_package(task_id, config_path=config)
    current = updated.tasks[task_id]
    return {
        "task_id": task_id,
        "active": current.active,
        "previous": current.previous,
        "package": report.model_dump(),
        "restart_required": True,
    }


def package_status(config: Path) -> dict[str, Any]:
    contracts = load_task_contracts()
    configured = load_active_packages(config)
    validate_active_package_task_set(configured, set(TASKS))
    tasks: dict[str, Any] = {}
    data_by_source: dict[str, Any] = {}
    for task_id, selection in configured.tasks.items():
        module = task_module(task_id)
        if task_id not in data_by_source:
            data_by_source[task_id] = module.data_loader(resolve_task_source(task_id))
        data = data_by_source[task_id]
        root = resolve_configured_package(task_id, config_path=config)
        package = ModelPackageLoader().load(root)
        validate_lifecycle_metadata(package, contracts[task_id], profile_path=Path(data.profile_path))
        tasks[task_id] = {
            "active": selection.active,
            "previous": selection.previous,
            "package_id": package.manifest.package_id,
            "package_version": package.manifest.package_version,
            "manifest_sha256": package.manifest_sha256,
        }
    return {
        "schema_version": configured.schema_version,
        "tasks": tasks,
        "history": check_active_package_history(config_path=config).model_dump(mode="json"),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build, verify, activate, and roll back trusted Model Packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    data = subparsers.add_parser("data", help="Export the canonical training dataset used by a task pipeline.")
    data.add_argument("--task", required=True, choices=TASKS)
    data.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    data.add_argument("--output", type=Path, required=True)
    data.add_argument("--replace", action="store_true")

    diagnose = subparsers.add_parser(
        "diagnose",
        help="Decide whether a source can replace data for an existing Task.",
    )
    diagnose.add_argument("--task")
    diagnose.add_argument("--source", type=Path, required=True)
    diagnose.add_argument("--profile", type=Path)

    build = subparsers.add_parser("build", help="Export canonical data, train, package, and verify one task.")
    build.add_argument("--task", required=True, choices=TASKS)
    build.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    build.add_argument("--output", type=Path)
    build.add_argument("--dataset-output", type=Path)
    build.add_argument("--package-id", required=True)
    build.add_argument("--package-version", required=True)
    build.add_argument("--replace", action="store_true")

    verify = subparsers.add_parser("verify", help="Run loader, lifecycle, adapter, and production smoke checks.")
    verify.add_argument("--task", required=True, choices=TASKS)
    verify.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    verify.add_argument("--package", type=Path, required=True)

    activate = subparsers.add_parser("activate", help="Verify then update the trusted active-package reference.")
    activate.add_argument("--task", required=True, choices=TASKS)
    activate.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    activate.add_argument("--package", type=Path, required=True)
    activate.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)

    promote = subparsers.add_parser(
        "promote",
        help="Verify and immutably copy a candidate into trusted models.",
    )
    promote.add_argument("--task", required=True, choices=TASKS)
    promote.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    promote.add_argument("--package", type=Path, required=True)
    promote.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)
    promote.add_argument("--activate", action="store_true")

    rollback = subparsers.add_parser("rollback", help="Verify and restore the previously active package.")
    rollback.add_argument("--task", required=True, choices=TASKS)
    rollback.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    rollback.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)

    status = subparsers.add_parser("status", help="Show trusted active package IDs, versions, and hashes.")
    status.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    arguments = _parser().parse_args()
    try:
        if arguments.command == "diagnose":
            result = diagnose_source(
                arguments.source,
                task_id=arguments.task,
                profile=arguments.profile,
            )
        elif arguments.command == "data":
            result = export_dataset(arguments.task, arguments.source, arguments.output, replace=arguments.replace)
        elif arguments.command == "build":
            output = (
                arguments.output
                or Path("artifacts/model-package-candidates")
                / arguments.package_id
            )
            dataset_output = arguments.dataset_output or Path("artifacts/model-data") / f"{arguments.task}.json"
            result = build_package(
                arguments.task,
                arguments.source,
                output,
                dataset_output,
                package_id=arguments.package_id,
                package_version=arguments.package_version,
                replace=arguments.replace,
            )
        elif arguments.command == "verify":
            result = verify_model_package(arguments.package, task_id=arguments.task, source=_task_source(arguments.task, arguments.source)).model_dump()
        elif arguments.command == "promote":
            result = promote_package(
                arguments.task,
                arguments.package,
                arguments.source,
                arguments.config,
                activate=arguments.activate,
            )
        elif arguments.command == "activate":
            result = activate_package(arguments.task, arguments.package, arguments.source, arguments.config)
        elif arguments.command == "rollback":
            result = rollback_package(arguments.task, arguments.source, arguments.config)
        else:
            result = package_status(arguments.config)
    except (MissingOptionalDependency, OSError, ValueError, PackageContractError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
