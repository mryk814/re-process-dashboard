"""Guard the dependency directions established by the Task composition split."""
from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "material_workbench"


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    return imports


def test_removed_integration_hubs_do_not_return() -> None:
    assert not (PACKAGE_ROOT / "task_modules.py").exists()
    assert not (PACKAGE_ROOT / "tasks" / "project_runtime_resolver.py").exists()
    assert not (PACKAGE_ROOT / "data" / "dataset_registration.py").exists()


def test_task_composition_has_no_top_level_runtime_or_storage_dependency() -> None:
    forbidden = (
        "material_workbench.application",
        "material_workbench.modeling",
        "material_workbench.persistence",
        "material_workbench.tasks",
    )
    offenders = {
        path.relative_to(PACKAGE_ROOT).as_posix(): sorted(
            name
            for name in _top_level_imports(path)
            if name.startswith(forbidden)
        )
        for path in (PACKAGE_ROOT / "task_composition").glob("*.py")
    }
    assert {path: names for path, names in offenders.items() if names} == {}


def test_tasks_and_data_do_not_own_application_transactions() -> None:
    forbidden_by_package = {
        "tasks": ("material_workbench.application", "material_workbench.persistence"),
        "data": ("material_workbench.application", "material_workbench.persistence"),
    }
    offenders: dict[str, list[str]] = {}
    for package, forbidden in forbidden_by_package.items():
        for path in (PACKAGE_ROOT / package).glob("*.py"):
            imports = sorted(
                name
                for name in _top_level_imports(path)
                if name.startswith(forbidden)
            )
            if imports:
                offenders[path.relative_to(PACKAGE_ROOT).as_posix()] = imports
    assert offenders == {}
