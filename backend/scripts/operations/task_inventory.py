from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = REPOSITORY_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.modeling.active_package_history import (  # noqa: E402
    check_active_package_history,
    format_active_package_history,
)
from material_workbench.modeling.model_lifecycle import (  # noqa: E402
    ACTIVE_PACKAGES_PATH,
    load_active_packages,
    resolve_configured_package,
    validate_active_package_task_set,
)
from material_workbench.modeling.model_packages import ModelPackageLoader  # noqa: E402
from material_workbench.task_modules import registered_task_modules, resolve_task_source  # noqa: E402
from material_workbench.tasks.task_registry import load_task_contracts  # noqa: E402


DEFAULT_OUTPUT = REPOSITORY_ROOT / "docs" / "contracts" / "task-inventory.json"


def _repository_path(value: str | Path) -> str:
    path = Path(value).resolve()
    try:
        return path.relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def build_inventory() -> dict[str, Any]:
    modules = registered_task_modules()
    contracts = load_task_contracts()
    active = load_active_packages(ACTIVE_PACKAGES_PATH)
    validate_active_package_task_set(active, set(modules))
    if set(contracts) != set(modules):
        raise ValueError("TaskDefinition and TaskModule task sets differ")

    data_by_source: dict[str, Any] = {}
    tasks = []
    for task_id, module in modules.items():
        if task_id not in data_by_source:
            data_by_source[task_id] = module.data_loader(resolve_task_source(task_id))
        data = data_by_source[task_id]
        contract = contracts[task_id]
        package = ModelPackageLoader().load(
            resolve_configured_package(task_id, config_path=ACTIVE_PACKAGES_PATH)
        )
        selection = active.tasks[task_id]
        tasks.append({
            "task_id": task_id,
            "label": contract.task_definition.label,
            "outputs": [
                {"key": output.key, "label": output.label, "unit": output.unit}
                for output in contract.task_definition.outputs
            ],
            "source": {
                "kind": module.source_kind,
                "path": _repository_path(data.source_path),
                "sha256": data.source_sha256,
                "profile_id": data.profile_id,
                "profile_path": _repository_path(data.profile_path),
            },
            "active_package": {
                "path": selection.active,
                "previous": selection.previous,
                "package_id": package.manifest.package_id,
                "version": package.manifest.package_version,
                "manifest_sha256": package.manifest_sha256,
                "runtime_types": sorted({item.runtime_type for item in package.manifest.predictors}),
            },
            "runtime_operations": contract.runtime_capability.operations.model_dump(mode="json"),
            "application_capability": module.application.model_dump(mode="json"),
            "data_explorer": None if module.data_explorer is None else module.data_explorer.model_dump(mode="json"),
        })
    return {
        "schema_version": "task-inventory/v2",
        "generated_from": [
            "backend/src/material_workbench/task_modules.py",
            "backend/src/material_workbench/tasks/task_definitions",
            "models/active-packages.json",
            "resolved dataset input profiles",
            "resolved Model Package manifests",
        ],
        "tasks": tasks,
    }


def packaged_source_paths() -> list[str]:
    return sorted({
        _repository_path(REPOSITORY_ROOT / module.default_source)
        for module in registered_task_modules().values()
    })


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Generate or verify the production task inventory.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    parser.add_argument(
        "--print-source-paths",
        action="store_true",
        help="Print the unique registered default sources as JSON for packaging checks.",
    )
    args = parser.parse_args()
    if args.print_source_paths:
        print(json.dumps(packaged_source_paths(), ensure_ascii=False))
        return 0
    expected = json.dumps(build_inventory(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        try:
            current = args.output.read_text(encoding="utf-8")
        except OSError:
            current = ""
        if current != expected:
            print(f"Task inventory is stale: {args.output}", file=sys.stderr)
            return 1
        history = check_active_package_history()
        report = "\n".join(format_active_package_history(history))
        if not history.ok:
            print(report, file=sys.stderr)
            print(
                "active-packages.json is edited outside npm run model:activate",
                file=sys.stderr,
            )
            return 1
        print(f"Task inventory is current: {args.output}")
        print(report)
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
