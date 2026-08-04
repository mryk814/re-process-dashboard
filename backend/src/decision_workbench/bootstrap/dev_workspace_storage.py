"""Validate launcher-owned, checkout-local development storage."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal


MANIFEST_SCHEMA_VERSION = "dev-workspace-manifest/v1"
DEV_WORKSPACE_ROOT_ENV = "WORKBENCH_DEV_WORKSPACE_ROOT"
DEV_WORKSPACE_MANIFEST_ENV = "WORKBENCH_DEV_WORKSPACE_MANIFEST"
WORKSPACE_ID_ENV = "WORKBENCH_WORKSPACE_ID"
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_RESOURCE_NAMES = {
    "profile": "profiles",
    "task": "tasks",
    "model": "models",
}
DevResourceKind = Literal["profile", "task", "model"]


def _inside_repository(path: Path) -> bool:
    return path == REPOSITORY_ROOT or REPOSITORY_ROOT in path.parents


def validate_personal_or_dev_store(
    path: Path,
    *,
    resource_kind: DevResourceKind,
    error_type: type[Exception] = ValueError,
) -> Path:
    """Allow external personal stores or one exact launcher-owned sandbox child."""

    resolved = path.expanduser().resolve()
    if not _inside_repository(resolved):
        return resolved

    configured_root = os.getenv(DEV_WORKSPACE_ROOT_ENV, "").strip()
    configured_manifest = os.getenv(DEV_WORKSPACE_MANIFEST_ENV, "").strip()
    configured_id = os.getenv(WORKSPACE_ID_ENV, "").strip()
    if not configured_root or not configured_manifest or not configured_id:
        raise error_type(
            "personal storage must be outside the repository unless it is an "
            "exact launcher-owned development workspace marker target"
        )

    root = Path(configured_root).expanduser().resolve()
    manifest_path = Path(configured_manifest).expanduser().resolve()
    expected_workspace_parent = (REPOSITORY_ROOT / ".dev-workspaces").resolve()
    if root.parent != expected_workspace_parent:
        raise error_type(
            "development workspace root must be an immediate child of "
            ".dev-workspaces"
        )
    if manifest_path != root / "workspace-manifest.json" or not manifest_path.is_file():
        raise error_type("development workspace marker is missing or misplaced")
    if resolved != root / _RESOURCE_NAMES[resource_kind]:
        raise error_type(
            f"repository-local {resource_kind} store is not declared by the "
            "development workspace marker"
        )

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise error_type(f"development workspace marker cannot be read: {exc}") from exc
    resources = payload.get("resources")
    if (
        payload.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or payload.get("workspace_kind") != "branch-default"
        or payload.get("workspace_id") != configured_id
        or root.name != configured_id
        or not isinstance(resources, dict)
        or resources.get(_RESOURCE_NAMES[resource_kind]) != _RESOURCE_NAMES[resource_kind]
    ):
        raise error_type("development workspace marker does not match this workspace")
    declared_checkout = str(payload.get("checkout_root", "")).strip()
    if (
        not declared_checkout
        or Path(declared_checkout).expanduser().resolve() != REPOSITORY_ROOT
    ):
        raise error_type("development workspace marker belongs to another checkout")
    return resolved
