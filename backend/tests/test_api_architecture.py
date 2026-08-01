from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TARGET_ROUTERS = (
    ROOT / "backend/src/decision_workbench/api/catalog.py",
    ROOT / "backend/src/decision_workbench/api/data_library.py",
    ROOT / "backend/src/decision_workbench/api/chains.py",
)
GENERIC_CANDIDATE_USE_CASES = (
    ROOT / "backend/src/decision_workbench/application/candidates.py",
    ROOT / "backend/src/decision_workbench/application/inference.py",
    ROOT / "backend/src/decision_workbench/application/decision_activity_robustness.py",
    ROOT / "backend/src/decision_workbench/application/decision_activity_counterfactual.py",
    ROOT / "backend/src/decision_workbench/application/decision_activity_difference.py",
)
EXPLICIT_POLICY_BOUNDARIES = {
    "curve sampling": ROOT
    / "backend/src/decision_workbench/modeling/curve_grid.py",
    "canonical training": ROOT
    / "backend/src/decision_workbench/modeling/model_lifecycle.py",
    "validation roles": ROOT
    / "backend/src/decision_workbench/modeling/training/feature_dataset.py",
}
FORBIDDEN_PREFIXES = (
    "decision_workbench.data",
    "decision_workbench.modeling",
    "decision_workbench.persistence",
    "decision_workbench.tasks",
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
        "Chain request/response contracts belong in decision_workbench.contracts, "
        f"not the router: {sorted(local_classes)}"
    )


def test_generic_candidate_use_cases_do_not_interpret_candidate_family_layout() -> None:
    forbidden = (
        "domain.candidate_inputs",
        "inputs.composition",
        "inputs.heat_pattern",
        "inputs.process",
        "heat.stage_temperature_c",
        "ls_mpm",
        "composition_totals",
    )
    violations = {
        path.relative_to(ROOT).as_posix(): [
            token
            for token in forbidden
            if token in path.read_text(encoding="utf-8")
        ]
        for path in GENERIC_CANDIDATE_USE_CASES
    }

    assert not {
        path: tokens
        for path, tokens in violations.items()
        if tokens
    }, (
        "Generic application use cases must resolve path/value/balance/heat "
        "semantics through CandidateFamilyAdapter."
    )


def test_generic_modeling_boundaries_do_not_reintroduce_ambient_or_family_magic() -> None:
    sources = {
        name: path.read_text(encoding="utf-8")
        for name, path in EXPLICIT_POLICY_BOUNDARIES.items()
    }

    assert "ContextVar" not in sources["curve sampling"]
    assert "TabularDatasetProfile" not in sources["canonical training"]
    assert "__class__.__name__" not in sources["canonical training"]
    assert "fold_ids == -" not in sources["validation roles"]
