"""Safe, data-only model package contracts and a fixed adapter registry.

Packages identify one of the adapters compiled into this application.  They
never contain an import path, Python source, pickle/joblib object, or callback.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from .task_contracts import TargetRuntimeCapability, TaskDefinition


MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
PACKAGE_SCHEMA_VERSION = "model-package/v1"
RUNTIME_TYPES = {
    "builtin.linear.v1",
    "builtin.exact_gp.v1",
    "builtin.additive_terms.v1",
    "builtin.quantile_linear.v1",
    "sklearn.skops.v1",
    "lightgbm.booster.v1",
    "gpytorch.static_exact_rbf.v1",
    "numpyro.dense_posterior.v1",
}
LIKELIHOOD_IDS = {
    "normal", "student_t", "lognormal", "bernoulli_logit", "poisson_log",
    "negative_binomial_log", "zero_inflated_poisson_log", "ordinal_logit",
}
LIKELIHOOD_TARGET_KINDS = {
    "normal": {"continuous", "continuous_positive"},
    "student_t": {"continuous", "continuous_positive"},
    "lognormal": {"continuous_positive"},
    "bernoulli_logit": {"binary"},
    "poisson_log": {"count"},
    "negative_binomial_log": {"count"},
    "zero_inflated_poisson_log": {"count"},
    "ordinal_logit": {"ordinal"},
}


class PackageContractError(ValueError):
    """A package is malformed, untrusted, or incompatible with this runtime."""


class MissingOptionalDependency(RuntimeError):
    """The package is valid, but this installed Edition lacks its adapter dependency."""


class PackageModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ArtifactSpec(PackageModel):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bytes: Annotated[int, Field(gt=0, le=MAX_ARTIFACT_BYTES)]
    media_type: str = "application/octet-stream"

    @field_validator("path")
    @classmethod
    def relative_file_path(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts or path.name != value.split("/")[-1]:
            raise ValueError("artifact path must be a package-relative file path")
        return value.replace("\\", "/")


class FeaturePipelineSpec(PackageModel):
    id: str
    version: str
    spec: str
    canonical_input_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    output_features: Annotated[tuple[str, ...], Field(min_length=1)]
    artifacts: tuple[str, ...] = ()

    @field_validator("canonical_input_paths")
    @classmethod
    def unique_canonical_input_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not path for path in value):
            raise ValueError("canonical input paths must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("canonical input paths must be unique")
        return value

    @field_validator("output_features")
    @classmethod
    def unique_output_features(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not name for name in value) or len(value) != len(set(value)):
            raise ValueError("output feature names must be unique and non-empty")
        return value


class PipelineFeatureSpec(PackageModel):
    name: Annotated[str, Field(min_length=1)]
    unit: str
    meaning: str
    group: Literal["composition", "process", "categorical", "metallurgy", "heat_pattern", "other"]


class FeaturePipelineDocument(PackageModel):
    """Common contract carried by every task-specific pipeline document."""

    id: str
    version: str
    canonical_input_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    features: Annotated[tuple[PipelineFeatureSpec, ...], Field(min_length=1)]
    missing_composition: str | None = None
    heat_interpolation: str | None = None

    @field_validator("canonical_input_paths")
    @classmethod
    def unique_canonical_input_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not path for path in value):
            raise ValueError("canonical input paths must not be empty")
        if len(value) != len(set(value)):
            raise ValueError("canonical input paths must be unique")
        return value

    @model_validator(mode="after")
    def unique_output_features(self) -> "FeaturePipelineDocument":
        names = [feature.name for feature in self.features]
        if len(names) != len(set(names)):
            raise ValueError("pipeline output feature names must be unique")
        return self


class PredictorSpec(PackageModel):
    id: str
    target: str
    unit: str
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    runtime_type: str
    artifact: str
    predictive_family: str
    architecture_id: str | None = None
    feature_names: Annotated[tuple[str, ...], Field(min_length=1)]
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("runtime_type")
    @classmethod
    def known_runtime(cls, value: str) -> str:
        if value not in RUNTIME_TYPES:
            raise ValueError(f"unsupported runtime_type: {value}")
        return value

    @field_validator("predictive_family")
    @classmethod
    def known_likelihood(cls, value: str) -> str:
        if value not in LIKELIHOOD_IDS and value != "empirical_quantiles":
            raise ValueError(f"unsupported predictive_family: {value}")
        return value

    @field_validator("feature_names")
    @classmethod
    def unique_feature_names(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not name for name in value) or len(value) != len(set(value)):
            raise ValueError("predictor feature names must be unique and non-empty")
        return value

    @model_validator(mode="after")
    def static_architecture_only(self) -> "PredictorSpec":
        if self.runtime_type == "numpyro.dense_posterior.v1" and self.architecture_id != "dense_mlp_v1":
            raise ValueError("numpyro adapter only permits architecture_id=dense_mlp_v1")
        if self.runtime_type == "gpytorch.static_exact_rbf.v1" and self.architecture_id != "exact_rbf_v1":
            raise ValueError("gpytorch adapter only permits architecture_id=exact_rbf_v1")
        if self.runtime_type == "builtin.exact_gp.v1" and self.architecture_id != "exact_rbf_grouped_v1":
            raise ValueError("built-in exact GP adapter only permits architecture_id=exact_rbf_grouped_v1")
        if self.runtime_type == "builtin.additive_terms.v1" and self.architecture_id != "additive_terms_v1":
            raise ValueError("built-in additive adapter only permits architecture_id=additive_terms_v1")
        if self.runtime_type == "builtin.quantile_linear.v1" and self.architecture_id != "quantile_linear_v1":
            raise ValueError("built-in quantile adapter only permits architecture_id=quantile_linear_v1")
        return self


class ProvenanceSpec(PackageModel):
    training_data_id: str
    feature_dataset_id: str
    training_code_revision: str
    dataset_profile_id: str | None = None


class SmokeTestSpec(PackageModel):
    input: str
    expected: str

    @field_validator("input", "expected")
    @classmethod
    def package_relative_file(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts:
            raise ValueError("smoke path must be package-relative")
        return value.replace("\\", "/")


class ModelPackageManifest(PackageModel):
    schema_version: Literal[PACKAGE_SCHEMA_VERSION]
    package_id: str
    package_version: str
    task_id: str
    input_schema_version: str
    input_contract_digest: str | None = None
    runtime_capability_digest: str | None = None
    feature_pipeline: FeaturePipelineSpec
    predictors: tuple[PredictorSpec, ...]
    provenance: ProvenanceSpec
    artifacts: tuple[ArtifactSpec, ...]
    smoke_test: SmokeTestSpec | None = None
    quality_report: str | None = None

    @model_validator(mode="after")
    def references_listed_artifacts(self) -> "ModelPackageManifest":
        listed = {artifact.path for artifact in self.artifacts}
        if len(listed) != len(self.artifacts):
            raise ValueError("artifact paths must be unique")
        needed = {self.feature_pipeline.spec, *self.feature_pipeline.artifacts, *(predictor.artifact for predictor in self.predictors)}
        if self.quality_report:
            needed.add(self.quality_report)
        if self.smoke_test:
            needed.update((self.smoke_test.input, self.smoke_test.expected))
        missing = sorted(needed - listed)
        if missing:
            raise ValueError(f"manifest references unlisted artifacts: {', '.join(missing)}")
        ids = [predictor.id for predictor in self.predictors]
        if len(ids) != len(set(ids)):
            raise ValueError("predictor ids must be unique")
        expected = self.feature_pipeline.output_features
        if any(predictor.feature_names != expected for predictor in self.predictors):
            raise ValueError("predictor feature order must match feature pipeline output_features")
        return self


def ordered_canonical_input_paths(task_definition: "TaskDefinition") -> tuple[str, ...]:
    """Return every canonical input in the sole order declared by a TaskDefinition."""

    return tuple(
        field.path
        for group in sorted(task_definition.input_groups, key=lambda item: item.order)
        for field in sorted(group.fields, key=lambda item: item.order)
    )


def validate_task_definition_canonical_inputs(
    task_definition: "TaskDefinition",
    manifest: ModelPackageManifest,
) -> None:
    """Reject a package whose canonical input order differs from its task."""

    if manifest.task_id != task_definition.id:
        raise PackageContractError(
            f"model package task {manifest.task_id} does not match TaskDefinition {task_definition.id}"
        )
    expected = ordered_canonical_input_paths(task_definition)
    actual = manifest.feature_pipeline.canonical_input_paths
    if actual != expected:
        raise PackageContractError(
            "model package canonical input order does not match TaskDefinition: "
            f"expected {expected}, got {actual}"
        )


class PredictiveSummary(PackageModel):
    target: str
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    unit: str
    point_statistic: Literal["mean", "median", "probability", "rate", "expected_category"]
    point_estimate: float
    quantiles: dict[str, float] = Field(default_factory=dict)
    event_probability: float | None = None
    distribution: dict[str, Any]
    uncertainty_components: dict[str, float] | None = None
    warnings: tuple[str, ...] = ()


class TermContribution(PackageModel):
    term_id: str
    kind: Literal["linear", "bspline_univariate", "categorical_lookup"]
    feature_names: tuple[str, ...]
    contribution: float


class AdditiveExplanation(PackageModel):
    target: str
    link_id: Literal["identity"]
    intercept: float
    terms: tuple[TermContribution, ...]
    link_score: float
    prediction: float


def validate_predictive_summary(
    summary: PredictiveSummary,
    spec: PredictorSpec,
    capability: TargetRuntimeCapability | None = None,
) -> None:
    """Validate the semantic contract shared by every prediction adapter."""
    if (summary.target, summary.target_kind, summary.unit) != (spec.target, spec.target_kind, spec.unit):
        raise PackageContractError(f"predictor {spec.id!r} returned incompatible target metadata")
    if not math.isfinite(summary.point_estimate):
        raise PackageContractError(f"predictor {spec.id!r} returned a non-finite point estimate")
    family = summary.distribution.get("family")
    if family != spec.predictive_family:
        raise PackageContractError(
            f"predictor {spec.id!r} returned distribution family {family!r}, expected {spec.predictive_family!r}"
        )
    expected_kinds = LIKELIHOOD_TARGET_KINDS.get(family)
    if expected_kinds is not None and spec.target_kind not in expected_kinds:
        raise PackageContractError(f"predictor {spec.id!r} uses {family} with incompatible target kind")
    try:
        ordered_quantiles = sorted((float(level), value) for level, value in summary.quantiles.items())
    except ValueError as exc:
        raise PackageContractError(f"predictor {spec.id!r} returned an invalid quantile level") from exc
    levels = [level for level, _ in ordered_quantiles]
    if any(not math.isfinite(level) or not 0 <= level <= 1 for level in levels) or len(levels) != len(set(levels)):
        raise PackageContractError(f"predictor {spec.id!r} returned invalid or duplicate quantile levels")
    if any(not math.isfinite(value) for _, value in ordered_quantiles):
        raise PackageContractError(f"predictor {spec.id!r} returned non-finite quantiles")
    if any(left[1] > right[1] for left, right in zip(ordered_quantiles, ordered_quantiles[1:])):
        raise PackageContractError(f"predictor {spec.id!r} returned unordered quantiles")
    if spec.target_kind == "binary" and not (
        0 <= summary.point_estimate <= 1
        and summary.event_probability is not None
        and 0 <= summary.event_probability <= 1
        and math.isclose(summary.event_probability, summary.point_estimate, rel_tol=0, abs_tol=1e-12)
    ):
        raise PackageContractError(f"predictor {spec.id!r} returned invalid binary probability semantics")
    if spec.target_kind == "binary" and any(not 0 <= value <= 1 for _, value in ordered_quantiles):
        raise PackageContractError(f"predictor {spec.id!r} returned binary quantiles outside probability support")
    values = [summary.point_estimate, *(value for _, value in ordered_quantiles)]
    requires_nonnegative_output = spec.target_kind == "count" or (
        spec.target_kind == "continuous_positive" and summary.distribution.get("support") == "positive"
    )
    if requires_nonnegative_output and any(value < 0 for value in values):
        raise PackageContractError(f"predictor {spec.id!r} returned values outside nonnegative support")
    if spec.target_kind in {"binary", "ordinal"} and spec.unit not in {"", "1"}:
        raise PackageContractError(f"predictor {spec.id!r} must use a dimensionless unit")
    if spec.target_kind == "ordinal":
        categories = summary.distribution.get("categories")
        if not isinstance(categories, list) or len(categories) < 2 or len(categories) != len(set(categories)):
            raise PackageContractError(f"predictor {spec.id!r} returned invalid ordinal category metadata")
        if not 0 <= summary.point_estimate <= len(categories) - 1 or any(
            value < 0 or value > len(categories) - 1 for _, value in ordered_quantiles
        ):
            raise PackageContractError(f"predictor {spec.id!r} returned values outside ordinal support")
    if capability is None:
        return
    if summary.point_statistic not in capability.point_statistics:
        raise PackageContractError(f"predictor {spec.id!r} returned an undeclared point statistic")
    if bool(summary.quantiles) != capability.quantiles:
        raise PackageContractError(f"predictor {spec.id!r} quantile capability does not match its smoke output")
    has_standard_deviation = "std" in summary.distribution
    if has_standard_deviation != capability.standard_deviation:
        raise PackageContractError(f"predictor {spec.id!r} standard-deviation capability does not match its smoke output")
    has_parametric_distribution = summary.distribution.get("family") != "empirical_quantiles"
    if has_parametric_distribution != capability.parametric_distribution:
        raise PackageContractError(f"predictor {spec.id!r} distribution capability does not match its smoke output")
    if bool(summary.uncertainty_components) != capability.uncertainty_components:
        raise PackageContractError(f"predictor {spec.id!r} uncertainty-component capability does not match its smoke output")
    if capability.samples:
        raise PackageContractError(f"predictor {spec.id!r} declares samples that PredictiveSummary does not expose")


class LoadedPredictor(Protocol):
    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary: ...


class Adapter(Protocol):
    runtime_type: str

    def load(self, package: "VerifiedModelPackage", predictor: PredictorSpec) -> LoadedPredictor: ...


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class VerifiedModelPackage:
    root: Path
    manifest: ModelPackageManifest
    artifacts: dict[str, Path]
    registry: "AdapterRegistry"

    def artifact_path(self, relative_path: str) -> Path:
        try:
            return self.artifacts[relative_path]
        except KeyError as exc:
            raise PackageContractError(f"artifact was not verified: {relative_path}") from exc

    def load_predictor(self, predictor_id: str) -> LoadedPredictor:
        spec = next((item for item in self.manifest.predictors if item.id == predictor_id), None)
        if spec is None:
            raise PackageContractError(f"unknown predictor id: {predictor_id}")
        return self.registry.adapter_for(spec.runtime_type).load(self, spec)

    @property
    def manifest_sha256(self) -> str:
        return _sha256(self.root / "manifest.json")


class AdapterRegistry:
    """Explicit allow-list; package data can never select a Python module."""

    def __init__(self, adapters: tuple[Adapter, ...] | None = None) -> None:
        if adapters is None:
            from .adapters.builtin_linear import BuiltinLinearAdapter
            from .adapters.builtin_additive_terms import BuiltinAdditiveTermsAdapter
            from .adapters.builtin_quantile_linear import BuiltinQuantileLinearAdapter
            from .adapters.builtin_exact_gp import BuiltinExactGPAdapter
            from .adapters.gpytorch_static import GPyTorchStaticAdapter
            from .adapters.lightgbm_booster import LightGBMBoosterAdapter
            from .adapters.numpyro_posterior import NumpyroDensePosteriorAdapter
            from .adapters.sklearn_skops import SklearnSkopsAdapter

            adapters = (BuiltinLinearAdapter(), BuiltinExactGPAdapter(), BuiltinAdditiveTermsAdapter(), BuiltinQuantileLinearAdapter(), SklearnSkopsAdapter(), LightGBMBoosterAdapter(), GPyTorchStaticAdapter(), NumpyroDensePosteriorAdapter())
        self._adapters = {adapter.runtime_type: adapter for adapter in adapters}
        if set(self._adapters) != RUNTIME_TYPES:
            raise PackageContractError("adapter registry must implement exactly the approved runtime types")

    def adapter_for(self, runtime_type: str) -> Adapter:
        try:
            return self._adapters[runtime_type]
        except KeyError as exc:
            raise PackageContractError(f"runtime is not registered: {runtime_type}") from exc


class ModelPackageLoader:
    def __init__(self, registry: AdapterRegistry | None = None, *, max_artifact_bytes: int = MAX_ARTIFACT_BYTES) -> None:
        self.registry = registry or AdapterRegistry()
        self.max_artifact_bytes = max_artifact_bytes

    def load(self, package_root: str | Path) -> VerifiedModelPackage:
        try:
            root = Path(package_root).resolve(strict=True)
        except OSError as exc:
            raise PackageContractError(f"model package root cannot be resolved: {exc}") from exc
        if not root.is_dir():
            raise PackageContractError("model package root must be a directory")
        manifest_path = root / "manifest.json"
        try:
            raw_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = ModelPackageManifest.model_validate(raw_manifest)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise PackageContractError(f"invalid model package manifest: {exc}") from exc
        artifacts: dict[str, Path] = {}
        for spec in manifest.artifacts:
            try:
                candidate = (root / spec.path).resolve(strict=True)
            except OSError as exc:
                raise PackageContractError(f"artifact cannot be resolved: {spec.path}") from exc
            if root not in candidate.parents:
                raise PackageContractError(f"artifact escapes package root: {spec.path}")
            if not candidate.is_file():
                raise PackageContractError(f"artifact is not a regular file: {spec.path}")
            actual_size = candidate.stat().st_size
            if actual_size != spec.bytes or actual_size > self.max_artifact_bytes:
                raise PackageContractError(f"artifact size mismatch: {spec.path}")
            if _sha256(candidate) != spec.sha256:
                raise PackageContractError(f"artifact hash mismatch: {spec.path}")
            artifacts[spec.path] = candidate
        try:
            raw_pipeline = json.loads(artifacts[manifest.feature_pipeline.spec].read_text(encoding="utf-8"))
            pipeline = FeaturePipelineDocument.model_validate(raw_pipeline)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise PackageContractError(f"invalid feature pipeline specification: {exc}") from exc
        if (pipeline.id, pipeline.version) != (
            manifest.feature_pipeline.id,
            manifest.feature_pipeline.version,
        ):
            raise PackageContractError("feature pipeline id/version differs between manifest and specification")
        if pipeline.canonical_input_paths != manifest.feature_pipeline.canonical_input_paths:
            raise PackageContractError(
                "canonical input paths differ between model package manifest and pipeline specification"
            )
        pipeline_outputs = tuple(feature.name for feature in pipeline.features)
        if pipeline_outputs != manifest.feature_pipeline.output_features:
            raise PackageContractError(
                "pipeline output feature order differs from model package manifest output_features"
            )
        return VerifiedModelPackage(root=root, manifest=manifest, artifacts=artifacts, registry=self.registry)
