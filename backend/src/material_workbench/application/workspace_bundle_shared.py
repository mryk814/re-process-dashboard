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

DATABASE_ARCHIVE_PATH = "workspace/workbench.db"
MANIFEST_ARCHIVE_PATH = "manifest.json"
RESOURCE_ARCHIVE_ROOT = "workspace/resources"
ROW_PAYLOAD_ARCHIVE_ROOT = "workspace/row-payloads"
LIFECYCLE_ROW_TABLES = frozenset(
    {
        "raw_source_snapshots",
        "source_curation_runs",
        "data_lifecycle_payload_findings",
    }
)
MAX_BUNDLE_ENTRIES = 20_000
MAX_BUNDLE_BYTES = 2 * 1024 * 1024 * 1024
MAX_ENTRY_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MIN_FREE_SPACE_RESERVE = 64 * 1024 * 1024
RESTORE_EXPIRY_HOURS = 24
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class WorkspaceBundleError(RuntimeError):
    pass


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    return value
