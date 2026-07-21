from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


BACKEND_SRC = Path(__file__).resolve().parents[1] / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.importer import load_workbook_data  # noqa: E402
from material_workbench.model_lifecycle import (  # noqa: E402
    ACTIVE_PACKAGES_PATH,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    load_active_packages,
    resolve_configured_package,
    rollback_active_package,
    set_active_package,
    validate_lifecycle_metadata,
)
from material_workbench.model_package_verify import verify_model_package  # noqa: E402
from material_workbench.model_packages import MissingOptionalDependency, ModelPackageLoader, PackageContractError  # noqa: E402
from material_workbench.task_registry import load_task_contracts  # noqa: E402


TASKS = ("annealed-properties-v1", "hot-rolled-properties-v1", "flank-wear-v1")
DEFAULT_SOURCE = Path("data/source/process_dashboard_realistic_excel_v2.xlsx")
FLANK_WEAR_SOURCE = Path("data/source/cutting_tool_flank_wear_synthetic_dataset.xlsx")


def _task_source(task_id: str, source: Path) -> Path:
    if task_id == "flank-wear-v1" and source == DEFAULT_SOURCE:
        return FLANK_WEAR_SOURCE
    return source


def _load_task_data(task_id: str, source: Path):
    if task_id == "flank-wear-v1":
        from material_workbench.flank_wear import load_flank_wear_data

        return load_flank_wear_data(source)
    return load_workbook_data(source)


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


def build_package(task_id: str, source: Path, output: Path, dataset_output: Path, *, replace: bool) -> dict[str, Any]:
    if output.exists() and not replace:
        raise FileExistsError(f"refusing to replace existing model package: {output}")
    source = _task_source(task_id, source)
    dataset = export_dataset(task_id, source, dataset_output, replace=replace)
    if task_id == "annealed-properties-v1":
        from build_default_model_package import build
    elif task_id == "hot-rolled-properties-v1":
        from build_hot_rolling_model_package import build
    else:
        from build_flank_wear_model_package import build
    build(source, output, replace=replace)
    report = verify_model_package(output, task_id=task_id, source=source)
    return {"dataset": dataset, "package": report.model_dump()}


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
        raise PackageContractError(f"no previous active package is recorded for task {task_id}")
    previous = (config.resolve().parent / selection.previous).resolve(strict=True)
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
    tasks: dict[str, Any] = {}
    for task_id, selection in configured.tasks.items():
        root = resolve_configured_package(task_id, config_path=config)
        package = ModelPackageLoader().load(root)
        validate_lifecycle_metadata(package, contracts[task_id])
        tasks[task_id] = {
            "active": selection.active,
            "previous": selection.previous,
            "package_id": package.manifest.package_id,
            "package_version": package.manifest.package_version,
            "manifest_sha256": package.manifest_sha256,
        }
    return {"schema_version": configured.schema_version, "tasks": tasks}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build, verify, activate, and roll back trusted Model Packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    data = subparsers.add_parser("data", help="Export the canonical training dataset used by a task pipeline.")
    data.add_argument("--task", required=True, choices=TASKS)
    data.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    data.add_argument("--output", type=Path, required=True)
    data.add_argument("--replace", action="store_true")

    build = subparsers.add_parser("build", help="Export canonical data, train, package, and verify one task.")
    build.add_argument("--task", required=True, choices=TASKS)
    build.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--dataset-output", type=Path)
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

    rollback = subparsers.add_parser("rollback", help="Verify and restore the previously active package.")
    rollback.add_argument("--task", required=True, choices=TASKS)
    rollback.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    rollback.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)

    status = subparsers.add_parser("status", help="Show trusted active package IDs, versions, and hashes.")
    status.add_argument("--config", type=Path, default=ACTIVE_PACKAGES_PATH)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "data":
            result = export_dataset(arguments.task, arguments.source, arguments.output, replace=arguments.replace)
        elif arguments.command == "build":
            dataset_output = arguments.dataset_output or Path("artifacts/model-data") / f"{arguments.task}.json"
            result = build_package(arguments.task, arguments.source, arguments.output, dataset_output, replace=arguments.replace)
        elif arguments.command == "verify":
            result = verify_model_package(arguments.package, task_id=arguments.task, source=_task_source(arguments.task, arguments.source)).model_dump()
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
