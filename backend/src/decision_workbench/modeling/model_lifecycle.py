from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from decision_workbench.contracts.task_contracts import (
    RuntimeCapability,
    TaskContractFixture,
    TaskDefinition,
)
from decision_workbench.data.importer import training_context_key
from decision_workbench.data.profile_family_registry import (
    build_canonical_training_features,
    lifecycle_profile_for_data,
    load_profile_document,
    normalize_profile_digest_payload,
    profile_output_columns,
)
from decision_workbench.modeling.packages.contracts import (
    FEATURE_DATASET_DIGEST_FLOAT15,
    FEATURE_DATASET_DIGEST_LEGACY,
    FeatureDatasetDigestAlgorithm,
    FeaturePipelineDocument,
    PackageContractError,
)
from decision_workbench.modeling.packages.verification import VerifiedModelPackage
from decision_workbench.task_composition.catalog import (
    registered_task_modules,
    task_module,
)
from decision_workbench.task_composition.ports import DataDescriptor

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
MODELS_ROOT = REPOSITORY_ROOT / "models"
PACKAGES_ROOT = MODELS_ROOT / "packages"
ACTIVE_PACKAGES_PATH = MODELS_ROOT / "active-packages.json"
AVAILABLE_PACKAGES_PATH = MODELS_ROOT / "available-packages.json"
MODEL_STORE_ENV = "WORKBENCH_MODEL_STORE_PATH"
DATASET_PROFILE_PATH = Path(__file__).parent.parent / "data" / "dataset-input-profile-tutorial.json"


def personal_model_store_path() -> Path:
    configured = os.getenv(MODEL_STORE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.getenv("LOCALAPPDATA", "").strip()
    if local_app_data:
        return (
            Path(local_app_data)
            / "Material Decision Workbench"
            / "models"
        ).resolve()
    xdg_data_home = os.getenv("XDG_DATA_HOME", "").strip()
    base = (
        Path(xdg_data_home).expanduser()
        if xdg_data_home
        else Path.home() / ".local" / "share"
    )
    return (base / "material-decision-workbench" / "models").resolve()


def validate_personal_model_store_path(store_root: Path) -> Path:
    root = store_root.expanduser().resolve()
    if root == REPOSITORY_ROOT or REPOSITORY_ROOT in root.parents:
        raise PackageContractError(
            "personal model store must be outside the repository"
        )
    return root


def ensure_available_packages_config(store_root: Path) -> Path:
    root = validate_personal_model_store_path(store_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "available-packages.json"
    if not path.exists():
        path.write_text(
            json.dumps(
                {
                    "schema_version": "available-model-packages/v1",
                    "packages": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    else:
        load_available_packages(path)
    return path


@contextmanager
def staged_package_destination(destination: Path, *, replace: bool) -> Iterator[Path]:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        destination.exists()
        and replace
        and (destination == PACKAGES_ROOT or PACKAGES_ROOT in destination.parents)
    ):
        raise FileExistsError(
            "checked-in Model Packages are immutable; build a new package ID and directory"
        )
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


class AvailablePackagesConfig(LifecycleModel):
    schema_version: Literal["available-model-packages/v1"]
    packages: tuple[str, ...]


class TargetQualityMetric(LifecycleModel):
    target: Annotated[str, Field(min_length=1)]
    parent_conditions: Annotated[int, Field(ge=2)]
    mae: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    rmse: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    interval_coverage_90: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    interval_coverage_method: Literal[
        "cross-fitted-oof-residual-quantiles",
        "cross-fitted-oof-normal-scale",
        "nested-grouped-oof-residual-quantiles",
        "nested-grouped-oof-normal-scale",
        "loo-predictive-interval",
        "grouped-fold-predictive-interval",
        "temporal-holdout-residual-quantiles",
        "temporal-holdout-normal-scale",
        "temporal-holdout-predictive-interval",
        "posterior-predictive-interval",
    ] | None = None
    interval_coverage_observations: Annotated[int, Field(ge=1)] | None = None


class TargetValidationEvidence(LifecycleModel):
    cohort_digest: Annotated[
        str,
        Field(pattern=r"^sha256:[0-9a-f]{64}$"),
    ]
    fold_digest: Annotated[
        str,
        Field(pattern=r"^sha256:[0-9a-f]{64}$"),
    ]
    validation_plan_digest: Annotated[
        str,
        Field(pattern=r"^sha256:[0-9a-f]{64}$"),
    ]


class QualityReport(LifecycleModel):
    schema_version: Literal["model-quality-report/v1"]
    split: Literal[
        "leave-one-parent-condition-out",
        "grouped-parent-condition-k-fold",
        "independent-source-row-k-fold",
        "typed-validation-plan",
    ]
    folds: Annotated[int, Field(ge=2)] | None = None
    targets: Annotated[tuple[TargetQualityMetric, ...], Field(min_length=1)]
    validation_plans: dict[str, dict[str, Any]] | None = None
    validation_diagnostics: dict[str, dict[str, Any]] | None = None
    validation_evidence: dict[str, TargetValidationEvidence] | None = None

    @model_validator(mode="after")
    def split_has_matching_fold_count(self) -> "QualityReport":
        if self.split == "typed-validation-plan":
            return self
        if (self.split != "leave-one-parent-condition-out") != (self.folds is not None):
            raise ValueError("k-fold quality reports require folds, and leave-one-out reports must omit it")
        return self


class SamplingDiagnosticsReport(LifecycleModel):
    schema_version: Literal["sampling-diagnostics/v1"]
    chains: Annotated[int, Field(ge=2)]
    draws_per_chain: Annotated[int, Field(ge=100)]
    warmup_per_chain: Annotated[int, Field(ge=100)]
    divergences: Annotated[int, Field(ge=0)]
    minimum_effective_sample_size: Annotated[float, Field(ge=50, allow_inf_nan=False)]
    maximum_r_hat: Annotated[float, Field(ge=0, le=1.1, allow_inf_nan=False)]
    finite_export: Literal[True]

    @model_validator(mode="after")
    def has_no_divergences(self) -> "SamplingDiagnosticsReport":
        if self.divergences:
            raise ValueError("posterior sampling must have zero divergences")
        return self


def _semantic_digest(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def task_input_contract_digest(task: TaskDefinition) -> str:
    input_groups = [group.model_dump(mode="json") for group in task.input_groups]
    # Numeric exploration semantics constrain candidate construction, not the
    # canonical feature shape consumed by an immutable Model Package.  Keep
    # existing Packages and saved prediction snapshots valid when a Task gains
    # integer, step, or log search semantics.
    for group in input_groups:
        for field in group["fields"]:
            field.pop("numeric_domain_kind", None)
            field.pop("step", None)
            field.pop("search_scale", None)
    return _semantic_digest({
        "schema_version": task.schema_version,
        "task_id": task.id,
        "input_groups": input_groups,
    })


def runtime_capability_digest(capability: RuntimeCapability) -> str:
    payload = capability.model_dump(mode="json")
    # Added after the first packaged contracts.  Omitting only its false value
    # preserves the immutable digest of every pre-existing Package.
    if not payload["operations"].get("target_specific_similarity", False):
        payload["operations"].pop("target_specific_similarity", None)
    for model_target, target in zip(capability.targets, payload["targets"], strict=True):
        # `target_kind=continuous` was introduced after existing Package
        # capability digests.  Preserve those immutable artifact identities
        # unless a Task explicitly opted into the semantic field.
        if "target_kind" not in model_target.model_fields_set:
            target.pop("target_kind", None)
        if not target.get("explanation", False):
            target.pop("explanation", None)
    return _semantic_digest(payload)


def dataset_profile_digest(path: Path | Any = DATASET_PROFILE_PATH) -> str:
    if hasattr(path, "model_dump"):
        profile = path
    else:
        profile = load_profile_document(Path(path))
    payload = profile.model_dump(mode="json", exclude={"task_definitions"})
    shared = payload.get("shared")
    if isinstance(shared, dict) and not shared.get("column_aliases"):
        # The optional alias map was added after existing immutable Packages.
        # Its empty value carries no semantic change, so preserve their digest.
        shared.pop("column_aliases", None)
    if isinstance(payload.get("curation_recipe"), dict):
        for rule in payload["curation_recipe"].get("columns", {}).values():
            if isinstance(rule, dict):
                if rule.get("warn_below") is None:
                    rule.pop("warn_below", None)
                if rule.get("warn_above") is None:
                    rule.pop("warn_above", None)
                if rule.get("reject_below") is None:
                    rule.pop("reject_below", None)
                if rule.get("reject_above") is None:
                    rule.pop("reject_above", None)
    # Optional contract additions must not invalidate existing packages when the
    # active profile does not use them.
    if payload.get("curation_recipe") is None:
        payload.pop("curation_recipe", None)
    for item in payload.get("inputs", []):
        if item.get("numeric_missing") == {
            "strategy": "reject",
            "value": None,
            "reason": None,
        }:
            item.pop("numeric_missing", None)
        if item.get("categorical_missing") == {
            "strategy": "reject",
            "category": None,
        }:
            item.pop("categorical_missing", None)
        if item.get("unknown_category") == {
            "strategy": "reject",
            "other_choice": None,
        }:
            item.pop("unknown_category", None)
    normalize_profile_digest_payload(profile, payload)
    if payload.get("ridge_alpha") in {None, 1.0}:
        payload.pop("ridge_alpha", None)
    shared = payload.get("shared")
    if isinstance(shared, dict):
        for key in ("policy_defaults", "optional_roles", "optional_technical_fields"):
            if not shared.get(key):
                shared.pop(key, None)
        for join in shared.get("relation", {}).get("joins", []):
            if not join.get("alternate_columns"):
                join.pop("alternate_columns", None)
    for task in payload.get("tasks", {}).values():
        for mapping in task.get("mappings", []):
            if mapping.get("measurement_point_fallback") is None:
                mapping.pop("measurement_point_fallback", None)
        for observation in task.get("observations", []):
            if observation.get("parent_column") is None:
                observation.pop("parent_column", None)
            for key in ("optional_metadata_keys", "optional_auxiliary_keys"):
                if not observation.get(key):
                    observation.pop(key, None)
            for target in (*observation.get("targets", []), *observation.get("auxiliary", [])):
                if target.get("column") is None:
                    target.pop("column", None)
                if not target.get("columns"):
                    target.pop("columns", None)
    return _semantic_digest(payload)


def canonical_training_dataset(
    task_id: str,
    data: DataDescriptor,
    contract: TaskContractFixture,
    *,
    pipeline_version: str | None = None,
) -> dict[str, Any]:
    specialized = getattr(data, "canonical_training_dataset", None)
    if callable(specialized):
        return specialized(contract, pipeline_version=pipeline_version)
    runtime_profile = (
        getattr(data, "profile", None)
        or load_profile_document(Path(data.profile_path))
    )
    lifecycle_profile = lifecycle_profile_for_data(data)
    profile_columns = profile_output_columns(runtime_profile, task_id)
    output_columns = {
        output.key: tuple(dict.fromkeys((*output.measurement_keys, *profile_columns.get(output.key, ()))))
        for output in contract.task_definition.outputs
    }
    builder = task_module(task_id).feature_row_builder
    if pipeline_version == "2.0.0":
        if task_id == "annealed-properties-v1":
            from decision_workbench.modeling.feature_pipeline import (
                build_feature_bundle_v2,
                candidate_from_observation,
            )

            builder = lambda row, defaults: (
                None
                if (candidate := candidate_from_observation(row)) is None
                else build_feature_bundle_v2(candidate, defaults)
            )
        elif task_id == "hot-rolled-properties-v1":
            from decision_workbench.modeling.hot_rolling_feature_pipeline import (
                build_hot_rolling_features_v2,
                candidate_from_observation,
            )

            builder = lambda row, defaults: (
                None
                if (candidate := candidate_from_observation(row)) is None
                else build_hot_rolling_features_v2(candidate, defaults)
            )
    rows: list[dict[str, Any]] = []
    runtime_bundles: dict[str, Any] = {}
    for observation in data.observations:
        if not observation["eligible"]:
            continue
        feature_set = build_canonical_training_features(
            runtime_profile,
            data,
            observation,
            builder,
        )
        if feature_set is None:
            continue
        outputs: dict[str, float] = {}
        for output in contract.task_definition.outputs:
            source_column = next(
                (column for column in output_columns[output.key] if column in observation["outputs"]), None
            )
            if source_column is not None:
                outputs[output.key] = float(observation["outputs"][source_column])
        if not outputs:
            continue
        observation_id = str(observation["id"])
        runtime_bundles[observation_id] = feature_set.runtime_bundle
        training_row = {
            "observation_id": observation_id,
            "parent_key": str(observation["parent_key"]),
            "features": feature_set.training_features,
            "outputs": outputs,
        }
        if observation.get("condition_context_id"):
            training_row.update({
                "condition_context_id": training_context_key(observation),
                "composition_key": observation.get("composition_key"),
                "relation_context_ids": list(observation.get("relation_context_ids", [])),
            })
        rows.append(training_row)
    rows.sort(key=lambda item: (training_context_key(item), item["observation_id"]))
    if not rows:
        raise ValueError(f"no eligible canonical training rows for {task_id}")
    first_bundle = runtime_bundles[rows[0]["observation_id"]]
    assert first_bundle is not None
    return {
        "schema_version": "canonical-training-dataset/v1",
        "task_id": task_id,
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "dataset_profile_digest": dataset_profile_digest(lifecycle_profile),
        "source_data_digest": f"sha256:{data.source_sha256}",
        "feature_pipeline": {
            "id": first_bundle.pipeline_id,
            "version": first_bundle.pipeline_version,
            "features": [
                {"name": item.name, "unit": item.unit, "group": item.group}
                for item in first_bundle.definitions
            ],
            **(
                {
                    "missing_policy": {
                        "imputation_values": data.feature_imputation_values,
                        "digest": _semantic_digest(data.feature_imputation_values),
                    }
                }
                if getattr(data, "feature_imputation_values", None)
                else {}
            ),
        },
        "composition_defaults": data.medians,
        "rows": rows,
    }


def _finite_float15_digest_value(payload: Any) -> Any:
    if isinstance(payload, float):
        if not math.isfinite(payload):
            raise ValueError("canonical training dataset digest requires finite floats")
        normalized = "0" if payload == 0 else format(payload, ".15g")
        return {"$finite_float15": normalized}
    if isinstance(payload, dict):
        return {
            key: _finite_float15_digest_value(value)
            for key, value in payload.items()
        }
    if isinstance(payload, (list, tuple)):
        return [_finite_float15_digest_value(value) for value in payload]
    return payload


def canonical_training_dataset_digest(
    payload: dict[str, Any],
    *,
    algorithm: FeatureDatasetDigestAlgorithm = FEATURE_DATASET_DIGEST_LEGACY,
) -> str:
    if algorithm == FEATURE_DATASET_DIGEST_LEGACY:
        return _semantic_digest(payload)
    if algorithm != FEATURE_DATASET_DIGEST_FLOAT15:
        raise ValueError(f"unsupported feature dataset digest algorithm: {algorithm}")
    return _semantic_digest({
        "algorithm": algorithm,
        "payload": _finite_float15_digest_value(payload),
    })


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
    requires_sampling_diagnostics = any(
        predictor.runtime_type == "builtin.posterior_linear.v1"
        and predictor.config.get("method") == "regularized_horseshoe"
        for predictor in manifest.predictors
    )
    if requires_sampling_diagnostics:
        try:
            SamplingDiagnosticsReport.model_validate_json(
                package.artifact_path("reports/training-diagnostics.json").read_text(encoding="utf-8")
            )
        except (KeyError, OSError, ValueError) as exc:
            raise PackageContractError(f"invalid posterior sampling diagnostics: {exc}") from exc
    return report


def _line_ending_rewrite_hint(source_path: str, expected_digest: str) -> str:
    """Name line-ending rewriting when it alone explains a source digest mismatch."""
    try:
        raw = Path(source_path).read_bytes()
    except OSError:
        return ""
    if b"\r\n" not in raw:
        return ""
    normalized = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    if f"sha256:{normalized}" != expected_digest:
        return ""
    return (
        f"; {source_path} holds CRLF bytes whose LF form matches the package."
        " The checkout rewrote line endings, so restore the committed bytes"
        " (git checkout with the .gitattributes rules in place) instead of rebuilding the package"
    )


def validate_training_provenance(
    package: VerifiedModelPackage,
    data: DataDescriptor,
    contract: TaskContractFixture,
) -> None:
    expected_training_data = package.manifest.provenance.training_data_id
    if expected_training_data != f"sha256:{data.source_sha256}":
        raise PackageContractError(
            "model package training data digest does not match the active source"
            f" (package={expected_training_data}, source=sha256:{data.source_sha256})"
            + _line_ending_rewrite_hint(data.source_path, expected_training_data)
        )
    canonical = canonical_training_dataset(
        package.manifest.task_id,
        data,
        contract,
        pipeline_version=package.manifest.feature_pipeline.version,
    )
    pipeline_document = FeaturePipelineDocument.model_validate_json(
        package.artifact_path(
            package.manifest.feature_pipeline.spec
        ).read_text(encoding="utf-8")
    )
    if pipeline_document.feature_recipe is not None:
        from decision_workbench.modeling.training.feature_recipe import (
            apply_feature_recipe_to_canonical_dataset,
            load_feature_recipe_artifacts,
        )

        recipe_ref = pipeline_document.feature_recipe
        recipe, state = load_feature_recipe_artifacts(
            package.artifact_path(recipe_ref.recipe),
            package.artifact_path(recipe_ref.state),
        )
        authoring = task_module(package.manifest.task_id).standard_model_authoring
        if authoring is None:
            raise PackageContractError(
                "feature recipe package requires standard model authoring"
            )
        apply_feature_recipe_to_canonical_dataset(
            canonical,
            data,
            authoring.candidate_builder,
            recipe,
            state,
        )
    if package.manifest.provenance.feature_dataset_id != canonical_training_dataset_digest(
        canonical,
        algorithm=package.manifest.provenance.feature_dataset_digest_algorithm,
    ):
        raise PackageContractError("model package canonical training dataset digest does not match the active source")
    validate_training_rows_within_allowed_range(data, contract)


def training_range_drift(
    data: DataDescriptor,
    contract: TaskContractFixture,
) -> dict[str, tuple[float, float]]:
    """Numeric inputs whose real training values fall outside the declared range.

    Reported rather than raised: ``training_range`` is a declared observation of
    the data, so drift means the TaskDefinition needs regenerating, which is a
    human decision about a scientific contract.
    """

    observed = _observed_numeric_ranges(data, contract)
    drift: dict[str, tuple[float, float]] = {}
    for path, field in _numeric_fields(contract).items():
        seen = observed.get(path)
        if seen is None or field.training_range is None:
            continue
        if seen[0] < field.training_range.min or seen[1] > field.training_range.max:
            drift[path] = seen
    return drift


def validate_training_rows_within_allowed_range(
    data: DataDescriptor,
    contract: TaskContractFixture,
) -> None:
    """Eligible training rows must sit inside the TaskDefinition's allowed range.

    Candidates are validated against ``allowed_range``, so a training value
    outside it can never be reached by a candidate. Its presence means the source
    data and the task contract disagree. Swapping in same-shaped data must not
    widen the real training distribution past the declared contract silently.
    """

    observed = _observed_numeric_ranges(data, contract)
    violations = {
        path: seen
        for path, field in _numeric_fields(contract).items()
        if (seen := observed.get(path)) is not None
        and field.allowed_range is not None
        and (seen[0] < field.allowed_range.min or seen[1] > field.allowed_range.max)
    }
    if violations:
        detail = ", ".join(
            f"{path}: 実データ {low:g}–{high:g}"
            for path, (low, high) in sorted(violations.items())
        )
        raise PackageContractError(
            "学習データがTaskDefinitionのallowed_rangeを超えています。"
            "元データを修正するか、TaskDefinitionの範囲を人が見直してください: "
            + detail
        )


def _numeric_fields(contract: TaskContractFixture) -> dict[str, Any]:
    return {
        field.path: field
        for group in contract.task_definition.input_groups
        for field in group.fields
        if field.kind == "number"
    }


def _observed_numeric_ranges(
    data: DataDescriptor,
    contract: TaskContractFixture,
) -> dict[str, tuple[float, float]]:
    """Min/max per declared numeric input across eligible training rows."""

    declared = set(_numeric_fields(contract))
    task_id = contract.task_definition.id
    ranges: dict[str, tuple[float, float]] = {}
    for observation in data.observations:
        if not observation.get("eligible"):
            continue
        # 1つのWorkbookが複数Taskの観測を持つ。他Taskの行を混ぜない。
        row_task = observation.get("task_id")
        if row_task is not None and row_task != task_id:
            continue
        flat: dict[str, Any] = {}
        for key, value in (observation.get("composition") or {}).items():
            flat[f"composition.{key}"] = value
        for key, value in (observation.get("features") or {}).items():
            flat[f"process.{key}"] = value
        for key, value in (observation.get("canonical_inputs") or {}).items():
            flat[key] = value
        for path in declared & set(flat):
            value = flat[path]
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            numeric = float(value)
            if not math.isfinite(numeric):
                continue
            low, high = ranges.get(path, (numeric, numeric))
            ranges[path] = (min(low, numeric), max(high, numeric))
    return ranges


def load_active_packages(path: Path = ACTIVE_PACKAGES_PATH) -> ActivePackagesConfig:
    try:
        return ActivePackagesConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackageContractError(f"invalid active model package configuration: {exc}") from exc


def load_available_packages(
    path: Path = AVAILABLE_PACKAGES_PATH,
) -> AvailablePackagesConfig:
    try:
        return AvailablePackagesConfig.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise PackageContractError(
            f"invalid available model package configuration: {exc}"
        ) from exc


def register_available_package(
    package_root: Path,
    *,
    config_path: Path = AVAILABLE_PACKAGES_PATH,
) -> AvailablePackagesConfig:
    config = load_available_packages(config_path)
    models_root = config_path.resolve().parent
    resolved = package_root.resolve(strict=True)
    if models_root not in resolved.parents:
        raise PackageContractError(
            "only packages inside the trusted models directory can be registered"
        )
    relative = resolved.relative_to(models_root).as_posix()
    if relative in config.packages:
        return config
    updated = AvailablePackagesConfig(
        schema_version=config.schema_version,
        packages=(*config.packages, relative),
    )
    temporary = config_path.with_suffix(config_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(updated.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(config_path)
    return updated


def validate_active_package_task_set(
    config: ActivePackagesConfig,
    task_ids: set[str] | None = None,
) -> None:
    expected = set(registered_task_modules()) if task_ids is None else task_ids
    actual = set(config.tasks)
    if actual != expected:
        raise PackageContractError(
            "active package tasks must exactly match registered TaskModules; "
            f"missing={sorted(expected - actual)}, unknown={sorted(actual - expected)}"
        )


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
        raise PackageContractError(
            f"no previous active package is recorded for task {task_id}; "
            "activeの切替は npm run model:activate を通す（JSONを直接編集するとpreviousが記録されない）"
        )
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
