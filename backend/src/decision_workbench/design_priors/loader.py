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
    DesignPriorQualityReport,
)


@dataclass(frozen=True)
class VerifiedDesignPriorPackage:
    root: Path
    manifest: DesignPriorManifest
    manifest_sha256: str
    observations_by_artifact: dict[str, DesignPriorObservations]
    quality_report: DesignPriorQualityReport

    def observations_for(self, generator_id: str) -> DesignPriorObservations:
        generator = next((item for item in self.manifest.generators if item.generator_id == generator_id), None)
        if generator is None:
            raise DesignPriorPackageError(f"unregistered Design Prior generator: {generator_id}")
        return self.observations_by_artifact[generator.observations_artifact]


class DesignPriorPackageLoader:
    def __init__(self, *, max_package_bytes: int = MAX_DESIGN_PRIOR_PACKAGE_BYTES) -> None:
        self.max_package_bytes = max_package_bytes

    def load(self, package_root: str | Path) -> VerifiedDesignPriorPackage:
        unresolved_root = Path(package_root).absolute()
        if unresolved_root.is_symlink():
            raise DesignPriorPackageError("unsafe Design Prior Package symlink root")
        root = unresolved_root.resolve()
        manifest_path = root / "manifest.json"
        if not root.is_dir() or not manifest_path.is_file():
            raise DesignPriorPackageError("Design Prior Packageにmanifest.jsonがありません")
        package_entries = tuple(root.rglob("*"))
        symlinks = [entry for entry in package_entries if entry.is_symlink()]
        if symlinks:
            relative = symlinks[0].relative_to(root).as_posix()
            raise DesignPriorPackageError(f"unsafe Design Prior Package symlink: {relative}")
        manifest_bytes = manifest_path.read_bytes()
        try:
            manifest = DesignPriorManifest.model_validate(json.loads(manifest_bytes))
        except (json.JSONDecodeError, ValueError) as exc:
            raise DesignPriorPackageError(f"Design Prior manifestが不正です: {exc}") from exc
        declared_files = {"manifest.json", *(artifact.path for artifact in manifest.artifacts)}
        actual_files = {
            entry.relative_to(root).as_posix()
            for entry in package_entries
            if entry.is_file()
        }
        if actual_files != declared_files:
            undeclared = sorted(actual_files - declared_files)
            missing = sorted(declared_files - actual_files)
            detail = (
                f"undeclared={undeclared}" if undeclared else f"missing={missing}"
            )
            raise DesignPriorPackageError(
                f"unsafe Design Prior Package file inventory: {detail}"
            )
        artifact_total = 0
        observations: dict[str, DesignPriorObservations] = {}
        quality_report: DesignPriorQualityReport | None = None
        observation_artifacts = {
            item.observations_artifact for item in manifest.generators
        }
        for artifact in manifest.artifacts:
            unresolved_target = root / artifact.path
            if unresolved_target.is_symlink():
                raise DesignPriorPackageError(
                    f"unsafe Design Prior artifact symlink: {artifact.path}"
                )
            target = unresolved_target.resolve()
            if root not in target.parents or not target.is_file():
                raise DesignPriorPackageError(f"unsafe Design Prior artifact path: {artifact.path}")
            payload = target.read_bytes()
            if len(payload) != artifact.bytes:
                raise DesignPriorPackageError(f"Design Prior artifact size mismatch: {artifact.path}")
            if hashlib.sha256(payload).hexdigest() != artifact.sha256:
                raise DesignPriorPackageError(f"Design Prior artifact hash mismatch: {artifact.path}")
            artifact_total += len(payload)
            if artifact_total + len(manifest_bytes) > self.max_package_bytes:
                raise DesignPriorPackageError("Design Prior Package exceeds its size limit")
            try:
                decoded = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise DesignPriorPackageError(
                    f"Design Prior artifact must be JSON: {artifact.path}: {exc}"
                ) from exc
            if artifact.path in observation_artifacts:
                try:
                    parsed = DesignPriorObservations.model_validate(decoded)
                except ValueError as exc:
                    raise DesignPriorPackageError(f"Design Prior observationsが不正です: {artifact.path}: {exc}") from exc
                allowed = set(manifest.canonical_input_paths)
                for row in parsed.rows:
                    if set(row.inputs) != allowed:
                        raise DesignPriorPackageError("Design Prior row input paths must exactly match the manifest")
                observations[artifact.path] = parsed
            if artifact.path == manifest.quality_report:
                try:
                    quality_report = DesignPriorQualityReport.model_validate(decoded)
                except ValueError as exc:
                    raise DesignPriorPackageError(
                        f"Design Prior quality reportが不正です: {artifact.path}: {exc}"
                    ) from exc
        if quality_report is None:
            raise DesignPriorPackageError("Design Prior quality reportがありません")
        expected_numeric_paths: tuple[str, ...] | None = None
        for parsed in observations.values():
            numeric_paths: list[str] = []
            for path in manifest.canonical_input_paths:
                values = [row.inputs[path] for row in parsed.rows]
                if all(
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    for value in values
                ):
                    numeric_paths.append(path)
                elif not all(isinstance(value, str) for value in values):
                    raise DesignPriorPackageError(
                        "Design Prior input path must be uniformly numeric or categorical: "
                        f"{path}"
                    )
            artifact_numeric_paths = tuple(numeric_paths)
            if (
                expected_numeric_paths is not None
                and artifact_numeric_paths != expected_numeric_paths
            ):
                raise DesignPriorPackageError(
                    "Design Prior observation artifacts disagree on input roles"
                )
            expected_numeric_paths = artifact_numeric_paths
        if (
            tuple(quality_report.canonical_input_paths)
            != tuple(manifest.canonical_input_paths)
            or tuple(quality_report.numeric_paths) != expected_numeric_paths
            or any(
                quality_report.rows != len(item.rows)
                for item in observations.values()
            )
        ):
            raise DesignPriorPackageError(
                "Design Prior quality reportがmanifestまたはobservationsと一致しません"
            )
        return VerifiedDesignPriorPackage(
            root=root,
            manifest=manifest,
            manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
            observations_by_artifact=observations,
            quality_report=quality_report,
        )
