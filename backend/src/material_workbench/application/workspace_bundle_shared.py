from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from material_workbench.contracts.workspace_bundle_contracts import (
    WorkspaceBundleResource,
)

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
    "CON",
    "PRN",
    "AUX",
    "NUL",
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


def canonical_resource_bundle_root(
    resource: WorkspaceBundleResource,
) -> str:
    """Return the only archive root accepted for a resource identity."""
    if resource.kind == "data_asset":
        reference_digest = sha256(resource.reference_id.encode("utf-8")).hexdigest()[
            :12
        ]
        relative = f"data-assets/sha256/{resource.content_digest}/{reference_digest}"
    else:
        relative = (
            f"model-packages/sha256/{resource.content_digest.removeprefix('sha256:')}"
        )
    return f"{RESOURCE_ARCHIVE_ROOT}/{relative}"
