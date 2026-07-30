from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import uuid4
from openpyxl import load_workbook
from material_workbench.data.evidence_images import (
    EvidenceImageError,
    resolve_evidence_image,
)
from material_workbench.contracts.chain_contracts import (
    semantic_digest,
    task_contract_surface,
    validate_chain_revision,
)
from material_workbench.contracts.workspace_bundle_contracts import (
    WorkspaceBackupResult,
    WorkspaceBundleDiagnostic,
    WorkspaceBundleFile,
    WorkspaceBundleManifest,
    WorkspaceBundleMigration,
    WorkspaceBundlePackageReference,
    WorkspaceBundleResource,
    WorkspaceRestoreCommitResult,
    WorkspaceRestorePrepared,
    WorkspaceRestoreResolution,
    WorkspaceTableEvidence,
)
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalog,
)
from material_workbench.persistence.sqlite_connection import (
    connect_sqlite,
    validate_sqlite_foreign_keys,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.row_payload_store import (
    RowPayloadError,
    RowPayloadReference,
    RowPayloadStore,
)
from material_workbench.persistence.data_lifecycle_payload_storage import (
    QuarantinedPayloadReference,
    StoredLifecycleRowResource,
    hydrate_curation_run,
    hydrate_raw_snapshot,
)
from material_workbench.contracts.data_lifecycle_contracts import (
    CurationRun,
    RawSourceSnapshot,
)
from material_workbench.persistence.welding_chain_bootstrap import (
    welding_stage_a_surface,
)
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.application.project_runtime import ProjectRuntimeResolver
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.application.workspace_bundle_shared import (
    DATABASE_ARCHIVE_PATH, LIFECYCLE_ROW_TABLES, MANIFEST_ARCHIVE_PATH,
    MAX_BUNDLE_BYTES, MAX_BUNDLE_ENTRIES, MAX_COMPRESSION_RATIO, MAX_ENTRY_BYTES,
    MAX_MANIFEST_BYTES, MIN_FREE_SPACE_RESERVE, RESOURCE_ARCHIVE_ROOT,
    RESTORE_EXPIRY_HOURS, ROW_PAYLOAD_ARCHIVE_ROOT, WINDOWS_RESERVED_NAMES,
    WorkspaceBundleError, _canonical_digest, _file_digest, _json_value,
)
from material_workbench.application.workspace_bundle_manifest import _quarantined_payload_references, _resource_bundle_digest, _row_payload_references

def _validate_archive_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "\\" in name
        or re.match(r"^[A-Za-z]:", name)
        or ":" in name
    ):
        raise WorkspaceBundleError(f"Unsafe Workspace bundle entry: {name}")
    for part in path.parts:
        stem = part.rstrip(" .").split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise WorkspaceBundleError(
                f"Reserved Windows path in Workspace bundle: {name}"
            )


def _inspect_bundle(source: Path) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    try:
        bundle = zipfile.ZipFile(source, mode="r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise WorkspaceBundleError(f"Workspace bundle cannot be opened: {exc}") from exc
    entries = bundle.infolist()
    names = [entry.filename for entry in entries]
    try:
        if len(entries) > MAX_BUNDLE_ENTRIES:
            raise WorkspaceBundleError("Workspace bundle has too many entries")
        if len(names) != len(set(names)):
            raise WorkspaceBundleError("Workspace bundle contains duplicate entries")
        if sum(entry.file_size for entry in entries) > MAX_BUNDLE_BYTES:
            raise WorkspaceBundleError("Workspace bundle is too large")
        for entry in entries:
            _validate_archive_name(entry.filename)
            if entry.file_size > MAX_ENTRY_BYTES:
                raise WorkspaceBundleError(
                    f"Workspace bundle entry is too large: {entry.filename}"
                )
            unix_mode = entry.external_attr >> 16
            if stat.S_ISLNK(unix_mode):
                raise WorkspaceBundleError(
                    f"Workspace bundle symlink is not allowed: {entry.filename}"
                )
            if (
                entry.file_size
                and entry.compress_size
                and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO
            ):
                raise WorkspaceBundleError(
                    f"Workspace bundle compression ratio is unsafe: {entry.filename}"
                )
        manifest = next(
            (entry for entry in entries if entry.filename == MANIFEST_ARCHIVE_PATH),
            None,
        )
        if manifest is None or manifest.file_size > MAX_MANIFEST_BYTES:
            raise WorkspaceBundleError(
                "Workspace bundle manifest is missing or too large"
            )
        return bundle, {entry.filename: entry for entry in entries}
    except Exception:
        bundle.close()
        raise


def _stream_extract(
    bundle: zipfile.ZipFile,
    entries: dict[str, zipfile.ZipInfo],
    manifest: WorkspaceBundleManifest,
    destination: Path,
) -> None:
    records = (
        manifest.database,
        *manifest.data_library_files,
        *manifest.row_payload_files,
    )
    expanded_bytes = sum(record.size_bytes for record in records)
    required_free = expanded_bytes + max(
        MIN_FREE_SPACE_RESERVE,
        expanded_bytes // 10,
    )
    available_free = shutil.disk_usage(destination).free
    if available_free < required_free:
        raise WorkspaceBundleError(
            "Workspace bundleの展開に必要な空き容量がありません"
        )
    expected = {MANIFEST_ARCHIVE_PATH, *(record.path for record in records)}
    if set(entries) != expected:
        raise WorkspaceBundleError(
            "Workspace bundle entries do not match the manifest"
        )
    for record in records:
        entry = entries.get(record.path)
        if entry is None or entry.file_size != record.size_bytes:
            raise WorkspaceBundleError(
                f"Workspace bundle size mismatch: {record.path}"
            )
        target = destination / Path(record.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        digest = sha256()
        written = 0
        with bundle.open(entry, mode="r") as source, target.open("xb") as output:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > record.size_bytes:
                    raise WorkspaceBundleError(
                        f"Workspace bundle entry expanded beyond manifest: {record.path}"
                    )
                digest.update(chunk)
                output.write(chunk)
        if written != record.size_bytes or digest.hexdigest() != record.sha256:
            raise WorkspaceBundleError(
                f"Workspace bundle digest mismatch: {record.path}"
            )


def _validate_resource_manifest(manifest: WorkspaceBundleManifest) -> None:
    records = {record.path: record for record in manifest.data_library_files}
    covered: set[str] = set()
    references: set[tuple[str, str]] = set()
    for resource in manifest.bundled_resources:
        reference = (resource.kind, resource.reference_id)
        if reference in references:
            raise WorkspaceBundleError(
                f"Workspace bundle has duplicate resource reference: {reference}"
            )
        references.add(reference)
        if not resource.files:
            raise WorkspaceBundleError(
                f"Workspace bundle resource has no files: {resource.reference_id}"
            )
        root = PurePosixPath(resource.bundle_root)
        selected: list[WorkspaceBundleFile] = []
        for path in resource.files:
            candidate = PurePosixPath(path)
            try:
                candidate.relative_to(root)
            except ValueError as exc:
                raise WorkspaceBundleError(
                    f"Workspace resource file escapes its root: {path}"
                ) from exc
            record = records.get(path)
            if record is None:
                raise WorkspaceBundleError(
                    f"Workspace resource file is not indexed: {path}"
                )
            selected.append(record)
            covered.add(path)
        if resource.kind == "data_asset":
            if resource.primary_file is None or resource.primary_file not in resource.files:
                raise WorkspaceBundleError(
                    f"Data Asset primary file is invalid: {resource.reference_id}"
                )
        elif resource.primary_file is not None:
            raise WorkspaceBundleError(
                f"Model Package cannot declare a primary file: {resource.reference_id}"
            )
        if _resource_bundle_digest(tuple(selected)) != resource.bundle_digest:
            raise WorkspaceBundleError(
                f"Workspace resource inventory digest mismatch: {resource.reference_id}"
            )
    if covered != set(records):
        raise WorkspaceBundleError(
            "Workspace bundle contains resource files with no resource owner"
        )


def _extract_verified_bundle(
    source: Path,
    destination: Path,
) -> tuple[WorkspaceBundleManifest, str]:
    bundle, entries = _inspect_bundle(source)
    try:
        try:
            raw_manifest = bundle.read(MANIFEST_ARCHIVE_PATH)
            manifest = WorkspaceBundleManifest.model_validate_json(raw_manifest)
        except (KeyError, ValueError) as exc:
            raise WorkspaceBundleError(
                f"Unsupported or invalid Workspace bundle manifest: {exc}"
            ) from exc
        _validate_resource_manifest(manifest)
        _stream_extract(bundle, entries, manifest, destination)
        (destination / MANIFEST_ARCHIVE_PATH).write_bytes(raw_manifest)
        return manifest, sha256(raw_manifest).hexdigest()
    finally:
        bundle.close()


def _row_payload_archive_path(reference: RowPayloadReference) -> str:
    return (
        f"{ROW_PAYLOAD_ARCHIVE_ROOT}/sha256/"
        f"{reference.sha256[:2]}/{reference.sha256}.jsonl"
    )


def _validate_staged_row_payloads(
    staged_database: Path,
    manifest: WorkspaceBundleManifest,
) -> None:
    references = _row_payload_references(staged_database)
    store = RowPayloadStore(staged_database)
    for reference in references:
        store.verify(reference)
    if (
        manifest.schema_version == "workspace-bundle/v2"
        or manifest.row_payload_files
    ):
        expected = {_row_payload_archive_path(reference) for reference in references}
        for reference in _quarantined_payload_references(staged_database):
            path = staged_database.parent / Path(reference.path)
            if (
                not path.is_file()
                or path.is_symlink()
                or path.stat().st_size != reference.size_bytes
                or _file_digest(path) != reference.sha256
            ):
                raise WorkspaceBundleError(
                    "Lifecycle payload quarantine is unavailable: "
                    f"{reference.path}"
                )
            expected.add(f"workspace/{reference.path}")
        declared = {record.path for record in manifest.row_payload_files}
        if declared != expected:
            raise WorkspaceBundleError(
                "Workspace lifecycle row payload inventory does not match its database"
            )
