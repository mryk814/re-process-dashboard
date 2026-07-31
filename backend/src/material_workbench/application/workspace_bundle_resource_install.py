from __future__ import annotations

import os
import shutil
from pathlib import Path, PurePosixPath
from typing import Callable
from uuid import uuid4
from material_workbench.contracts.workspace_bundle_contracts import (
    WorkspaceBundleFile,
    WorkspaceBundleManifest,
    WorkspaceBundleResource,
)
from material_workbench.modeling.model_package_verification import ModelPackageLoader
from material_workbench.persistence.sqlite_connection import (
    connect_sqlite,
    validate_sqlite_foreign_keys,
)
from material_workbench.persistence.row_payload_store import (
    RowPayloadStore,
)
from material_workbench.application.workspace_bundle_shared import (
    WorkspaceBundleError,
    _file_digest,
)
from material_workbench.application.workspace_bundle_manifest import (
    _resource_bundle_digest,
    _row_payload_references,
)
from material_workbench.application.workspace_bundle_restore_plan import (
    _final_resource_path,
    _staged_resource_root,
    _write_state,
)


def _validate_resource_tree(
    root: Path,
    resource: WorkspaceBundleResource,
    records: dict[str, WorkspaceBundleFile],
) -> None:
    expected_relative: set[str] = set()
    verified: list[WorkspaceBundleFile] = []
    bundle_root = PurePosixPath(resource.bundle_root)
    for archive_path in resource.files:
        record = records[archive_path]
        relative = PurePosixPath(archive_path).relative_to(bundle_root)
        relative_text = relative.as_posix()
        expected_relative.add(relative_text)
        target = root / Path(relative_text)
        if not target.is_file() or target.is_symlink():
            raise WorkspaceBundleError(
                f"Installed Workspace resource file is missing: {archive_path}"
            )
        if (
            target.stat().st_size != record.size_bytes
            or _file_digest(target) != record.sha256
        ):
            raise WorkspaceBundleError(
                f"Installed Workspace resource file changed: {archive_path}"
            )
        verified.append(record)
    actual_relative = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    if actual_relative != expected_relative:
        raise WorkspaceBundleError(
            f"Installed Workspace resource tree has undeclared files: "
            f"{resource.reference_id}"
        )
    if _resource_bundle_digest(tuple(verified)) != resource.bundle_digest:
        raise WorkspaceBundleError(
            f"Installed Workspace resource inventory digest mismatch: "
            f"{resource.reference_id}"
        )


def _install_resources(
    *,
    database: Path,
    data_library_root: Path,
    root: Path,
    manifest: WorkspaceBundleManifest,
) -> tuple[str, ...]:
    staged_database = root / "next" / Path(manifest.database.path)
    connection = connect_sqlite(staged_database)
    installed: list[str] = []
    records = {record.path: record for record in manifest.data_library_files}
    try:
        connection.execute("BEGIN IMMEDIATE")
        for resource in manifest.bundled_resources:
            source_root = _staged_resource_root(root / "next", resource)
            final_root = _final_resource_path(data_library_root, resource)
            if not final_root.exists():
                final_root.parent.mkdir(parents=True, exist_ok=True)
                temporary = final_root.with_name(
                    f".{final_root.name}.{uuid4().hex}.tmp"
                )
                temporary.mkdir()
                try:
                    bundle_root = PurePosixPath(resource.bundle_root)
                    for archive_path in resource.files:
                        relative = PurePosixPath(archive_path).relative_to(bundle_root)
                        source = source_root / Path(relative.as_posix())
                        destination = temporary / Path(relative.as_posix())
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(source, destination)
                    _validate_resource_tree(temporary, resource, records)
                    os.replace(temporary, final_root)
                    installed.append(
                        final_root.relative_to(data_library_root).as_posix()
                    )
                finally:
                    shutil.rmtree(temporary, ignore_errors=True)
            _validate_resource_tree(final_root, resource, records)
            if resource.kind == "data_asset":
                if resource.primary_file is None:
                    raise WorkspaceBundleError(
                        f"Data Asset primary file is missing: {resource.reference_id}"
                    )
                relative_file = PurePosixPath(resource.primary_file).relative_to(
                    PurePosixPath(resource.bundle_root)
                )
                locator = final_root / Path(relative_file.as_posix())
                if _file_digest(locator) != resource.content_digest.removeprefix(
                    "sha256:"
                ):
                    raise WorkspaceBundleError(
                        f"Installed Data Asset digest mismatch: {resource.reference_id}"
                    )
                connection.execute(
                    "UPDATE data_assets SET locator_kind='managed',locator=? WHERE id=?",
                    (str(locator.resolve()), resource.reference_id),
                )
            else:
                verified = ModelPackageLoader().load(final_root)
                if verified.manifest_sha256 != resource.content_digest.removeprefix(
                    "sha256:"
                ):
                    raise WorkspaceBundleError(
                        f"Installed Model Package digest mismatch: {resource.reference_id}"
                    )
                connection.execute(
                    "UPDATE model_package_refs SET locator=? WHERE id=?",
                    (str(final_root.resolve()), resource.reference_id),
                )
        connection.commit()
    finally:
        connection.close()
    validate_sqlite_foreign_keys(staged_database)
    return tuple(installed)


def _install_row_payloads(
    *,
    database: Path,
    staged_database: Path,
    restore_root: Path,
    state: dict[str, object],
    fault_injector: Callable[[str], None] | None = None,
) -> tuple[str, ...]:
    source_store = RowPayloadStore(staged_database)
    destination_store = RowPayloadStore(database)
    installed: list[str] = []

    def install_file(source: Path, destination: Path, digest: str) -> None:
        if destination.exists():
            if _file_digest(destination) != digest:
                raise WorkspaceBundleError(
                    "Installed lifecycle row payload digest mismatch"
                )
            return
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temporary)
            if _file_digest(temporary) != digest:
                raise WorkspaceBundleError(
                    "Installed lifecycle row payload digest mismatch"
                )
            with temporary.open("r+b") as copied:
                os.fsync(copied.fileno())
            os.replace(temporary, destination)
            installed.append(destination.relative_to(database.parent).as_posix())
            state["installed_row_payload_files"] = list(installed)
            _write_state(restore_root, state)
            if fault_injector is not None:
                fault_injector("after_row_payload_installed")
        finally:
            temporary.unlink(missing_ok=True)

    try:
        for reference in _row_payload_references(staged_database):
            source_store.verify(reference)
            source = source_store.path_for(reference)
            destination = destination_store.path_for(reference)
            install_file(source, destination, reference.sha256)
            destination_store.verify(reference)
        quarantine_root = staged_database.parent / "row-payloads" / "quarantine"
        if quarantine_root.exists():
            destination_root = database.parent / "row-payloads" / "quarantine"
            for source in sorted(quarantine_root.rglob("*.json")):
                digest = _file_digest(source)
                if source.stem != digest:
                    raise WorkspaceBundleError(
                        "Lifecycle payload quarantine digest does not match its name"
                    )
                destination = destination_root / source.relative_to(quarantine_root)
                install_file(source, destination, digest)
    except Exception:
        _cleanup_installed_row_payloads(database, installed)
        state["installed_row_payload_files"] = []
        _write_state(restore_root, state)
        raise
    return tuple(installed)


def _cleanup_installed_row_payloads(
    database: Path,
    relative_files: object,
) -> None:
    if not isinstance(relative_files, list | tuple):
        return
    root = (database.parent / "row-payloads").resolve()
    for raw in relative_files:
        if not isinstance(raw, str):
            continue
        candidate = (database.parent / raw).resolve()
        if candidate != root and root not in candidate.parents:
            continue
        candidate.unlink(missing_ok=True)
        parent = candidate.parent
        while parent != root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent


def _cleanup_installed_resources(
    data_library_root: Path,
    relative_roots: object,
) -> None:
    if not isinstance(relative_roots, list | tuple):
        return
    root = data_library_root.resolve()
    for raw in relative_roots:
        if not isinstance(raw, str):
            continue
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        target = (root / Path(relative.as_posix())).resolve()
        expected_parent = root / "by-digest"
        if expected_parent not in target.parents:
            continue
        shutil.rmtree(target, ignore_errors=True)
        parent = target.parent
        while parent != root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
