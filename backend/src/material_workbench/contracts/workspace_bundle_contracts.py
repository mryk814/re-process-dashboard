from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceBundleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkspaceBundleFile(WorkspaceBundleModel):
    path: Annotated[str, Field(min_length=1)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=0)]


class WorkspaceBundleMigration(WorkspaceBundleModel):
    id: Annotated[str, Field(min_length=1)]
    checksum: Annotated[str, Field(min_length=1)]
    applied_at: Annotated[str, Field(min_length=1)]


class WorkspaceBundlePackageReference(WorkspaceBundleModel):
    reference_id: Annotated[str, Field(min_length=1)]
    package_id: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    manifest_digest: Annotated[str, Field(min_length=1)]
    archived: bool


class WorkspaceBundleResource(WorkspaceBundleModel):
    kind: Literal["data_asset", "model_package"]
    reference_id: Annotated[str, Field(min_length=1)]
    content_digest: Annotated[str, Field(min_length=1)]
    bundle_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bundle_root: Annotated[str, Field(min_length=1)]
    primary_file: str | None = None
    files: tuple[Annotated[str, Field(min_length=1)], ...]


class WorkspaceTableEvidence(WorkspaceBundleModel):
    table: Annotated[str, Field(min_length=1)]
    columns: tuple[Annotated[str, Field(min_length=1)], ...]
    row_count: Annotated[int, Field(ge=0)]
    digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class WorkspaceBundleDiagnostic(WorkspaceBundleModel):
    id: Annotated[str, Field(min_length=1)]
    status: Literal["ok", "warning"]
    detail: str


class WorkspaceBundleManifest(WorkspaceBundleModel):
    schema_version: Literal["workspace-bundle/v1"] = "workspace-bundle/v1"
    bundle_id: Annotated[str, Field(min_length=1)]
    created_at: datetime
    app_version: Annotated[str, Field(min_length=1)]
    database: WorkspaceBundleFile
    data_library_files: tuple[WorkspaceBundleFile, ...]
    schema_migrations: tuple[WorkspaceBundleMigration, ...]
    model_package_strategy: Literal["included"] = "included"
    model_package_references: tuple[WorkspaceBundlePackageReference, ...]
    bundled_resources: tuple[WorkspaceBundleResource, ...]
    table_counts: dict[str, Annotated[int, Field(ge=0)]]
    table_evidence: tuple[WorkspaceTableEvidence, ...]
    diagnostics: tuple[WorkspaceBundleDiagnostic, ...]


class WorkspaceBackupRequest(BaseModel):
    destination: Annotated[str, Field(min_length=1)]


class WorkspaceBackupResult(WorkspaceBundleModel):
    status: Literal["created"] = "created"
    destination: Annotated[str, Field(min_length=1)]
    manifest: WorkspaceBundleManifest
    bundle_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    size_bytes: Annotated[int, Field(ge=0)]


class WorkspaceRestoreRequest(BaseModel):
    source: Annotated[str, Field(min_length=1)]


class WorkspaceRestorePrepared(WorkspaceBundleModel):
    status: Literal["prepared"] = "prepared"
    restore_token: Annotated[str, Field(min_length=1)]
    manifest: WorkspaceBundleManifest
    migrated_database_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    diagnostics: tuple[WorkspaceBundleDiagnostic, ...]


class WorkspaceRestoreCommitRequest(BaseModel):
    restore_token: Annotated[str, Field(min_length=1)]


class WorkspaceRestoreCommitResult(WorkspaceBundleModel):
    status: Literal["committed"] = "committed"
    restore_token: Annotated[str, Field(min_length=1)]
    rollback_available: bool = True


class WorkspaceRestoreResolution(WorkspaceBundleModel):
    status: Literal["cancelled", "finalized", "rolled_back"]
    restore_token: Annotated[str, Field(min_length=1)]
