"""Load reviewed external Task bundles as data-only compositions."""
from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from decision_workbench.contracts.task_contracts import TaskContractFixture
from decision_workbench.developer_experience.task_scaffolding import (
    TASK_BUNDLE_SCHEMA_VERSION,
    validate_personal_task_store_path,
)
from decision_workbench.modeling.tabular.profile import load_tabular_profile
from decision_workbench.modeling.training.recipe import (
    CSV_ONBOARDING_ESTIMATOR_IDS,
    estimator_recipe,
)
from decision_workbench.task_composition.builtin.tabular import (
    external_tabular_task_module,
)
from decision_workbench.task_composition.descriptors import TaskModule


_PERSONAL_TASK_DISCOVERY_ENABLED: ContextVar[bool] = ContextVar(
    "personal_task_discovery_enabled",
    default=True,
)


@contextmanager
def without_personal_task_discovery():
    """Run a repository-only operation without reading a user's Task store."""

    token = _PERSONAL_TASK_DISCOVERY_ENABLED.set(False)
    try:
        yield
    finally:
        _PERSONAL_TASK_DISCOVERY_ENABLED.reset(token)


def _inside(root: Path, raw: str, label: str) -> Path:
    candidate = (root / raw).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"external Task {label} leaves its bundle directory")
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def _bundle_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"external Task bundle must be an object: {path}")
    if payload.get("schema_version") != TASK_BUNDLE_SCHEMA_VERSION:
        raise ValueError(f"unsupported external Task bundle: {path}")
    if payload.get("state") != "ready":
        raise ValueError(f"external Task bundle is not ready: {path}")
    if payload.get("loads_python_code") is not False:
        raise ValueError(f"external Task bundle cannot load Python code: {path}")
    if payload.get("grain_confirmation") != "one-row-one-observation":
        raise ValueError(f"external Task grain is not confirmed: {path}")
    if payload.get("relation_confirmation") != "no-relations":
        raise ValueError(f"external Task relations are not confirmed: {path}")
    return payload


def external_task_bundles(
    store: Path | None = None,
) -> dict[str, tuple[TaskModule, TaskContractFixture]]:
    if not _PERSONAL_TASK_DISCOVERY_ENABLED.get():
        return {}
    root = validate_personal_task_store_path(store)
    if not root.exists():
        return {}
    result: dict[str, tuple[TaskModule, TaskContractFixture]] = {}
    for bundle_path in sorted(root.glob("*/bundle.json")):
        bundle_root = bundle_path.parent.resolve()
        payload = _bundle_payload(bundle_path)
        task_id = str(payload.get("task_id", ""))
        if not task_id or task_id in result:
            raise ValueError(f"duplicate or missing external Task id: {task_id}")
        task_path = _inside(
            bundle_root,
            str(payload.get("task_definition_path", "")),
            "TaskDefinition",
        )
        profile_path = _inside(
            bundle_root,
            str(payload.get("profile_path", "")),
            "Dataset Profile",
        )
        source_path = _inside(
            bundle_root,
            str(payload.get("source_path", "")),
            "source",
        )
        recipe_path = _inside(
            bundle_root,
            str(payload.get("training_recipe_path", "")),
            "Training Recipe",
        )
        fixture = TaskContractFixture.model_validate_json(
            task_path.read_text(encoding="utf-8")
        )
        profile = load_tabular_profile(profile_path)
        recipe_payload = json.loads(recipe_path.read_text(encoding="utf-8"))
        recipe = estimator_recipe(
            str(recipe_payload.get("estimator", {}).get("estimator_id", "")),
            {
                key: value
                for key, value in dict(recipe_payload.get("estimator", {})).items()
                if key != "estimator_id"
            },
        )
        if fixture.task_definition.id != task_id or profile.task_id != task_id:
            raise ValueError(f"external Task identities disagree: {task_id}")
        declared_estimator = str(payload.get("estimator_id", ""))
        if recipe.estimator_id != declared_estimator:
            raise ValueError(f"external Task estimator identities disagree: {task_id}")
        declared_estimator_ids = tuple(
            str(item)
            for item in payload.get("standard_estimator_ids", (declared_estimator,))
        )
        if (
            not declared_estimator_ids
            or len(set(declared_estimator_ids)) != len(declared_estimator_ids)
            or declared_estimator not in declared_estimator_ids
            or not set(declared_estimator_ids).issubset(CSV_ONBOARDING_ESTIMATOR_IDS)
        ):
            raise ValueError(f"external Task standard estimator allow-list is invalid: {task_id}")
        package_path = payload.get("package_path")
        resolved_package = (
            Path(str(package_path)).expanduser().resolve()
            if package_path
            else None
        )
        result[task_id] = (
            external_tabular_task_module(
                task_id=task_id,
                label=fixture.task_definition.label,
                source_path=source_path,
                profile_path=profile_path,
                estimator_ids=declared_estimator_ids,
                default_estimator_id=declared_estimator,
                package_path=resolved_package,
                actual_measurement=(
                    fixture.runtime_capability.operations.actual_measurement
                ),
            ),
            fixture,
        )
    return result


def external_task_modules(store: Path | None = None) -> dict[str, TaskModule]:
    return {
        task_id: module
        for task_id, (module, _fixture) in external_task_bundles(store).items()
    }


def external_task_contracts(
    store: Path | None = None,
) -> dict[str, TaskContractFixture]:
    return {
        task_id: fixture
        for task_id, (_module, fixture) in external_task_bundles(store).items()
    }
