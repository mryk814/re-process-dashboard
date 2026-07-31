"""Guard the dependency directions established by the Task composition split."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "decision_workbench"


def _module_name(path: Path) -> str | None:
    try:
        relative = path.relative_to(PACKAGE_ROOT).with_suffix("")
    except ValueError:
        return None
    return "decision_workbench." + ".".join(relative.parts)


def _imports(path: Path, *, top_level_only: bool = False) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    nodes = tree.body if top_level_only else ast.walk(tree)
    current_module = _module_name(path)
    current_package = current_module.rsplit(".", 1)[0] if current_module is not None else None
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
            imports.update(f"{base}.{alias.name}" for alias in node.names if alias.name != "*")
    return imports


def test_removed_integration_hubs_do_not_return() -> None:
    assert not (PACKAGE_ROOT / "task_modules.py").exists()
    assert not (PACKAGE_ROOT / "domain" / "services.py").exists()
    assert not (PACKAGE_ROOT / "task_composition" / "builtin_tasks.py").exists()
    assert not (PACKAGE_ROOT / "tasks" / "project_runtime_resolver.py").exists()
    assert not (PACKAGE_ROOT / "data" / "dataset_registration.py").exists()
    assert not (PACKAGE_ROOT / "data" / "dataset_profile.py").exists()
    assert not (PACKAGE_ROOT / "persistence" / "workspace_catalog_bootstrap.py").exists()
    assert not (PACKAGE_ROOT / "application" / "chain_execution.py").exists()


def test_chain_use_cases_depend_on_explicit_plan_and_stage_boundaries() -> None:
    application = PACKAGE_ROOT / "application"
    chain = application / "chain"
    assert {
        "__init__.py",
        "plan.py",
        "stage_execution.py",
        "execution.py",
        "snapshot.py",
    } <= {path.name for path in chain.iterdir()}
    assert not any(
        (application / filename).exists()
        for filename in (
            "chain_execution_plan.py",
            "chain_stage_execution.py",
            "chain_execution_use_case.py",
            "chain_snapshot_use_case.py",
        )
    )
    assert not any(
        imported.startswith("decision_workbench.application.chain_execution")
        for path in application.rglob("*.py")
        for imported in _imports(path)
    )


def test_contract_resource_families_do_not_restore_the_schemas_hub() -> None:
    contracts = PACKAGE_ROOT / "contracts"
    assert not (contracts / "schemas.py").exists()
    expected_families = {
        "candidate_project_contracts.py",
        "data_exploration_contracts.py",
        "data_library_contracts.py",
        "evidence_contracts.py",
        "prediction_catalog_contracts.py",
        "screening_contracts.py",
    }
    assert expected_families <= {path.name for path in contracts.glob("*_contracts.py")}

    roots = (
        PACKAGE_ROOT,
        PACKAGE_ROOT.parents[1] / "scripts",
        PACKAGE_ROOT.parents[1] / "tests",
    )
    offenders = {
        path.relative_to(PACKAGE_ROOT.parents[2]).as_posix()
        for root in roots
        for path in root.rglob("*.py")
        if "decision_workbench.contracts.schemas" in _imports(path)
    }
    assert offenders == set()

def test_tabular_regression_boundaries_are_one_way_without_a_legacy_facade() -> None:
    modeling = PACKAGE_ROOT / "modeling"
    tabular = modeling / "tabular"

    assert not (modeling / "tabular_regression.py").exists()
    assert {"__init__.py", "profile.py", "data.py", "features.py", "runtime.py"} <= {
        path.name for path in tabular.iterdir()
    }

    init_tree = ast.parse((tabular / "__init__.py").read_text(encoding="utf-8"))
    assert not [
        node
        for node in init_tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]

    imports = {
        name: _imports(tabular / f"{name}.py")
        for name in ("profile", "data", "features", "runtime")
    }
    tabular_prefix = "decision_workbench.modeling.tabular"
    assert not {
        name
        for name in imports["profile"]
        if name.startswith(tabular_prefix)
    }
    assert not {
        name
        for name in imports["data"]
        if name.startswith((f"{tabular_prefix}.features", f"{tabular_prefix}.runtime"))
    }
    assert not {
        name
        for name in imports["features"]
        if name.startswith((f"{tabular_prefix}.data", f"{tabular_prefix}.runtime"))
    }
    assert not {
        name
        for name in imports["runtime"]
        if name.startswith(f"{tabular_prefix}.profile")
    }


def test_profile_package_has_one_way_schema_validation_and_canonicalization_dependencies() -> None:
    profiles = PACKAGE_ROOT / "data" / "profiles"
    imports = {
        name: _imports(profiles / f"{name}.py")
        for name in ("schema", "loading", "validation", "canonicalization")
    }
    workbook_io = (
        "openpyxl",
        "decision_workbench.data.importer",
        "decision_workbench.data.profiles.canonicalization",
    )
    assert (
        sorted(
            name
            for module in ("schema", "loading")
            for name in imports[module]
            if name.startswith(workbook_io)
        )
        == []
    )
    assert (
        sorted(
            name
            for name in imports["schema"]
            if name.startswith("decision_workbench.data.profiles.")
        )
        == []
    )
    assert (
        sorted(
            name
            for name in imports["validation"]
            if name.startswith(
                (
                    "decision_workbench.data.profiles.loading",
                    "decision_workbench.data.profiles.canonicalization",
                )
            )
        )
        == []
    )
    assert (
        sorted(
            name
            for name in imports["canonicalization"]
            if name.startswith("decision_workbench.data.profiles.loading")
        )
        == []
    )
    assert "decision_workbench.data.profiles.validation" in imports["canonicalization"]


def test_model_adapter_ports_registry_and_verification_are_one_way() -> None:
    packages = PACKAGE_ROOT / "modeling" / "packages"
    ports = packages / "ports.py"
    registry = packages / "registry.py"
    verification = packages / "verification.py"
    loader = packages / "loader.py"
    adapters = PACKAGE_ROOT / "adapters"

    ports_forbidden = (
        "decision_workbench.adapters",
        "decision_workbench.modeling.packages.registry",
        "decision_workbench.modeling.packages.verification",
        "decision_workbench.modeling.packages.loader",
    )
    assert sorted(
        name for name in _imports(ports) if name.startswith(ports_forbidden)
    ) == []
    assert sorted(
        name
        for name in _imports(registry)
        if name.startswith(
            (
                "decision_workbench.modeling.packages.verification",
                "decision_workbench.modeling.packages.loader",
            )
        )
    ) == []
    assert sorted(
        name
        for name in _imports(verification)
        if name.startswith("decision_workbench.adapters")
    ) == []
    assert sorted(
        name for name in _imports(loader) if name.startswith("decision_workbench.adapters")
    ) == []
    verification_definitions = {
        node.name
        for node in ast.parse(verification.read_text(encoding="utf-8")).body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    loader_definitions = {
        node.name
        for node in ast.parse(loader.read_text(encoding="utf-8")).body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "ModelPackageLoader" not in verification_definitions
    assert "ModelPackageLoader" in loader_definitions

    offenders = {
        path.name: sorted(
            name
            for name in _imports(path)
            if name.startswith(
                (
                    "decision_workbench.modeling.packages.verification",
                    "decision_workbench.modeling.packages.loader",
                )
            )
        )
        for path in adapters.glob("*.py")
    }
    assert {path: names for path, names in offenders.items() if names} == {}


def test_task_ports_descriptors_and_catalog_have_no_runtime_or_storage_dependency() -> None:
    forbidden = (
        "decision_workbench.application",
        "decision_workbench.modeling",
        "decision_workbench.persistence",
        "decision_workbench.tasks",
    )
    offenders = {
        path.relative_to(PACKAGE_ROOT).as_posix(): sorted(
            name for name in _imports(path) if name.startswith(forbidden)
        )
        for path in (
            PACKAGE_ROOT / "task_composition" / name
            for name in ("ports.py", "descriptors.py", "catalog.py")
        )
    }
    assert {path: names for path, names in offenders.items() if names} == {}


def test_builtin_composition_defers_runtime_imports_until_a_factory_is_called() -> None:
    forbidden = (
        "decision_workbench.application",
        "decision_workbench.modeling",
        "decision_workbench.persistence",
        "decision_workbench.tasks",
    )
    builtin = PACKAGE_ROOT / "task_composition" / "builtin"
    offenders = {
        path.name: sorted(
            name for name in _imports(path, top_level_only=True) if name.startswith(forbidden)
        )
        for path in builtin.glob("*.py")
    }
    assert {path: names for path, names in offenders.items() if names} == {}


def test_builtin_catalog_only_collects_family_modules() -> None:
    path = PACKAGE_ROOT / "task_composition" / "builtin" / "catalog.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    assert not any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node.value.endswith("-v1")
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.Name) and node.id.endswith("_TASK_ID") for node in ast.walk(tree)
    )


def test_builtin_family_factories_have_single_explicit_owners() -> None:
    builtin = PACKAGE_ROOT / "task_composition" / "builtin"
    expected = {
        "_annealed_runtime": "annealed.py",
        "_annealed_starter_candidates": "annealed.py",
        "_hot_rolling_runtime": "hot_rolling.py",
        "_hot_rolling_starter_candidates": "hot_rolling.py",
        "_flank_wear_runtime": "flank_wear.py",
        "_tabular_runtime": "tabular.py",
        "_tabular_starter": "tabular.py",
        "_observation_runtime": "welding.py",
        "_load_welding_stage_b": "welding.py",
        "_welding_stage_b_runtime": "welding.py",
        "_welding_stage_c_starter": "welding.py",
    }
    owners: dict[str, list[str]] = {name: [] for name in expected}
    for path in builtin.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in owners:
                    owners[node.name].append(path.name)
    assert owners == {name: [owner] for name, owner in expected.items()}


def test_app_is_a_transport_composition_root() -> None:
    path = PACKAGE_ROOT / "app.py"
    forbidden = (
        "decision_workbench.application",
        "decision_workbench.modeling",
        "decision_workbench.persistence",
        "decision_workbench.task_composition",
        "decision_workbench.tasks",
    )
    assert (
        sorted(name for name in _imports(path, top_level_only=True) if name.startswith(forbidden))
        == []
    )


def test_app_has_no_welding_or_blend_specific_composition() -> None:
    source = (PACKAGE_ROOT / "app.py").read_text(encoding="utf-8")
    assert "welding" not in source.lower()
    assert "blend" not in source.lower()
    imports = _imports(PACKAGE_ROOT / "app.py")
    assert not {
        "decision_workbench.api.chains",
        "decision_workbench.api.blend_optimization",
        "decision_workbench.api.transforms",
    }.intersection(imports)


def test_application_contribution_catalog_is_internal_and_allow_listed() -> None:
    source = (PACKAGE_ROOT / "bootstrap" / "contributions.py").read_text(encoding="utf-8")
    assert "entry_points" not in source
    assert "importlib.metadata" not in source
    assert "unknown application contribution" in source


def test_core_bootstrap_has_no_application_specific_state_or_imports() -> None:
    concrete_terms = (
        "welding",
        "blend",
        "deterministic_transform",
        "chain_execution",
        "chain_uncertainty",
        "active_transforms",
        "chain_evaluation",
    )
    for name in ("startup.py", "resources.py"):
        source = (PACKAGE_ROOT / "bootstrap" / name).read_text(encoding="utf-8").lower()
        assert not {term for term in concrete_terms if term in source}, name


def test_contribution_apis_read_the_request_runtime_generation() -> None:
    legacy_state_names = (
        "app.state.blend_contract_registry",
        "app.state.deterministic_transform_catalog",
        "app.state.chain_planning_use_case",
        "app.state.chain_execution_use_case",
        "app.state.chain_snapshot_use_case",
        "app.state.chain_uncertainty_service",
        "app.state.chain_evaluation_catalog",
    )
    for name in ("candidates.py", "dependencies.py", "transforms.py"):
        source = (PACKAGE_ROOT / "api" / name).read_text(encoding="utf-8")
        assert not {
            state_name
            for state_name in legacy_state_names
            if state_name in source
        }, name


def test_bootstrap_packages_have_one_way_dependencies() -> None:
    bootstrap = PACKAGE_ROOT / "bootstrap"
    forbidden_by_module = {
        "resources.py": (
            "decision_workbench.app",
            "decision_workbench.api",
            "decision_workbench.application",
            "decision_workbench.bootstrap.contributions",
            "decision_workbench.bootstrap.startup",
            "decision_workbench.persistence",
        ),
        "contributions.py": (
            "decision_workbench.app",
            "decision_workbench.bootstrap.startup",
        ),
        "startup.py": (
            "decision_workbench.app",
            "decision_workbench.api",
        ),
    }
    offenders = {
        name: sorted(
            imported
            for imported in _imports(bootstrap / name)
            if any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in forbidden)
        )
        for name, forbidden in forbidden_by_module.items()
    }
    assert {name: imports for name, imports in offenders.items() if imports} == {}


def test_app_does_not_restore_removed_private_bootstrap_shims() -> None:
    path = PACKAGE_ROOT / "app.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_AppResources" not in names
    assert "_prepare_app_resources" not in names
    removed_imports = {
        "decision_workbench.app._AppResources",
        "decision_workbench.app._prepare_app_resources",
    }
    roots = (
        PACKAGE_ROOT,
        PACKAGE_ROOT.parents[1] / "scripts",
        PACKAGE_ROOT.parents[1] / "tests",
    )
    offenders = {
        path.relative_to(PACKAGE_ROOT.parents[2]).as_posix(): sorted(
            removed_imports & _imports(path)
        )
        for root in roots
        for path in root.rglob("*.py")
    }
    assert {path: imports for path, imports in offenders.items() if imports} == {}


def test_tasks_and_data_do_not_own_application_transactions() -> None:
    forbidden_by_package = {
        "tasks": ("decision_workbench.application", "decision_workbench.persistence"),
        "data": ("decision_workbench.application", "decision_workbench.persistence"),
    }
    offenders: dict[str, list[str]] = {}
    for package, forbidden in forbidden_by_package.items():
        for path in (PACKAGE_ROOT / package).rglob("*.py"):
            imports = sorted(name for name in _imports(path) if name.startswith(forbidden))
            if imports:
                offenders[path.relative_to(PACKAGE_ROOT).as_posix()] = imports
    assert offenders == {}


def test_proposal_service_has_no_dataset_model_or_material_dependency() -> None:
    path = PACKAGE_ROOT / "application" / "proposal_service.py"
    forbidden = (
        "decision_workbench.data",
        "decision_workbench.modeling",
        "decision_workbench.application.candidate_spreadsheet",
        "decision_workbench.application.material_lineage_candidates",
        "numpy",
        "openpyxl",
    )
    assert sorted(name for name in _imports(path) if name.startswith(forbidden)) == []


def test_material_lineage_candidate_logic_stays_isolated() -> None:
    path = PACKAGE_ROOT / "application" / "material_lineage_candidates.py"
    forbidden = (
        "decision_workbench.application.candidate_spreadsheet",
        "decision_workbench.application.proposal_service",
        "decision_workbench.persistence",
        "decision_workbench.tasks",
        "openpyxl",
    )
    assert sorted(name for name in _imports(path) if name.startswith(forbidden)) == []


def test_removed_modules_are_not_imported_or_named_by_runtime_and_scripts() -> None:
    removed_modules = {
        "decision_workbench.task_composition.builtin_tasks",
        "decision_workbench.domain.services",
        "decision_workbench.task_modules",
        "decision_workbench.tasks.project_runtime_resolver",
        "decision_workbench.data.dataset_registration",
        "decision_workbench.data.dataset_profile",
        "decision_workbench.data.profile_document",
        "decision_workbench.persistence.workspace_catalog_bootstrap",
    }
    removed_paths = {
        "backend/src/decision_workbench/task_composition/builtin_tasks.py",
        "backend/src/decision_workbench/domain/services.py",
        "backend/src/decision_workbench/task_modules.py",
        "backend/src/decision_workbench/tasks/project_runtime_resolver.py",
        "backend/src/decision_workbench/data/dataset_registration.py",
        "backend/src/decision_workbench/data/dataset_profile.py",
        "backend/src/decision_workbench/data/profile_document.py",
        "backend/src/decision_workbench/persistence/workspace_catalog_bootstrap.py",
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
