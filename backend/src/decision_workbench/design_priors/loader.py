"""Verification-first loader for Design Prior Packages."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from decision_workbench.design_priors.contracts import (
    MAX_DESIGN_PRIOR_PACKAGE_BYTES,
    DesignPriorManifest,
    DesignPriorObservations,
    DesignPriorPackageError,
)


@dataclass(frozen=True)
class VerifiedDesignPriorPackage:
    root: Path
    manifest: DesignPriorManifest
    manifest_sha256: str
    observations_by_artifact: dict[str, DesignPriorObservations]

    def observations_for(self, generator_id: str) -> DesignPriorObservations:
        generator = next((item for item in self.manifest.generators if item.generator_id == generator_id), None)
        if generator is None:
            raise DesignPriorPackageError(f"unregistered Design Prior generator: {generator_id}")
        return self.observations_by_artifact[generator.observations_artifact]


class DesignPriorPackageLoader:
    def __init__(self, *, max_package_bytes: int = MAX_DESIGN_PRIOR_PACKAGE_BYTES) -> None:
        self.max_package_bytes = max_package_bytes

    def load(self, package_root: str | Path) -> VerifiedDesignPriorPackage:
        root = Path(package_root).resolve()
        manifest_path = root / "manifest.json"
        if not root.is_dir() or not manifest_path.is_file():
            raise DesignPriorPackageError("Design Prior Packageにmanifest.jsonがありません")
        manifest_bytes = manifest_path.read_bytes()
        try:
            manifest = DesignPriorManifest.model_validate(json.loads(manifest_bytes))
        except (json.JSONDecodeError, ValueError) as exc:
            raise DesignPriorPackageError(f"Design Prior manifestが不正です: {exc}") from exc
        artifact_total = 0
        observations: dict[str, DesignPriorObservations] = {}
        for artifact in manifest.artifacts:
            target = (root / artifact.path).resolve()
            if root not in target.parents or not target.is_file() or target.is_symlink():
                raise DesignPriorPackageError(f"unsafe Design Prior artifact path: {artifact.path}")
            payload = target.read_bytes()
            if len(payload) != artifact.bytes:
                raise DesignPriorPackageError(f"Design Prior artifact size mismatch: {artifact.path}")
            if hashlib.sha256(payload).hexdigest() != artifact.sha256:
                raise DesignPriorPackageError(f"Design Prior artifact hash mismatch: {artifact.path}")
            artifact_total += len(payload)
            if artifact_total + len(manifest_bytes) > self.max_package_bytes:
                raise DesignPriorPackageError("Design Prior Package exceeds its size limit")
            if artifact.path in {item.observations_artifact for item in manifest.generators}:
                try:
                    parsed = DesignPriorObservations.model_validate(json.loads(payload))
                except (json.JSONDecodeError, ValueError) as exc:
                    raise DesignPriorPackageError(f"Design Prior observationsが不正です: {artifact.path}: {exc}") from exc
                allowed = set(manifest.canonical_input_paths)
                for row in parsed.rows:
                    if set(row.inputs) != allowed:
                        raise DesignPriorPackageError("Design Prior row input paths must exactly match the manifest")
                observations[artifact.path] = parsed
        return VerifiedDesignPriorPackage(
            root=root,
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            observations_by_artifact=observations,
        )
