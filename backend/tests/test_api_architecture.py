from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET_ROUTERS = (
    ROOT / "backend/src/material_workbench/api/catalog.py",
    ROOT / "backend/src/material_workbench/api/data_library.py",
    ROOT / "backend/src/material_workbench/api/chains.py",
)
FORBIDDEN_PREFIXES = (
    "material_workbench.data",
    "material_workbench.modeling",
    "material_workbench.persistence",
    "material_workbench.tasks",
)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_catalog_data_library_and_chain_routers_depend_only_on_application_and_contracts() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): sorted(
            imported
            for imported in _imports(path)
            if imported.startswith(FORBIDDEN_PREFIXES)
        )
        for path in TARGET_ROUTERS
    }

    assert not {
        path: imports for path, imports in violations.items() if imports
    }, (
        "API routers are transport boundaries. Move persistence/modeling/data/task "
        "coordination into an application use case and inject that use case instead."
    )


def test_chain_router_defines_no_api_local_pydantic_contracts() -> None:
    tree = ast.parse(TARGET_ROUTERS[2].read_text(encoding="utf-8"))
    local_classes = {
        node.name for node in tree.body if isinstance(node, ast.ClassDef)
    }

    assert local_classes == set(), (
        "Chain request/response contracts belong in material_workbench.contracts, "
        f"not the router: {sorted(local_classes)}"
    )
