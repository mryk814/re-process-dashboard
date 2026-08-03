"""Build and promote a reviewed personal Task package.

This is the application seam used by the UI and by the operations CLI.  It is
deliberately independent of a command-line entrypoint so a browser flow never
has to pretend that a user ran an external command.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from decision_workbench.contracts.feature_recipe_contracts import FeatureRecipe
from decision_workbench.data.profile_family_registry import (
    load_profile_document,
    profile_task_ids,
)
from decision_workbench.developer_experience.task_scaffolding import (
    link_promoted_package,
    validate_personal_task_store_path,
)
from decision_workbench.modeling.model_lifecycle import (
    AvailablePackagesConfig,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    ensure_available_packages_config,
    load_available_packages,
    register_available_package,
    staged_package_destination,
    validate_personal_model_store_path,
)
from decision_workbench.modeling.model_package_verify import verify_model_package
from decision_workbench.modeling.packages.contracts import PackageContractError
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.training.feature_recipe import (
    apply_feature_recipe_to_canonical_dataset,
)
from decision_workbench.modeling.training.package_assembler import (
    build_standard_model_package,
)
from decision_workbench.modeling.training.recipe import estimator_recipe
from decision_workbench.modeling.training.readiness import (
    compatible_standard_estimator_ids,
)
from decision_workbench.task_composition.catalog import resolve_task_source, task_module
from decision_workbench.tasks.task_registry import load_task_contracts


def _selected_profile(task_id: str, profile: Path | None) -> Any | None:
    if profile is None:
        return None
    resolved = profile.resolve(strict=True)
    loaded = load_profile_document(resolved)
    if task_id not in profile_task_ids(loaded):
        raise ValueError(f"Profile does not declare task {task_id}: {resolved}")
    return loaded


def _load_task_data(task_id: str, source: Path, profile: Path | None):
    return task_module(task_id).data_loader(
        resolve_task_source(task_id, source),
        _selected_profile(task_id, profile),
    )


def _write_json(path: Path, payload: Any, *, replace: bool) -> None:
    if path.exists() and not replace:
        raise FileExistsError(f"refusing to replace existing file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def build_standard_package(
    task_id: str,
    source: Path,
    output: Path,
    dataset_output: Path,
    *,
    package_id: str,
    package_version: str,
    replace: bool,
    estimator: str | None = None,
    estimator_options: dict[str, Any] | None = None,
    profile: Path | None = None,
    feature_recipe_path: Path | None = None,
) -> dict[str, Any]:
    if output.exists() and not replace:
        raise FileExistsError(f"refusing to replace existing model package: {output}")
    source = resolve_task_source(task_id, source)
    module = task_module(task_id)
    authoring = module.standard_model_authoring
    selected_estimator = estimator or (authoring.default_estimator_id if authoring else None)
    selected_options = estimator_options
    if estimator is None and authoring is not None:
        selected_options = authoring.default_options()
    if selected_estimator is None or authoring is None:
        raise ValueError(
            f"{task_id} has no standard model authoring seam for "
            f"{selected_estimator or 'an unspecified estimator'}"
        )
    contract = load_task_contracts()[task_id]
    allowed = authoring.allowed_estimator_ids(
        compatible_standard_estimator_ids(contract.task_definition.outputs)
    )
    if selected_estimator not in allowed:
        reason = (
            f": {authoring.specialization_reason}"
            if authoring.specialization_reason
            else ""
        )
        raise ValueError(
            f"{selected_estimator} is outside the Task recipe policy{reason}"
        )
    selected_options = authoring.resolved_options(selected_options)
    data = _load_task_data(task_id, source, profile)
    feature_recipe = (
        FeatureRecipe.model_validate_json(
            feature_recipe_path.read_text(encoding="utf-8")
        )
        if feature_recipe_path is not None
        else None
    )
    payload = canonical_training_dataset(task_id, data, contract)
    if feature_recipe is not None:
        apply_feature_recipe_to_canonical_dataset(
            payload,
            data,
            authoring.candidate_builder,
            feature_recipe,
        )
    _write_json(dataset_output, payload, replace=replace)
    recipe = estimator_recipe(selected_estimator, selected_options)
    build_standard_model_package(
        task_id=task_id,
        source=source,
        data=data,
        contract=contract,
        candidate_builder=authoring.candidate_builder,
        recipe=recipe,
        destination=output,
        package_id=package_id,
        package_version=package_version,
        replace=replace,
        positive_targets=authoring.positive_targets,
        feature_recipe=feature_recipe,
    )
    report = verify_model_package(output, task_id=task_id, source=source, profile=profile)
    manifest = ModelPackageLoader().load(output).manifest
    payload["feature_dataset_id"] = canonical_training_dataset_digest(
        payload, algorithm=manifest.provenance.feature_dataset_digest_algorithm,
    )
    payload["feature_dataset_digest_algorithm"] = manifest.provenance.feature_dataset_digest_algorithm
    return {
        "dataset": {
            "path": str(dataset_output.resolve()),
            "rows": len(payload["rows"]),
            "feature_dataset_id": payload["feature_dataset_id"],
            "dataset_profile_id": payload["dataset_profile_digest"],
            "feature_dataset_digest_algorithm": payload["feature_dataset_digest_algorithm"],
        },
        "package": report.model_dump(),
    }


def promote_personal_package(
    task_id: str,
    package: Path,
    source: Path,
    store: Path,
    *,
    profile: Path | None = None,
    task_store: Path,
) -> dict[str, Any]:
    validated_task_store = validate_personal_task_store_path(task_store)
    package = package.resolve(strict=True)
    source = resolve_task_source(task_id, source)
    verify_model_package(package, task_id=task_id, source=source, profile=profile)
    loaded = ModelPackageLoader().load(package)
    package_id = loaded.manifest.package_id
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", package_id):
        raise PackageContractError("package_id must be a filesystem-safe immutable identifier")
    available_config = ensure_available_packages_config(store)
    destination = available_config.parent / "packages" / package_id
    promoted = False
    if destination.exists():
        existing = ModelPackageLoader().load(destination)
        if existing.manifest_sha256 != loaded.manifest_sha256:
            raise FileExistsError(f"trusted package ID already exists with different content: {destination}")
    else:
        with staged_package_destination(destination, replace=False) as staging:
            shutil.copytree(package, staging)
        promoted = True
    report = verify_model_package(destination, task_id=task_id, source=source, profile=profile)
    available = register_available_package(destination, config_path=available_config)
    link_promoted_package(task_id, destination, store=validated_task_store)
    return {
        "task_id": task_id,
        "promoted": promoted,
        "store": str(available_config.parent),
        "trusted_package": str(destination),
        "package": report.model_dump(),
        "restart_required": False,
        "available_package": destination.relative_to(available_config.parent).as_posix(),
        "available_package_count": len(available.packages),
    }


def rollback_promoted_personal_package(
    promotion: dict[str, Any],
    *,
    store: Path,
) -> None:
    """Compensate a promotion that has not yet become a runtime resource."""

    if not promotion.get("promoted"):
        return
    rollback_personal_package_attempt(
        Path(str(promotion["trusted_package"])).name,
        store=store,
    )


def rollback_personal_package_attempt(
    package_id: str,
    *,
    store: Path,
) -> None:
    """Remove a just-created personal Package and its availability entry.

    Callers must establish that ``package_id`` did not exist before the
    attempt.  Keeping that precondition at the caller makes this usable for a
    failed promotion as well as a successful promotion that later fails to
    register its Dataset.
    """

    models_root = validate_personal_model_store_path(store)
    destination = (models_root / "packages" / package_id).resolve()
    packages_root = (models_root / "packages").resolve()
    if destination.parent != packages_root:
        raise ValueError("onboarding package destination leaves the personal model store")
    relative = destination.relative_to(models_root).as_posix()
    config_path = models_root / "available-packages.json"
    if config_path.is_file():
        config = load_available_packages(config_path)
        if relative in config.packages:
            updated = AvailablePackagesConfig(
                schema_version=config.schema_version,
                packages=tuple(item for item in config.packages if item != relative),
            )
            temporary = config_path.with_suffix(config_path.suffix + ".onboarding-rollback")
            temporary.write_text(
                json.dumps(updated.model_dump(mode="json"), ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
                newline="\n",
            )
            temporary.replace(config_path)
    if destination.exists():
        shutil.rmtree(destination)
