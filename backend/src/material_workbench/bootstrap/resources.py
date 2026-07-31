"""Resolve Task data, Model Packages, and prediction runtimes."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from material_workbench.contracts.task_contracts import TaskAvailability
from material_workbench.modeling.model_lifecycle import (
    ACTIVE_PACKAGES_PATH,
    load_active_packages,
    personal_model_store_path,
    resolve_configured_package,
    validate_active_package_task_set,
)
from material_workbench.modeling.packages.loader import ModelPackageLoader
from material_workbench.task_composition.builtin.catalog import (
    BUILTIN_TASK_MODULES,
)
from material_workbench.task_composition.catalog import registered_task_modules
from material_workbench.task_composition.descriptors import TaskModule
from material_workbench.task_composition.ports import PredictionRuntime
from material_workbench.tasks.task_registry import DataExplorerEntry, TaskRegistry

logger = logging.getLogger(__name__)


def default_personal_model_store_path() -> Path:
    return personal_model_store_path()


@dataclass(frozen=True)
class AppResources:
    """Prepared source data and model runtimes treated as read-only by the app."""

    modules: Mapping[str, TaskModule]
    data_by_task: Mapping[str, Any]
    default_data: Any | None
    runtimes: Mapping[str, PredictionRuntime]
    task_registry: TaskRegistry


def _task_unavailable(
    task_id: str,
    *,
    stage: str,
    label: str,
    resource_id: str,
    expected_locator: str | Path,
    recovery_hint: str,
    exc: Exception,
) -> TaskAvailability:
    logger.warning(
        "TASK_UNAVAILABLE task_id=%s stage=%s error_type=%s detail=%s",
        task_id,
        stage,
        type(exc).__name__,
        exc,
    )
    message = (
        f"{label}のファイルが見つかりません。設定を確認して再起動してください。"
        if isinstance(exc, FileNotFoundError)
        else f"{label}を準備できません: {exc}"
    )
    return TaskAvailability(
        status="unavailable",
        stage=stage,
        message=message,
        resource_id=resource_id,
        expected_locator=str(expected_locator),
        recovery_hint=recovery_hint,
    )


def _default_data_task_id(modules: Mapping[str, TaskModule]) -> str:
    """Return the one Task that supplies the generic default data projection.

    Task-scoped consumers must use ``TaskRegistry``.  This projection remains
    only for the generic runtime context and diagnostics that predate that
    split, so a catalog-order-dependent selection is never acceptable.
    """

    selected = [
        task_id
        for task_id, module in modules.items()
        if module.default_data_projection
    ]
    if not selected:
        raise ValueError(
            "exactly one TaskModule must declare default_data_projection; "
            "none declared"
        )
    if len(selected) > 1:
        raise ValueError(
            "exactly one TaskModule must declare default_data_projection; "
            "multiple declared: "
            + ", ".join(sorted(selected))
        )
    return selected[0]


def prepare_app_resources(
    *,
    source_overrides: Mapping[str, str | Path] | None = None,
    package_roots: Mapping[str, str | Path] | None = None,
    active_packages_path: str | Path | None = None,
    task_ids: frozenset[str] | None = None,
) -> AppResources:
    """Load workbook and package resources that callers treat as read-only."""

    configured = (
        Path(active_packages_path) if active_packages_path else ACTIVE_PACKAGES_PATH
    )
    injected = dict(package_roots or {})
    injected_sources = dict(source_overrides or {})
    modules = dict(registered_task_modules())
    default_data_task_id = _default_data_task_id(modules)
    unknown_source_tasks = set(injected_sources) - set(modules)
    if unknown_source_tasks:
        raise ValueError(
            "source overrides reference unknown Task IDs: "
            + ", ".join(sorted(unknown_source_tasks))
        )
    validate_active_package_task_set(
        load_active_packages(configured),
        set(BUILTIN_TASK_MODULES),
    )
    data_by_task: dict[str, Any] = {}
    runtimes: dict[str, PredictionRuntime] = {}
    explorers: dict[str, DataExplorerEntry] = {}
    unavailable: dict[str, TaskAvailability] = {}
    for task_id, module in modules.items():
        if task_ids is not None and task_id not in task_ids:
            unavailable[task_id] = TaskAvailability(
                status="unavailable",
                stage="runtime",
                message="起動後にデータとModel Packageを準備しています。",
                resource_id=task_id,
                expected_locator=f"task:{task_id}",
                recovery_hint="準備完了後に自動で利用可能になります。",
            )
            continue
        configured_source = Path(module.default_source)
        try:
            configured_source = Path(
                injected_sources.get(task_id)
                or os.getenv(module.source_env, str(module.default_source))
            )
            if not configured_source.is_absolute() and not configured_source.exists():
                repository_source = (
                    Path(__file__).resolve().parents[4] / configured_source
                )
                if repository_source.exists():
                    configured_source = repository_source
            loaded = module.data_loader(configured_source, None)
            data_by_task[task_id] = loaded
        except (OSError, ValueError, KeyError) as exc:
            unavailable[task_id] = _task_unavailable(
                task_id,
                stage="source",
                label="データソース",
                resource_id=module.source_kind,
                expected_locator=configured_source.resolve(),
                recovery_hint=(
                    f"{module.source_env}と対象データファイルを確認して"
                    "再起動してください。"
                ),
                exc=exc,
            )
            continue
        package_override = injected.get(task_id) or os.getenv(
            module.package_override_env
        )
        configured_package = (
            Path(package_override)
            if package_override
            else module.default_package or configured
        )
        try:
            if package_override or module.default_package is None:
                configured_package = resolve_configured_package(
                    task_id,
                    config_path=configured,
                    override=package_override,
                )
            package = ModelPackageLoader().load(configured_package)
        except (OSError, ValueError, KeyError) as exc:
            unavailable[task_id] = _task_unavailable(
                task_id,
                stage="package",
                label="Model Package",
                resource_id=task_id,
                expected_locator=configured_package.resolve(),
                recovery_hint=(
                    f"{module.package_override_env}、active-packages.json、"
                    "対象Packageのmanifestとartifactを確認して再起動してください。"
                ),
                exc=exc,
            )
            continue
        try:
            runtimes[task_id] = module.runtime_factory(loaded, package)
        except (OSError, ValueError, KeyError) as exc:
            unavailable[task_id] = _task_unavailable(
                task_id,
                stage="runtime",
                label="予測runtime",
                resource_id=package.manifest.package_id,
                expected_locator=package.root,
                recovery_hint=(
                    "対象Packageのruntime種別、manifest、artifact、"
                    "追加依存を確認して再起動してください。"
                ),
                exc=exc,
            )
            continue
        if module.data_explorer is not None:
            explorers[task_id] = DataExplorerEntry(
                data=loaded,
                capability=module.data_explorer,
            )
    task_registry = TaskRegistry(
        runtimes,
        data_explorers=explorers,
        modules=modules,
        unavailable=unavailable,
        degrade_invalid_runtimes=True,
    )
    default_data = data_by_task.get(default_data_task_id)
    return AppResources(
        modules=MappingProxyType(modules),
        data_by_task=MappingProxyType(data_by_task),
        default_data=default_data,
        runtimes=MappingProxyType(runtimes),
        task_registry=task_registry,
    )
