"""Task and Model Package catalog use cases."""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from material_workbench.application.catalog.contracts import CatalogRuntimeState
from material_workbench.application.catalog.errors import (
    CatalogConflictError,
    lifecycle_profile,
    require_project,
)
from material_workbench.application.project_runtime import ProjectRuntimeResolver
from material_workbench.application.workspace_catalog_bootstrap import (
    task_definition_digest,
)
from material_workbench.contracts.subsystem_availability import (
    SubsystemAvailability,
    SubsystemAvailabilityRegistry,
)
from material_workbench.contracts.task_contracts import ResolvedTaskDefinition
from material_workbench.modeling.model_lifecycle import validate_lifecycle_metadata
from material_workbench.modeling.packages.contracts import PREDICTOR_RUNTIME_TYPES
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError


def _storage_status(path: Path | None, label: str) -> dict[str, Any]:
    if path is None:
        return {
            "label": label,
            "path": None,
            "available": False,
            "reason": "このWorkspaceには保存先が設定されていません。",
            "next_action": "ワークスペース → 保存場所を管理で保存先を確認し、APIを再起動してください。",
        }
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        return {
            "label": label,
            "path": str(resolved),
            "available": False,
            "reason": "保存先フォルダが存在しません。",
            "next_action": "ワークスペース → 保存場所を管理で場所を確認し、フォルダを準備してから再確認してください。",
        }
    try:
        writable = os.access(resolved, os.W_OK)
    except OSError:
        writable = False
    if not writable:
        return {
            "label": label,
            "path": str(resolved),
            "available": False,
            "reason": "保存先フォルダへ書き込めません。",
            "next_action": "ワークスペース → 保存場所を管理で場所を確認し、書き込み権限を直してから再確認してください。",
        }
    return {
        "label": label,
        "path": str(resolved),
        "available": True,
        "reason": "このWorkspaceの新しいTask/Model準備に使用できます。",
        "next_action": "そのままCSV onboardingへ戻って準備を再試行してください。",
    }


@dataclass(frozen=True)
class TaskPackageCatalog:
    state: CatalogRuntimeState
    store: Store
    registry: TaskRegistry
    resolver: ProjectRuntimeResolver
    subsystem_registry: SubsystemAvailabilityRegistry
    transform_catalog: DeterministicTransformCatalog | None

    def health(self) -> dict[str, Any]:
        available = set(self.registry.available_task_ids)
        optional_subsystems = self.subsystem_registry.list()
        default_project = self.store.get_project("default")
        default_runtime = (
            self.registry.runtime_for(default_project.task_id)
            if default_project is not None and default_project.task_id in available
            else None
        )
        task_store = _storage_status(self.state.task_store_path, "個人Task")
        model_store = _storage_status(
            self.state.model_store_path,
            "個人Model / Package",
        )
        return {
            "ok": True,
            "ready": self.state.resources_ready,
            "resources_loading": (
                not self.state.resources_ready
                and self.state.resources_loading_error is None
            ),
            "resources_loading_error": self.state.resources_loading_error,
            "degraded": (
                len(available) != len(self.registry.task_ids)
                or any(item.status == "unavailable" for item in optional_subsystems)
            ),
            "models": sorted(default_runtime.models) if default_runtime is not None else [],  # type: ignore[attr-defined]
            "source": default_runtime.data.source_path if default_runtime is not None else None,
            "tasks": {
                task_id: (
                    {
                        "availability": self.registry.availability_for(task_id).model_dump(mode="json"),
                        "package_id": self.registry.entry_for(task_id).model_package.manifest.package_id,
                        "outputs": sorted(self.registry.runtime_for(task_id).output_keys),
                        "source": self.registry.runtime_for(task_id).data.source_path,
                    }
                    if task_id in available
                    else {
                        "availability": self.registry.availability_for(task_id).model_dump(mode="json"),
                        "package_id": None,
                        "outputs": [],
                        "source": None,
                    }
                )
                for task_id in self.registry.task_ids
            },
            "optional_subsystems": [
                item.model_dump(mode="json") for item in optional_subsystems
            ],
            "workspace": {
                "database_path": str(self.state.workspace_database),
                "data_library_path": str(self.state.data_library_root),
                "kind": self.state.workspace_kind,
            },
            "storage": {
                "ready": task_store["available"] and model_store["available"],
                "task_store": task_store,
                "model_store": model_store,
                "next_action": "保存先を確認してCSV onboardingへ戻ってください。",
            },
        }

    def readiness(self) -> dict[str, Any]:
        unavailable = {
            task_id: self.registry.availability_for(task_id).model_dump(mode="json")
            for task_id in self.registry.task_ids
            if self.registry.availability_for(task_id).status == "unavailable"
        }
        optional_subsystems = self.subsystem_registry.list()
        return {
            "ready": self.state.resources_ready,
            "resources_loading_error": self.state.resources_loading_error,
            "degraded": bool(unavailable) or any(
                item.status == "unavailable" for item in optional_subsystems
            ),
            "available_tasks": list(self.registry.available_task_ids),
            "unavailable_tasks": unavailable,
            "optional_subsystems": [
                item.model_dump(mode="json") for item in optional_subsystems
            ],
        }

    def subsystem_availability(self) -> tuple[SubsystemAvailability, ...]:
        return self.subsystem_registry.list()

    def model_package(self, project_id: str) -> dict[str, Any]:
        project = require_project(self.store, project_id)
        resolved = self.resolver.resolve(project)
        package = resolved.runtime.model_package
        assert package is not None
        manifest = package.manifest
        quality = validate_lifecycle_metadata(
            package,
            self.registry.contract_for(project.task_id),
            profile_path=lifecycle_profile(resolved.runtime.data),
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
            for runtime_type in PREDICTOR_RUNTIME_TYPES
        }
        return {
            "id": manifest.package_id,
            "version": manifest.package_version,
            "task_id": manifest.task_id,
            "manifest_sha256": package.manifest_sha256,
            "active_runtimes": sorted(
                {item.runtime_type for item in manifest.predictors}
            ),
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

    def task_definitions(self) -> list[dict[str, Any]]:
        catalog = []
        for task_id in self.registry.task_ids:
            contract = self.registry.contract_for(task_id)
            canonical = contract.canonical_candidate
            definition = self.registry.resolved_definition_for(task_id)
            starter_candidate: dict[str, Any] = {
                "name": "基準候補",
                "inputs": {
                    "composition": canonical.composition,
                    "process": canonical.process,
                    "categorical": canonical.categorical,
                    "heat_pattern": canonical.heat_pattern,
                },
                "provenance": {"source_kind": "direct", "source_ref": None},
            }
            transform_id = definition.application.sparse_blend_transform_id
            if transform_id is not None and self.transform_catalog is not None:
                starter_candidate["blend"] = self.transform_catalog.initial_blend(
                    transform_id
                )
            catalog.append({
                "definition": definition,
                "starter_candidate": starter_candidate,
            })
        return catalog

    def task_definition(self, project_id: str) -> ResolvedTaskDefinition:
        project = require_project(self.store, project_id)
        identity = project.scientific_identity
        if identity.identity_kind == "single_task":
            return self.registry.resolved_definition_for(identity.task_id)
        revision = self.store.get_chain_revision(identity.chain_revision_id)
        if (
            revision is None
            or revision.revision_digest != identity.chain_revision_digest
        ):
            raise CatalogConflictError(
                "プロジェクトに固定されたChain Revisionを読み込めません",
            )
        task_stages = [
            stage for stage in revision.stages if stage.stage_kind == "task"
        ]
        if not task_stages:
            raise CatalogConflictError("Chainに予測Taskがありません")
        terminal_stage = task_stages[-1]
        try:
            resolved = self.registry.resolved_definition_for(
                terminal_stage.contract_id
            )
            current_contract_digest = task_definition_digest(
                self.registry,
                terminal_stage.contract_id,
            )
        except TaskRegistryError as exc:
            raise CatalogConflictError(
                "Chain終端Taskの固定contractを読み込めません",
            ) from exc
        if current_contract_digest != terminal_stage.contract_digest:
            raise CatalogConflictError(
                "Chain終端Taskのcontract digestが固定Revisionと一致しません",
            )
        return resolved
