"""Safe, data-only model package contracts and a fixed adapter registry.

Packages identify one of the adapters compiled into this application.  They
never contain an import path, Python source, pickle/joblib object, or callback.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
PACKAGE_SCHEMA_VERSION = "model-package/v1"
RUNTIME_TYPES = {
    "builtin.linear.v1",
    "sklearn.skops.v1",
    "lightgbm.booster.v1",
    "gpytorch.static_exact_rbf.v1",
    "numpyro.dense_posterior.v1",
}
LIKELIHOOD_IDS = {
    "normal", "student_t", "lognormal", "bernoulli_logit", "poisson_log",
    "negative_binomial_log", "zero_inflated_poisson_log", "ordinal_logit",
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
    output_features: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()


class PredictorSpec(PackageModel):
    id: str
    target: str
    unit: str
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    runtime_type: str
    artifact: str
    predictive_family: str
    architecture_id: str | None = None
    feature_names: tuple[str, ...]
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

    @model_validator(mode="after")
    def static_architecture_only(self) -> "PredictorSpec":
        if self.runtime_type == "numpyro.dense_posterior.v1" and self.architecture_id != "dense_mlp_v1":
            raise ValueError("numpyro adapter only permits architecture_id=dense_mlp_v1")
        if self.runtime_type == "gpytorch.static_exact_rbf.v1" and self.architecture_id != "exact_rbf_v1":
            raise ValueError("gpytorch adapter only permits architecture_id=exact_rbf_v1")
        return self


class ProvenanceSpec(PackageModel):
    training_data_id: str
    feature_dataset_id: str
    training_code_revision: str


class ModelPackageManifest(PackageModel):
    schema_version: Literal[PACKAGE_SCHEMA_VERSION]
    package_id: str
    package_version: str
    task_id: str
    input_schema_version: str
    feature_pipeline: FeaturePipelineSpec
    predictors: tuple[PredictorSpec, ...]
    provenance: ProvenanceSpec
    artifacts: tuple[ArtifactSpec, ...]
    smoke_test: dict[str, str] | None = None

    @model_validator(mode="after")
    def references_listed_artifacts(self) -> "ModelPackageManifest":
        listed = {artifact.path for artifact in self.artifacts}
        if len(listed) != len(self.artifacts):
            raise ValueError("artifact paths must be unique")
        needed = {self.feature_pipeline.spec, *self.feature_pipeline.artifacts, *(predictor.artifact for predictor in self.predictors)}
        missing = sorted(needed - listed)
        if missing:
            raise ValueError(f"manifest references unlisted artifacts: {', '.join(missing)}")
        ids = [predictor.id for predictor in self.predictors]
        if len(ids) != len(set(ids)):
            raise ValueError("predictor ids must be unique")
        return self


class PredictiveSummary(PackageModel):
    target: str
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    unit: str
    point_statistic: Literal["mean", "median", "probability", "rate", "expected_category"]
    point_estimate: float
    quantiles: dict[str, float] = Field(default_factory=dict)
    event_probability: float | None = None
    distribution: dict[str, Any]
    warnings: tuple[str, ...] = ()


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
            from .adapters.gpytorch_static import GPyTorchStaticAdapter
            from .adapters.lightgbm_booster import LightGBMBoosterAdapter
            from .adapters.numpyro_posterior import NumpyroDensePosteriorAdapter
            from .adapters.sklearn_skops import SklearnSkopsAdapter

            adapters = (BuiltinLinearAdapter(), SklearnSkopsAdapter(), LightGBMBoosterAdapter(), GPyTorchStaticAdapter(), NumpyroDensePosteriorAdapter())
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
        return VerifiedModelPackage(root=root, manifest=manifest, artifacts=artifacts, registry=self.registry)
