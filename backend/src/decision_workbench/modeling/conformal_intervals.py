"""A data-only split-conformal wrapper for an already verified predictor.

This is deliberately not a new predictor runtime.  A wrapper pins a base
Package digest and a calibration-score artifact, then adds an explicitly
labelled interval to the base point prediction.  It never manufactures a
standard deviation, samples, probability, or parametric distribution.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, Sequence

from pydantic import Field, field_validator, model_validator

from decision_workbench.contracts.model_capability_contracts import (
    CapabilityLayerIdentity,
    ModelPackageCapabilityMatrix,
)
from decision_workbench.modeling.packages.contracts import (
    MAX_ARTIFACT_BYTES,
    ConformalIntervalCalibration,
    ConformalWrapperIdentity,
    PackageContractError,
    PackageModel,
    PredictionInterval,
    PredictiveSummary,
)
from decision_workbench.modeling.packages.ports import LoadedPredictor
from decision_workbench.modeling.packages.verification import VerifiedModelPackage


CONFORMAL_WRAPPER_SCHEMA_VERSION = "conformal-wrapper/v1"
_DIGEST = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
_SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _Contract(PackageModel):
    pass


class ConformalArtifact(_Contract):
    path: str
    sha256: _SHA256
    bytes: Annotated[int, Field(gt=0, le=MAX_ARTIFACT_BYTES)]
    media_type: Literal["application/json"] = "application/json"

    @field_validator("path")
    @classmethod
    def relative_json_path(cls, value: str) -> str:
        path = Path(value)
        if (
            not value
            or path.is_absolute()
            or ".." in path.parts
            or path.name != value.replace("\\", "/").split("/")[-1]
            or path.suffix != ".json"
        ):
            raise ValueError("conformal artifact path must be a package-relative JSON file")
        return value.replace("\\", "/")


class CalibrationScores(_Contract):
    schema_version: Literal["conformal-calibration-scores/v1"]
    scores: tuple[float, ...] = Field(min_length=1)

    @field_validator("scores")
    @classmethod
    def finite_nonnegative_scores(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        if any(not math.isfinite(item) or item < 0 for item in value):
            raise ValueError("conformal calibration scores must be finite and non-negative")
        return value


class FeaturePipelineIdentity(_Contract):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    spec_sha256: _SHA256


class CalibrationIdentity(_Contract):
    dataset_view_digest: _DIGEST
    training_snapshot_digest: _DIGEST
    split_policy_id: Literal["split-conformal/train-calibration/v1"]
    group_policy_id: str = Field(min_length=1)


class HeldOutQuality(_Contract):
    evaluation_dataset_digest: _DIGEST
    evaluation_split_policy_id: Literal["held-out-evaluation/v1"]
    sample_count: int = Field(ge=1)
    empirical_marginal_coverage: float = Field(ge=0, le=1)
    mean_interval_width: float = Field(ge=0)
    group_coverage: dict[str, float] = Field(default_factory=dict)
    small_calibration_warning: bool
    base_point_metric: dict[str, float] = Field(default_factory=dict)

    @field_validator("group_coverage")
    @classmethod
    def finite_coverages(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not key or not math.isfinite(item) or not 0 <= item <= 1 for key, item in value.items()):
            raise ValueError("group coverage must contain named finite proportions")
        return value

    @field_validator("base_point_metric")
    @classmethod
    def finite_point_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not key or not math.isfinite(item) for key, item in value.items()):
            raise ValueError("base point metrics must be named finite values")
        return value


@dataclass(frozen=True)
class EmpiricalIntervalMetrics:
    """Held-out diagnostics; group values are diagnostic, not guarantees."""

    empirical_marginal_coverage: float
    mean_interval_width: float
    group_coverage: dict[str, float]


def evaluate_split_conformal(
    point_estimates: Sequence[float],
    observations: Sequence[float],
    *,
    interval_radius: float,
    groups: Sequence[str] | None = None,
) -> EmpiricalIntervalMetrics:
    """Evaluate a fixed wrapper on held-out rows without refitting it."""
    if len(point_estimates) != len(observations) or not point_estimates:
        raise ValueError("held-out points and observations must have the same non-zero length")
    if not math.isfinite(interval_radius) or interval_radius < 0:
        raise ValueError("interval radius must be finite and non-negative")
    if groups is not None and len(groups) != len(observations):
        raise ValueError("held-out groups must match observation count")
    if any(not math.isfinite(item) for item in (*point_estimates, *observations)):
        raise ValueError("held-out points and observations must be finite")
    covered = [abs(point - observed) <= interval_radius for point, observed in zip(point_estimates, observations)]
    group_coverage: dict[str, float] = {}
    if groups is not None:
        if any(not group for group in groups):
            raise ValueError("held-out group labels must be non-empty")
        for group in sorted(set(groups)):
            indices = [index for index, item in enumerate(groups) if item == group]
            group_coverage[group] = sum(covered[index] for index in indices) / len(indices)
    return EmpiricalIntervalMetrics(
        empirical_marginal_coverage=sum(covered) / len(covered),
        mean_interval_width=2 * interval_radius,
        group_coverage=group_coverage,
    )

class ConformalWrapperManifest(_Contract):
    schema_version: Literal[CONFORMAL_WRAPPER_SCHEMA_VERSION]
    wrapper_id: str = Field(min_length=1)
    wrapper_version: str = Field(min_length=1)
    base_package_id: str = Field(min_length=1)
    base_package_manifest_digest: _DIGEST
    base_predictor_id: str = Field(min_length=1)
    target: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    feature_pipeline: FeaturePipelineIdentity
    calibration: CalibrationIdentity
    score_id: Literal["absolute_residual/v1"]
    finite_sample_rule: Literal["ceil_n_plus_1_over_coverage/v1"]
    alpha: float = Field(gt=0, lt=1)
    calibration_scores: ConformalArtifact
    calibration_score_count: int = Field(ge=1)
    quality: HeldOutQuality
    build_code_revision: str = Field(min_length=1)

    @model_validator(mode="after")
    def held_out_evaluation_is_not_calibration(self) -> "ConformalWrapperManifest":
        if self.quality.evaluation_dataset_digest == self.calibration.dataset_view_digest:
            raise ValueError("held-out evaluation dataset must differ from calibration dataset")
        return self


def _verified_artifact(root: Path, artifact: ConformalArtifact) -> bytes:
    try:
        path = (root / artifact.path).resolve(strict=True)
    except OSError as exc:
        raise PackageContractError(f"conformal artifact cannot be resolved: {artifact.path}") from exc
    if root not in path.parents or not path.is_file():
        raise PackageContractError(f"conformal artifact escapes wrapper root: {artifact.path}")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PackageContractError(f"conformal artifact cannot be read: {artifact.path}") from exc
    if len(payload) != artifact.bytes:
        raise PackageContractError("conformal calibration artifact size mismatch")
    if hashlib.sha256(payload).hexdigest() != artifact.sha256:
        raise PackageContractError("conformal calibration artifact hash mismatch")
    return payload


def _base_feature_pipeline_identity(package: VerifiedModelPackage) -> FeaturePipelineIdentity:
    pipeline = package.manifest.feature_pipeline
    if pipeline is None:
        raise PackageContractError("conformal wrapper requires a predictive base package")
    artifact = next((item for item in package.manifest.artifacts if item.path == pipeline.spec), None)
    if artifact is None:
        raise PackageContractError("base package feature pipeline is not a verified artifact")
    return FeaturePipelineIdentity(id=pipeline.id, version=pipeline.version, spec_sha256=artifact.sha256)


@dataclass(frozen=True)
class VerifiedConformalWrapper:
    manifest: ConformalWrapperManifest
    manifest_digest: str
    scores: tuple[float, ...]
    base_package: VerifiedModelPackage

    @property
    def coverage_level(self) -> float:
        return 1.0 - self.manifest.alpha

    def interval_radius(self) -> float:
        """Split-conformal order statistic with the declared finite-sample rule."""
        ordered = sorted(self.scores)
        rank = math.ceil((len(ordered) + 1) * self.coverage_level)
        return ordered[min(max(rank, 1), len(ordered)) - 1]

    def load_predictor(self) -> "ConformalPredictor":
        """Load only the predictor pinned and verified by this wrapper."""
        base = self.base_package.load_predictor(self.manifest.base_predictor_id)
        return ConformalPredictor(base, self)

    def apply_capability(
        self, matrix: ModelPackageCapabilityMatrix
    ) -> ModelPackageCapabilityMatrix:
        if (
            matrix.package_id != self.manifest.base_package_id
            or matrix.package_manifest_digest != self.manifest.base_package_manifest_digest
        ):
            raise PackageContractError("conformal wrapper does not match capability matrix package identity")
        target = matrix.target(self.manifest.target)
        if target is None:
            raise PackageContractError("conformal wrapper target is absent from capability matrix")
        if target.target_kind not in {"continuous", "continuous_positive"}:
            raise PackageContractError("split conformal wrapper only supports continuous targets")
        layer = CapabilityLayerIdentity(
            layer_id=self.manifest.wrapper_id,
            layer_version=self.manifest.wrapper_version,
            manifest_digest=f"sha256:{self.manifest_digest}",
        )
        return matrix.model_copy(update={
            "capability_layers": (*matrix.capability_layers, layer),
            "targets": tuple(
                item.model_copy(update={"conformal_interval": True})
                if item.target == self.manifest.target else item
                for item in matrix.targets
            )
        })


class ConformalPredictor:
    """Decorate a point predictor without changing its distribution semantics."""

    def __init__(self, base: LoadedPredictor, wrapper: VerifiedConformalWrapper) -> None:
        self._base = base
        self._wrapper = wrapper

    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        summary = self._base.predict(values, seed=seed)
        manifest = self._wrapper.manifest
        if summary.target != manifest.target or summary.unit != manifest.unit:
            raise PackageContractError("base predictor output does not match conformal wrapper target/unit")
        if summary.target_kind not in {"continuous", "continuous_positive"}:
            raise PackageContractError("split conformal wrapper only supports continuous target summaries")
        radius = self._wrapper.interval_radius()
        calibration = ConformalIntervalCalibration(
            calibration_dataset_digest=manifest.calibration.dataset_view_digest,
            calibration_sample_count=len(self._wrapper.scores),
            score_id=manifest.score_id,
            finite_sample_rule=manifest.finite_sample_rule,
        )
        return summary.model_copy(update={
            "prediction_interval": PredictionInterval(
                method="conformal",
                coverage_level=self._wrapper.coverage_level,
                lower=summary.point_estimate - radius,
                upper=summary.point_estimate + radius,
                calibration=calibration,
                conformal_wrapper=ConformalWrapperIdentity(
                    wrapper_id=manifest.wrapper_id,
                    wrapper_version=manifest.wrapper_version,
                    manifest_digest=f"sha256:{self._wrapper.manifest_digest}",
                    calibration_score_artifact_digest=(
                        f"sha256:{manifest.calibration_scores.sha256}"
                    ),
                ),
            )
        })


def verify_conformal_wrapper(
    wrapper_root: str | Path,
    *,
    base_package: VerifiedModelPackage,
) -> VerifiedConformalWrapper:
    """Verify wrapper data and bind it to one immutable base Package."""
    try:
        root = Path(wrapper_root).resolve(strict=True)
        manifest_bytes = (root / "manifest.json").read_bytes()
        manifest = ConformalWrapperManifest.model_validate_json(manifest_bytes)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise PackageContractError(f"invalid conformal wrapper manifest: {exc}") from exc
    if not root.is_dir():
        raise PackageContractError("conformal wrapper root must be a directory")
    if manifest.base_package_id != base_package.manifest.package_id:
        raise PackageContractError("conformal wrapper base package id mismatch")
    if manifest.base_package_manifest_digest != f"sha256:{base_package.manifest_sha256}":
        raise PackageContractError("conformal wrapper base package digest mismatch")
    if manifest.feature_pipeline != _base_feature_pipeline_identity(base_package):
        raise PackageContractError("conformal wrapper feature pipeline identity mismatch")
    predictor = next(
        (
            item
            for item in base_package.manifest.predictors
            if item.id == manifest.base_predictor_id
        ),
        None,
    )
    if predictor is None or predictor.unit != manifest.unit:
        raise PackageContractError("conformal wrapper predictor/unit does not match base package")
    if predictor.target != manifest.target:
        raise PackageContractError("conformal wrapper predictor target does not match manifest")
    if predictor.target_kind not in {"continuous", "continuous_positive"}:
        raise PackageContractError("split conformal wrapper only supports continuous base predictors")
    payload = _verified_artifact(root, manifest.calibration_scores)
    try:
        scores = CalibrationScores.model_validate_json(payload).scores
    except ValueError as exc:
        raise PackageContractError(f"invalid conformal calibration scores: {exc}") from exc
    if len(scores) != manifest.calibration_score_count:
        raise PackageContractError("conformal calibration score shape mismatch")
    return VerifiedConformalWrapper(
        manifest=manifest,
        manifest_digest=hashlib.sha256(manifest_bytes).hexdigest(),
        scores=scores,
        base_package=base_package,
    )
