from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

from .dataset_profile import load_dataset_profile
from .feature_pipeline import build_feature_bundle_from_observation
from .flank_wear_feature_pipeline import build_flank_wear_features_from_observation
from .hot_rolling_feature_pipeline import build_hot_rolling_features_from_observation
from .importer import WorkbookData
from .model_packages import PackageContractError, VerifiedModelPackage
from .task_contracts import RuntimeCapability, TaskContractFixture, TaskDefinition


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MODELS_ROOT = REPOSITORY_ROOT / "models"
ACTIVE_PACKAGES_PATH = MODELS_ROOT / "active-packages.json"
DATASET_PROFILE_PATH = Path(__file__).with_name("dataset-input-profile-v1.json")


@contextmanager
def staged_package_destination(destination: Path, *, replace: bool) -> Iterator[Path]:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not replace:
        raise FileExistsError(f"refusing to replace existing model package: {destination}")
    if destination.exists() and not (destination / "manifest.json").is_file():
        raise FileExistsError(f"refusing to replace a directory that is not a model package: {destination}")
    staging = destination.with_name(f".{destination.name}.building")
    backup = destination.with_name(f".{destination.name}.previous-swap")
    if staging.exists() or backup.exists():
        raise FileExistsError(f"stale package build/swap directory exists beside {destination}")
    try:
        yield staging
        if not (staging / "manifest.json").is_file():
            raise FileNotFoundError("staged model package did not produce manifest.json")
        if destination.exists():
            destination.replace(backup)
            try:
                staging.replace(destination)
            except Exception:
                backup.replace(destination)
                raise
            try:
                shutil.rmtree(backup)
            except OSError:
                # The verified package is already committed. Keep the recoverable
                # backup instead of reporting a failed build with a new active path.
                pass
        else:
            staging.replace(destination)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


class LifecycleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ActivePackageSelection(LifecycleModel):
    active: Annotated[str, Field(min_length=1)]
    previous: str | None = None


class ActivePackagesConfig(LifecycleModel):
    schema_version: Literal["active-model-packages/v1"]
    tasks: dict[str, ActivePackageSelection]


class TargetQualityMetric(LifecycleModel):
    target: Annotated[str, Field(min_length=1)]
    parent_conditions: Annotated[int, Field(ge=2)]
    mae: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    rmse: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    interval_coverage_90: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class QualityReport(LifecycleModel):
    schema_version: Literal["model-quality-report/v1"]
    split: Literal["leave-one-parent-condition-out"]
    targets: Annotated[tuple[TargetQualityMetric, ...], Field(min_length=1)]


def _semantic_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def task_input_contract_digest(task: TaskDefinition) -> str:
    return _semantic_digest({
        "schema_version": task.schema_version,
        "task_id": task.id,
        "input_groups": [group.model_dump(mode="json") for group in task.input_groups],
    })


def runtime_capability_digest(capability: RuntimeCapability) -> str:
    return _semantic_digest(capability.model_dump(mode="json"))


def dataset_profile_digest(path: Path = DATASET_PROFILE_PATH) -> str:
    profile = load_dataset_profile(path)
    payload = profile.model_dump(mode="json", exclude={"task_definitions"})
    shared = payload.get("shared")
    if isinstance(shared, dict):
        for key in ("policy_defaults", "optional_roles", "optional_technical_fields"):
            if not shared.get(key):
                shared.pop(key, None)
    return _semantic_digest(payload)


def canonical_training_dataset(
    task_id: str,
    data: WorkbookData,
    contract: TaskContractFixture,
) -> dict[str, Any]:
    profile = load_dataset_profile(data.profile_path)
    output_columns = {
        target.key: target.column
        for observation in profile.tasks[task_id].observations
        for target in observation.targets
    }
    builders = {
        "annealed-properties-v1": build_feature_bundle_from_observation,
        "hot-rolled-properties-v1": build_hot_rolling_features_from_observation,
        "flank-wear-v1": build_flank_wear_features_from_observation,
    }
    builder = builders.get(task_id)
    if builder is None:
        raise ValueError(f"unsupported training task: {task_id}")
    rows: list[dict[str, Any]] = []
    for observation in data.observations:
        if not observation["eligible"]:
            continue
        bundle = builder(observation, data.medians)
        if bundle is None:
            continue
        outputs = {
            output.key: float(observation["outputs"][output_columns[output.key]])
            for output in contract.task_definition.outputs
            if output_columns[output.key] in observation["outputs"]
        }
        if not outputs:
            continue
        rows.append({
            "observation_id": str(observation["id"]),
            "parent_key": str(observation["parent_key"]),
            "features": bundle.as_dict(),
            "outputs": outputs,
        })
    rows.sort(key=lambda item: (item["parent_key"], item["observation_id"]))
    if not rows:
        raise ValueError(f"no eligible canonical training rows for {task_id}")
    first_bundle = builder(
        next(row for row in data.observations if row["id"] == rows[0]["observation_id"]),
        data.medians,
    )
    assert first_bundle is not None
    return {
        "schema_version": "canonical-training-dataset/v1",
        "task_id": task_id,
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "dataset_profile_digest": dataset_profile_digest(Path(data.profile_path)),
        "source_data_digest": f"sha256:{data.source_sha256}",
        "feature_pipeline": {
            "id": first_bundle.pipeline_id,
            "version": first_bundle.pipeline_version,
            "features": [
                {"name": item.name, "unit": item.unit, "group": item.group}
                for item in first_bundle.definitions
            ],
        },
        "composition_defaults": data.medians,
        "rows": rows,
    }


def canonical_training_dataset_digest(payload: dict[str, Any]) -> str:
    return _semantic_digest(payload)


def exact_gp_loo_quality(
    target: str,
    train_y: Any,
    alpha: Any,
    precision: Any,
) -> TargetQualityMetric:
    import numpy as np

    diagonal = np.diag(precision)
    if np.any(diagonal <= 0):
        raise ValueError("GP precision diagonal must be positive for LOO evaluation")
    residuals = alpha / diagonal
    conditional_variance = 1.0 / diagonal
    z90 = 1.6448536269514722
    return TargetQualityMetric(
        target=target,
        parent_conditions=len(train_y),
        mae=float(np.mean(np.abs(residuals))),
        rmse=float(np.sqrt(np.mean(residuals * residuals))),
        interval_coverage_90=float(np.mean(np.abs(residuals) <= z90 * np.sqrt(conditional_variance))),
    )


def validate_lifecycle_metadata(
    package: VerifiedModelPackage,
    contract: TaskContractFixture,
    *,
    profile_path: Path = DATASET_PROFILE_PATH,
) -> QualityReport:
    manifest = package.manifest
    expected_input = task_input_contract_digest(contract.task_definition)
    if manifest.input_contract_digest != expected_input:
        raise PackageContractError("model package input contract digest does not match TaskDefinition")
    expected_capability = runtime_capability_digest(contract.runtime_capability)
    if manifest.runtime_capability_digest != expected_capability:
        raise PackageContractError("model package runtime capability digest does not match task contract")
    expected_profile = dataset_profile_digest(profile_path)
    if manifest.provenance.dataset_profile_id != expected_profile:
        raise PackageContractError("model package dataset profile digest does not match the active profile")
    if not manifest.quality_report:
        raise PackageContractError("active model package must include a quality report")
    try:
        report = QualityReport.model_validate_json(
            package.artifact_path(manifest.quality_report).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise PackageContractError(f"invalid model package quality report: {exc}") from exc
    expected_targets = {predictor.target for predictor in manifest.predictors}
    actual_targets = {metric.target for metric in report.targets}
    if actual_targets != expected_targets:
        raise PackageContractError("quality report targets do not match package predictors")
    return report


def validate_training_provenance(
    package: VerifiedModelPackage,
    data: WorkbookData,
    contract: TaskContractFixture,
) -> None:
    if package.manifest.provenance.training_data_id != f"sha256:{data.source_sha256}":
        raise PackageContractError("model package training data digest does not match the active source")
    canonical = canonical_training_dataset(package.manifest.task_id, data, contract)
    if package.manifest.provenance.feature_dataset_id != canonical_training_dataset_digest(canonical):
        raise PackageContractError("model package canonical training dataset digest does not match the active source")


def load_active_packages(path: Path = ACTIVE_PACKAGES_PATH) -> ActivePackagesConfig:
    try:
        return ActivePackagesConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageContractError(f"invalid active model package configuration: {exc}") from exc


def resolve_configured_package(
    task_id: str,
    *,
    config_path: Path = ACTIVE_PACKAGES_PATH,
    override: str | Path | None = None,
) -> Path:
    if override is not None:
        return Path(override).resolve(strict=True)
    config = load_active_packages(config_path)
    try:
        relative = Path(config.tasks[task_id].active)
    except KeyError as exc:
        raise PackageContractError(f"active package is not configured for task {task_id}") from exc
    if relative.is_absolute():
        raise PackageContractError("trusted active package references must be relative to the models directory")
    models_root = config_path.resolve().parent
    resolved = (models_root / relative).resolve(strict=True)
    if models_root not in resolved.parents:
        raise PackageContractError("active package reference escapes the models directory")
    return resolved


def set_active_package(
    task_id: str,
    package_root: Path,
    *,
    config_path: Path = ACTIVE_PACKAGES_PATH,
) -> ActivePackagesConfig:
    config = load_active_packages(config_path)
    models_root = config_path.resolve().parent
    resolved = package_root.resolve(strict=True)
    if models_root not in resolved.parents:
        raise PackageContractError("only packages inside the trusted models directory can be activated")
    relative = resolved.relative_to(models_root).as_posix()
    current = config.tasks.get(task_id)
    if current is None:
        raise PackageContractError(f"unknown task in active package configuration: {task_id}")
    if current.active == relative:
        return config
    tasks = dict(config.tasks)
    tasks[task_id] = ActivePackageSelection(active=relative, previous=current.active)
    updated = ActivePackagesConfig(schema_version=config.schema_version, tasks=tasks)
    _write_active_packages(updated, config_path)
    return updated


def rollback_active_package(
    task_id: str,
    *,
    config_path: Path = ACTIVE_PACKAGES_PATH,
) -> ActivePackagesConfig:
    config = load_active_packages(config_path)
    current = config.tasks.get(task_id)
    if current is None or current.previous is None:
        raise PackageContractError(f"no previous active package is recorded for task {task_id}")
    models_root = config_path.resolve().parent
    previous_path = (models_root / current.previous).resolve(strict=True)
    if models_root not in previous_path.parents:
        raise PackageContractError("previous package reference escapes the models directory")
    tasks = dict(config.tasks)
    tasks[task_id] = ActivePackageSelection(active=current.previous, previous=current.active)
    updated = ActivePackagesConfig(schema_version=config.schema_version, tasks=tasks)
    _write_active_packages(updated, config_path)
    return updated


def _write_active_packages(config: ActivePackagesConfig, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(config.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)
