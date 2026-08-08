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
WORKSPACE_KIND_ENV = "WORKBENCH_WORKSPACE_KIND"
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_RESOURCE_NAMES = {
    "profile": "profiles",
    "task": "tasks",
    "model": "models",
}
DevResourceKind = Literal["profile", "task", "model"]


def _inside_repository(path: Path) -> bool:
    return path == REPOSITORY_ROOT or REPOSITORY_ROOT in path.parents


def _is_reparse_point(path: Path) -> bool:
    try:
        stat = path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0) & 0x400
    )


def _reject_reparse_chain(path: Path, *, error_type: type[Exception]) -> None:
    for candidate in (path, *path.parents):
        if _is_reparse_point(candidate):
            raise error_type(
                f"development workspace storage cannot use a symlink/reparse point: {candidate}"
            )


def validate_personal_or_dev_store(
    path: Path,
    *,
    resource_kind: DevResourceKind,
    error_type: type[Exception] = ValueError,
) -> Path:
    """Allow external personal stores or one exact launcher-owned sandbox child."""

    configured_root = os.getenv(DEV_WORKSPACE_ROOT_ENV, "").strip()
    configured_manifest = os.getenv(DEV_WORKSPACE_MANIFEST_ENV, "").strip()
    configured_id = os.getenv(WORKSPACE_ID_ENV, "").strip()
    configured_kind = os.getenv(WORKSPACE_KIND_ENV, "").strip()
    marker_identity = (
        configured_root,
        configured_manifest,
        configured_id,
    )
    raw = path.expanduser().absolute()
    if configured_kind != "branch-default":
        if any(marker_identity):
            raise error_type(
                "development workspace launcher marker cannot be used outside "
                "a branch-default workspace"
            )
        resolved = raw.resolve()
        if not _inside_repository(resolved):
            return resolved
        raise error_type(
            "personal storage must be outside the repository unless it is an "
            "exact launcher-owned development workspace marker target"
        )
    if not all(marker_identity):
        raise error_type("development workspace launcher marker identity is incomplete")

    root_raw = Path(configured_root).expanduser().absolute()
    manifest_raw = Path(configured_manifest).expanduser().absolute()
    _reject_reparse_chain(raw, error_type=error_type)
    _reject_reparse_chain(root_raw, error_type=error_type)
    _reject_reparse_chain(manifest_raw, error_type=error_type)
    expected_workspace_parent = (REPOSITORY_ROOT / ".dev-workspaces").absolute()
    if root_raw.parent != expected_workspace_parent:
        raise error_type(
            "development workspace root must be an immediate child of "
            ".dev-workspaces"
        )
    if (
        manifest_raw != root_raw / "workspace-manifest.json"
        or not manifest_raw.is_file()
    ):
        raise error_type("development workspace marker is missing or misplaced")
    expected_resource = root_raw / _RESOURCE_NAMES[resource_kind]
    if raw != expected_resource:
        raise error_type(
            f"repository-local {resource_kind} store is not declared by the "
            "development workspace marker"
        )
    root = root_raw.resolve()
    manifest_path = manifest_raw.resolve()
    resolved = raw.resolve()
    if resolved != root / _RESOURCE_NAMES[resource_kind]:
        raise error_type("development workspace resource resolves outside its marker root")

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
