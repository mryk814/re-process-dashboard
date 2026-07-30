"""Guard the dependency directions established by the Task composition split."""
from __future__ import annotations

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "material_workbench"


def _module_name(path: Path) -> str | None:
    try:
        relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    except ValueError:
        return None
    return "material_workbench." + ".".join(relative.parts)


def _imports(path: Path, *, top_level_only: bool = False) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    nodes = tree.body if top_level_only else ast.walk(tree)
    current_module = _module_name(path)
    current_package = (
        current_module.rsplit(".", 1)[0]
        if current_module is not None
        else None
    )
    for node in nodes:
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported = node.module or ""
            if node.level and current_package is not None:
                package_parts = current_package.split(".")
                prefix = package_parts[: len(package_parts) - node.level + 1]
                base = ".".join(part for part in (*prefix, imported) if part)
            elif imported:
                base = imported
            else:
                continue
            imports.add(base)
            imports.update(
                f"{base}.{alias.name}"
                for alias in node.names
                if alias.name != "*"
            )
    return imports


def test_removed_integration_hubs_do_not_return() -> None:
    assert not (PACKAGE_ROOT / "task_modules.py").exists()
    assert not (PACKAGE_ROOT / "domain" / "services.py").exists()
    assert not (PACKAGE_ROOT / "tasks" / "project_runtime_resolver.py").exists()
    assert not (PACKAGE_ROOT / "data" / "dataset_registration.py").exists()
    assert not (
        PACKAGE_ROOT / "persistence" / "workspace_catalog_bootstrap.py"
    ).exists()


def test_task_ports_descriptors_and_catalog_have_no_runtime_or_storage_dependency() -> None:
    forbidden = (
        "material_workbench.application",
        "material_workbench.modeling",
        "material_workbench.persistence",
        "material_workbench.tasks",
    )
    offenders = {
        path.relative_to(PACKAGE_ROOT).as_posix(): sorted(
            name
            for name in _imports(path)
            if name.startswith(forbidden)
        )
        for path in (
            PACKAGE_ROOT / "task_composition" / name
            for name in ("ports.py", "descriptors.py", "catalog.py")
        )
    }
    assert {path: names for path, names in offenders.items() if names} == {}


def test_builtin_composition_defers_runtime_imports_until_a_factory_is_called() -> None:
    path = PACKAGE_ROOT / "task_composition" / "builtin_tasks.py"
    forbidden = (
        "material_workbench.application",
        "material_workbench.modeling",
        "material_workbench.persistence",
        "material_workbench.tasks",
    )
    assert sorted(
        name
        for name in _imports(path, top_level_only=True)
        if name.startswith(forbidden)
    ) == []


def test_tasks_and_data_do_not_own_application_transactions() -> None:
    forbidden_by_package = {
        "tasks": ("material_workbench.application", "material_workbench.persistence"),
        "data": ("material_workbench.application", "material_workbench.persistence"),
    }
    offenders: dict[str, list[str]] = {}
    for package, forbidden in forbidden_by_package.items():
        for path in (PACKAGE_ROOT / package).rglob("*.py"):
            imports = sorted(
                name
                for name in _imports(path)
                if name.startswith(forbidden)
            )
            if imports:
                offenders[path.relative_to(PACKAGE_ROOT).as_posix()] = imports
    assert offenders == {}


def test_proposal_service_has_no_dataset_model_or_material_dependency() -> None:
    path = PACKAGE_ROOT / "application" / "proposal_service.py"
    forbidden = (
        "material_workbench.data",
        "material_workbench.modeling",
        "material_workbench.application.candidate_spreadsheet",
        "material_workbench.application.material_lineage_candidates",
        "numpy",
        "openpyxl",
    )
    assert sorted(
        name
        for name in _imports(path)
        if name.startswith(forbidden)
    ) == []


def test_material_lineage_candidate_logic_stays_isolated() -> None:
    path = PACKAGE_ROOT / "application" / "material_lineage_candidates.py"
    forbidden = (
        "material_workbench.application.candidate_spreadsheet",
        "material_workbench.application.proposal_service",
        "material_workbench.persistence",
        "material_workbench.tasks",
        "openpyxl",
    )
    assert sorted(
        name
        for name in _imports(path)
        if name.startswith(forbidden)
    ) == []


def test_removed_modules_are_not_imported_or_named_by_runtime_and_scripts() -> None:
    removed_modules = {
        "material_workbench.domain.services",
        "material_workbench.task_modules",
        "material_workbench.tasks.project_runtime_resolver",
        "material_workbench.data.dataset_registration",
        "material_workbench.persistence.workspace_catalog_bootstrap",
    }
    removed_paths = {
        "backend/src/material_workbench/domain/services.py",
        "backend/src/material_workbench/task_modules.py",
        "backend/src/material_workbench/tasks/project_runtime_resolver.py",
        "backend/src/material_workbench/data/dataset_registration.py",
        "backend/src/material_workbench/persistence/workspace_catalog_bootstrap.py",
    }
    roots = (
        PACKAGE_ROOT,
        PACKAGE_ROOT.parents[1] / "scripts",
        PACKAGE_ROOT.parents[1] / "tests",
    )
    offenders: dict[str, list[str]] = {}
    for root in roots:
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            references = sorted(removed_modules & _imports(path))
            references.extend(
                sorted(
                    value
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Constant)
                    and isinstance((value := node.value), str)
                    and value.replace("\\", "/") in removed_paths
                    and path.resolve() != Path(__file__).resolve()
                )
            )
            if references:
                offenders[path.relative_to(PACKAGE_ROOT.parents[2]).as_posix()] = references
    assert offenders == {}
