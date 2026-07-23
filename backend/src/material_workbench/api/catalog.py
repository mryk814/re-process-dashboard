from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Annotated

from fastapi import APIRouter, Depends

from .dependencies import get_store, get_task_registry, project_or_404
from .errors import PROJECT_API_ERRORS
from material_workbench.modeling.model_lifecycle import validate_lifecycle_metadata
from material_workbench.modeling.model_packages import RUNTIME_TYPES
from material_workbench.contracts.schemas import ModelPackageStatus, TaskCatalogItem
from material_workbench.persistence.store import Store
from material_workbench.contracts.task_contracts import ResolvedTaskDefinition
from material_workbench.tasks.task_registry import TaskRegistry


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]


@router.get("/api/health")
@router.get("/health", include_in_schema=False)
def health(store: StoreDependency, registry: RegistryDependency) -> dict[str, Any]:
    default_project = project_or_404(store, "default")
    default_runtime = registry.runtime_for(default_project.task_id)
    return {
        "ok": True,
        "models": sorted(default_runtime.models),  # type: ignore[attr-defined]
        "source": default_runtime.data.source_path,
        "tasks": {
            task_id: {
                "package_id": registry.entry_for(task_id).model_package.manifest.package_id,
                "outputs": sorted(registry.runtime_for(task_id).output_keys),
                "source": registry.runtime_for(task_id).data.source_path,
            }
            for task_id in registry.task_ids
        },
    }


@router.get(
    "/api/projects/{project_id}/model-package",
    response_model=ModelPackageStatus,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectModelPackage",
)
def model_package(
    project_id: str,
    store: StoreDependency,
    registry: RegistryDependency,
) -> dict[str, Any]:
    project = project_or_404(store, project_id)
    entry = registry.entry_for(project.task_id)
    package = entry.model_package
    manifest = package.manifest
    quality = validate_lifecycle_metadata(
        package,
        registry.contract_for(project.task_id),
        profile_path=Path(entry.predictor_runtime.data.profile_path),
    )
    optional_dependencies = {
        "sklearn.skops.v1": importlib.util.find_spec("skops") is not None,
        "lightgbm.booster.v1": importlib.util.find_spec("lightgbm") is not None,
        "gpytorch.static_exact_rbf.v1": (
            importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("safetensors") is not None
        ),
    }
    dependencies = {
        runtime_type: optional_dependencies.get(runtime_type, True)
        for runtime_type in RUNTIME_TYPES
    }
    return {
        "id": manifest.package_id,
        "version": manifest.package_version,
        "task_id": manifest.task_id,
        "manifest_sha256": package.manifest_sha256,
        "active_runtimes": sorted({item.runtime_type for item in manifest.predictors}),
        "supported_runtimes": [
            {"runtime_type": runtime_type, "available": available}
            for runtime_type, available in dependencies.items()
        ],
        "predictors": [
            {
                "target": item.target,
                "runtime_type": item.runtime_type,
                "predictive_family": item.predictive_family,
            }
            for item in manifest.predictors
        ],
        "quality_report": quality.model_dump(mode="json"),
    }


@router.get(
    "/api/task-definitions",
    response_model=list[TaskCatalogItem],
    operation_id="listTaskDefinitions",
)
def task_definitions(registry: RegistryDependency) -> list[dict[str, Any]]:
    catalog = []
    for task_id in registry.task_ids:
        contract = registry.contract_for(task_id)
        canonical = contract.canonical_candidate
        catalog.append({
            "definition": registry.resolved_definition_for(task_id),
            "starter_candidate": {
                "name": "基準候補",
                "inputs": {
                    "composition": canonical.composition,
                    "process": canonical.process,
                    "categorical": canonical.categorical,
                    "heat_pattern": canonical.heat_pattern,
                },
                "provenance": {"source_kind": "direct", "source_ref": None},
            },
        })
    return catalog


@router.get(
    "/api/projects/{project_id}/task-definition",
    response_model=ResolvedTaskDefinition,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectTaskDefinition",
)
def task_definition(
    project_id: str,
    store: StoreDependency,
    registry: RegistryDependency,
) -> ResolvedTaskDefinition:
    project = project_or_404(store, project_id)
    return registry.resolved_definition_for(project.task_id)
