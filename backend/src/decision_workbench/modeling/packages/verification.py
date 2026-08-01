"""Verify and snapshot data-only model packages before adapter execution."""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from decision_workbench.modeling.packages.contracts import (
    MAX_ARTIFACT_BYTES,
    MAX_PACKAGE_ARTIFACTS,
    MAX_PACKAGE_BYTES,
    SNAPSHOT_CHUNK_BYTES,
    FeaturePipelineDocument,
    ModelPackageManifest,
    PackageContractError,
)
from decision_workbench.modeling.packages.ports import (
    LoadedDeterministicTransform,
    LoadedPredictor,
)
from decision_workbench.modeling.packages.registry import AdapterRegistry


@dataclass(frozen=True)
class VerifiedModelPackage:
    """An immutable, byte-verified package snapshot available to adapters."""

    root: Path
    manifest: ModelPackageManifest
    artifacts: dict[str, Path]
    registry: AdapterRegistry
    _manifest_sha256: str
    _snapshot: Any

    def artifact_path(self, relative_path: str) -> Path:
        try:
            return self.artifacts[relative_path]
        except KeyError as exc:
            raise PackageContractError(
                f"artifact was not verified: {relative_path}"
            ) from exc

    def load_predictor(self, predictor_id: str) -> LoadedPredictor:
        spec = next(
            (item for item in self.manifest.predictors if item.id == predictor_id),
            None,
        )
        if spec is None:
            raise PackageContractError(f"unknown predictor id: {predictor_id}")
        return self.registry.adapter_for(spec.runtime_type).load(self, spec)

    def load_transform(self, transform_id: str) -> LoadedDeterministicTransform:
        spec = next(
            (
                item
                for item in self.manifest.deterministic_transforms
                if item.id == transform_id
            ),
            None,
        )
        if spec is None:
            raise PackageContractError(
                f"unknown deterministic transform id: {transform_id}"
            )
        adapter = self.registry.adapter_for(spec.runtime_type)
        load_transform = getattr(adapter, "load_transform", None)
        if load_transform is None:
            raise PackageContractError(
                "runtime does not implement deterministic transforms: "
                f"{spec.runtime_type}"
            )
        return load_transform(self, spec)

    @property
    def manifest_sha256(self) -> str:
        return self._manifest_sha256


def verify_model_package(
    package_root: str | Path,
    *,
    registry: AdapterRegistry,
    max_artifact_bytes: int = MAX_ARTIFACT_BYTES,
    max_package_bytes: int = MAX_PACKAGE_BYTES,
) -> VerifiedModelPackage:
    """Return a snapshot only after paths, byte limits, and hashes all match."""

    try:
        root = Path(package_root).resolve(strict=True)
    except OSError as exc:
        raise PackageContractError(
            f"model package root cannot be resolved: {exc}"
        ) from exc
    if not root.is_dir():
        raise PackageContractError("model package root must be a directory")
    try:
        manifest_bytes = (root / "manifest.json").read_bytes()
        manifest = ModelPackageManifest.model_validate(
            json.loads(manifest_bytes.decode("utf-8"))
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise PackageContractError(f"invalid model package manifest: {exc}") from exc
    if len(manifest.artifacts) > MAX_PACKAGE_ARTIFACTS:
        raise PackageContractError(
            f"model package has too many artifacts: {len(manifest.artifacts)}"
        )
    declared_package_bytes = sum(spec.bytes for spec in manifest.artifacts)
    if declared_package_bytes > max_package_bytes:
        raise PackageContractError(
            "model package artifacts exceed aggregate byte limit: "
            f"{declared_package_bytes}"
        )

    snapshot = tempfile.TemporaryDirectory(prefix="material-workbench-package-")
    snapshot_root = Path(snapshot.name)
    artifacts: dict[str, Path] = {}
    snapshot_bytes = 0
    try:
        for spec in manifest.artifacts:
            try:
                candidate = (root / spec.path).resolve(strict=True)
            except OSError as exc:
                raise PackageContractError(
                    f"artifact cannot be resolved: {spec.path}"
                ) from exc
            if root not in candidate.parents:
                raise PackageContractError(f"artifact escapes package root: {spec.path}")
            if not candidate.is_file():
                raise PackageContractError(
                    f"artifact is not a regular file: {spec.path}"
                )
            if spec.bytes > max_artifact_bytes:
                raise PackageContractError(f"artifact size mismatch: {spec.path}")
            snapshot_path = snapshot_root / spec.path
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            artifact_bytes = 0
            try:
                with candidate.open("rb") as source, snapshot_path.open("xb") as target:
                    for chunk in iter(
                        lambda: source.read(SNAPSHOT_CHUNK_BYTES), b""
                    ):
                        artifact_bytes += len(chunk)
                        snapshot_bytes += len(chunk)
                        if (
                            artifact_bytes > max_artifact_bytes
                            or snapshot_bytes > max_package_bytes
                        ):
                            raise PackageContractError(
                                "model package artifact byte limit exceeded: "
                                f"{spec.path}"
                            )
                        digest.update(chunk)
                        target.write(chunk)
            except OSError as exc:
                raise PackageContractError(
                    f"artifact snapshot I/O failed: {spec.path}: {exc}"
                ) from exc
            if artifact_bytes != spec.bytes:
                raise PackageContractError(f"artifact size mismatch: {spec.path}")
            if digest.hexdigest() != spec.sha256:
                raise PackageContractError(f"artifact hash mismatch: {spec.path}")
            artifacts[spec.path] = snapshot_path
        _validate_feature_pipeline(manifest, artifacts)
    except Exception:
        snapshot.cleanup()
        raise

    return VerifiedModelPackage(
        root=root,
        manifest=manifest,
        artifacts=artifacts,
        registry=registry,
        _manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
        _snapshot=snapshot,
    )


def _validate_feature_pipeline(
    manifest: ModelPackageManifest,
    artifacts: dict[str, Path],
) -> None:
    if manifest.feature_pipeline is None:
        return
    try:
        pipeline = FeaturePipelineDocument.model_validate(
            json.loads(
                artifacts[manifest.feature_pipeline.spec].read_text(encoding="utf-8")
            )
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PackageContractError(
            f"invalid feature pipeline specification: {exc}"
        ) from exc
    expected = manifest.feature_pipeline
    if (pipeline.id, pipeline.version) != (expected.id, expected.version):
        raise PackageContractError(
            "feature pipeline id/version differs between manifest and specification"
        )
    if pipeline.canonical_input_paths != expected.canonical_input_paths:
        raise PackageContractError(
            "canonical input paths differ between model package manifest and "
            "pipeline specification"
        )
    if tuple(feature.name for feature in pipeline.features) != expected.output_features:
        raise PackageContractError(
            "pipeline output feature order differs from model package manifest "
            "output_features"
        )
    recipe_ref = pipeline.feature_recipe
    if recipe_ref is None:
        return
    if {recipe_ref.recipe, recipe_ref.state} - set(expected.artifacts):
        raise PackageContractError(
            "feature recipe and state must be declared pipeline artifacts"
        )
    try:
        from decision_workbench.modeling.training.feature_recipe import (
            load_feature_recipe_artifacts,
            recipe_digest,
            validate_recipe_canonical_inputs,
        )

        recipe, state = load_feature_recipe_artifacts(
            artifacts[recipe_ref.recipe],
            artifacts[recipe_ref.state],
        )
    except (KeyError, OSError, ValueError) as exc:
        raise PackageContractError(f"invalid feature recipe artifacts: {exc}") from exc
    if (
        recipe_ref.recipe_digest != state.recipe_digest
        or recipe_ref.recipe_digest != recipe_digest(recipe)
        or recipe_ref.state_digest != state.state_digest
    ):
        raise PackageContractError(
            "feature recipe artifact digests differ from pipeline specification"
        )
    if tuple(feature.name for feature in recipe.features) != expected.output_features:
        raise PackageContractError(
            "feature recipe output order differs from model package manifest"
        )
    try:
        validate_recipe_canonical_inputs(
            recipe,
            expected.canonical_input_paths,
        )
    except ValueError as exc:
        raise PackageContractError(str(exc)) from exc
