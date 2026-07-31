from __future__ import annotations

import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4
from decision_workbench.contracts.workspace_bundle_contracts import (
    WorkspaceBackupResult,
    WorkspaceBundleDiagnostic,
    WorkspaceBundleManifest,
)
from decision_workbench.application.workspace_bundle_shared import (
    DATABASE_ARCHIVE_PATH,
    MANIFEST_ARCHIVE_PATH,
    RESOURCE_ARCHIVE_ROOT,
    WorkspaceBundleError,
    _file_digest,
)
from decision_workbench.application.workspace_bundle_archive import _inspect_bundle
from decision_workbench.application.workspace_bundle_manifest import (
    _database_evidence,
    _file_record,
    _logical_database_backup,
    _snapshot_resources,
    _snapshot_row_payloads,
)


def _safe_backup_destination(
    raw: str | Path,
    *,
    database: Path,
    data_library_root: Path,
) -> Path:
    destination = Path(raw).expanduser().resolve()
    if destination.suffix.lower() != ".mdwb":
        destination = destination.with_suffix(".mdwb")
    for forbidden in (
        database.resolve(),
        data_library_root.resolve(),
        (database.parent / ".workspace-restore").resolve(),
    ):
        if destination == forbidden or forbidden in destination.parents:
            raise WorkspaceBundleError(
                "Backup destination cannot be inside the active Workspace"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination


def create_workspace_backup(
    *,
    database: Path,
    data_library_root: Path,
    destination: str | Path,
    app_version: str,
) -> WorkspaceBackupResult:
    target = _safe_backup_destination(
        destination,
        database=database,
        data_library_root=data_library_root,
    )
    temporary_target = target.with_name(f".{target.name}.{uuid4().hex}.partial")
    with tempfile.TemporaryDirectory(
        prefix="material-workbench-backup-", dir=target.parent
    ) as temporary:
        staging = Path(temporary)
        staged_database = staging / "workbench.db"
        _logical_database_backup(database, staged_database)
        migrations, packages, evidence, diagnostics = _database_evidence(
            staged_database
        )
        resources, resource_files, resource_warnings = _snapshot_resources(
            staged_database, staging / "resources"
        )
        row_payload_files = _snapshot_row_payloads(
            database=database,
            staged_database=staged_database,
            staging=staging,
        )
        database_record = _file_record(staged_database, DATABASE_ARCHIVE_PATH)
        manifest = WorkspaceBundleManifest(
            bundle_id=f"workspace-bundle-{uuid4()}",
            created_at=datetime.now(UTC),
            app_version=app_version,
            database=database_record,
            data_library_files=resource_files,
            row_payload_files=row_payload_files,
            schema_migrations=migrations,
            model_package_references=packages,
            bundled_resources=resources,
            table_counts={item.table: item.row_count for item in evidence},
            table_evidence=evidence,
            diagnostics=(
                *diagnostics,
                WorkspaceBundleDiagnostic(
                    id="cataloged-resources",
                    status="ok",
                    detail=f"{len(resources)} Data Asset/Package references included",
                ),
                WorkspaceBundleDiagnostic(
                    id="auxiliary-relative-files",
                    status="warning" if resource_warnings else "ok",
                    detail=(
                        "; ".join(resource_warnings[:20])
                        if resource_warnings
                        else "Declared relative evidence images are included"
                    ),
                ),
            ),
        )
        try:
            with zipfile.ZipFile(
                temporary_target,
                mode="w",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
            ) as bundle:
                bundle.writestr(
                    MANIFEST_ARCHIVE_PATH,
                    manifest.model_dump_json(indent=2),
                )
                bundle.write(staged_database, DATABASE_ARCHIVE_PATH)
                for record in resource_files:
                    relative = PurePosixPath(record.path).relative_to(
                        RESOURCE_ARCHIVE_ROOT
                    )
                    bundle.write(
                        staging / "resources" / Path(relative.as_posix()),
                        record.path,
                    )
                for record in row_payload_files:
                    bundle.write(staging / Path(record.path), record.path)
            inspected, _ = _inspect_bundle(temporary_target)
            inspected.close()
            os.replace(temporary_target, target)
        finally:
            temporary_target.unlink(missing_ok=True)
    return WorkspaceBackupResult(
        destination=str(target),
        manifest=manifest,
        bundle_sha256=_file_digest(target),
        size_bytes=target.stat().st_size,
    )
