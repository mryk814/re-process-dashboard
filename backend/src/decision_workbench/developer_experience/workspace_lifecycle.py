from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any


class WorkspacePruneRefused(ValueError):
    """Raised when a database is not an explicitly prunable branch workspace."""


_MANIFEST_SCHEMA_VERSION = "dev-workspace-manifest/v1"
_ACTIVE_LOCK_NAME = "workspace-active.json"


@dataclass(frozen=True)
class RepositoryWorkspaceContext:
    current_branch: str
    branches: tuple[str, ...]
    worktree_branches: frozenset[str]


def branch_workspace_name(branch: str) -> str:
    normalized = unicodedata.normalize("NFKC", branch)
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized)
    normalized = normalized.strip("-")[:80]
    digest = sha256(branch.encode("utf-8")).hexdigest()[:8]
    return f"{normalized or 'unknown-checkout'}-{digest}"


def checkout_identity(repository_root: Path) -> str:
    canonical = str(repository_root.resolve())
    if os.name == "nt":
        canonical = canonical.replace("\\", "/").lower()
    return sha256(canonical.encode("utf-8")).hexdigest()[:8]


def repository_workspace_context(repository_root: Path) -> RepositoryWorkspaceContext:
    root = repository_root.resolve()

    def git(*arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return completed.stdout

    current_branch = git("branch", "--show-current").strip()
    if not current_branch:
        current_branch = f"detached-{git('rev-parse', '--short', 'HEAD').strip()}"
    branches = {
        line.strip()
        for line in git(
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
        ).splitlines()
        if line.strip()
    }
    worktree_branches: set[str] = set()
    for block in git("worktree", "list", "--porcelain").split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        branch_line = next(
            (line for line in lines if line.startswith("branch refs/heads/")),
            None,
        )
        if branch_line is not None:
            worktree_branches.add(
                branch_line.removeprefix("branch refs/heads/").strip()
            )
            continue
        if "detached" not in lines:
            continue
        worktree_line = next(
            (line for line in lines if line.startswith("worktree ")),
            None,
        )
        if worktree_line is None:
            continue
        worktree_path = worktree_line.removeprefix("worktree ").strip()
        try:
            revision = git(
                "-C",
                worktree_path,
                "rev-parse",
                "--short",
                "HEAD",
            ).strip()
        except subprocess.CalledProcessError:
            continue
        worktree_branches.add(f"detached-{revision}")
    branches.update(worktree_branches)
    branches.add(current_branch)
    return RepositoryWorkspaceContext(
        current_branch=current_branch,
        branches=tuple(sorted(branches)),
        worktree_branches=frozenset(worktree_branches),
    )


def _branch_by_workspace_name(
    context: RepositoryWorkspaceContext,
) -> dict[str, str]:
    return {
        branch_workspace_name(branch): branch
        for branch in context.branches
    }


def _protection_reasons(
    *,
    workspace_name: str,
    branch: str | None,
    context: RepositoryWorkspaceContext,
) -> list[str]:
    reasons: list[str] = []
    if workspace_name == branch_workspace_name("main") or branch == "main":
        reasons.append("main")
    if branch is not None and branch == context.current_branch:
        reasons.append("current-branch")
    if branch is not None and branch in context.worktree_branches:
        reasons.append("registered-worktree")
    if branch is None:
        reasons.append("unrecognized-database")
    return reasons


def _is_reparse_point(path: Path) -> bool:
    try:
        stat = path.lstat()
    except OSError:
        return True
    return path.is_symlink() or bool(
        getattr(stat, "st_file_attributes", 0) & 0x400
    )


def _marked_workspace(
    root: Path,
    *,
    repository_root: Path,
) -> tuple[dict[str, Any], Path]:
    if _is_reparse_point(root):
        raise WorkspacePruneRefused(f"symlink/reparse pointはpruneできません: {root}")
    manifest = root / "workspace-manifest.json"
    if _is_reparse_point(manifest) or not manifest.is_file():
        raise WorkspacePruneRefused(f"launcher markerが見つかりません: {manifest}")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise WorkspacePruneRefused(f"launcher markerを読めません: {manifest}") from exc
    resources = payload.get("resources")
    branch = str(payload.get("branch_identity", "")).strip()
    expected_checkout_identity = checkout_identity(repository_root)
    expected_workspace_id = (
        f"{branch_workspace_name(branch)}-{expected_checkout_identity}"
        if branch
        else ""
    )
    if (
        payload.get("schema_version") != _MANIFEST_SCHEMA_VERSION
        or payload.get("workspace_kind") != "branch-default"
        or payload.get("workspace_id") != expected_workspace_id
        or root.name != expected_workspace_id
        or payload.get("checkout_identity") != expected_checkout_identity
        or not str(payload.get("checkout_root", "")).strip()
        or Path(str(payload["checkout_root"])).expanduser().resolve()
        != repository_root.resolve()
        or resources
        != {
            "database": "workspace.db",
            "data_library": "data-library",
            "profiles": "profiles",
            "tasks": "tasks",
            "models": "models",
        }
    ):
        raise WorkspacePruneRefused(f"launcher markerがworkspaceと一致しません: {manifest}")
    database = root / "workspace.db"
    return payload, database


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _active_lock_reason(root: Path) -> str | None:
    lock = root / _ACTIVE_LOCK_NAME
    if not lock.exists():
        return None
    if _is_reparse_point(lock) or not lock.is_file():
        return "active-lock-invalid"
    try:
        payload = json.loads(lock.read_text(encoding="utf-8"))
        pid = int(payload["pid"])
    except (json.JSONDecodeError, KeyError, OSError, TypeError, ValueError):
        return "active-lock-invalid"
    return "active-server" if _pid_running(pid) else None


def _workspace_size_and_latest(root: Path) -> tuple[int, float]:
    files: list[Path] = []
    for candidate in root.rglob("*"):
        if _is_reparse_point(candidate):
            continue
        if candidate.is_file():
            files.append(candidate)
    if not files:
        return 0, root.stat().st_mtime
    return (
        sum(path.stat().st_size for path in files),
        max(path.stat().st_mtime for path in files),
    )


def list_branch_workspaces(
    repository_root: Path,
    *,
    context: RepositoryWorkspaceContext | None = None,
) -> list[dict[str, Any]]:
    root = repository_root.resolve()
    dev_root = root / ".dev-workspaces"
    resolved_context = context or repository_workspace_context(root)
    branches = _branch_by_workspace_name(resolved_context)
    if not dev_root.is_dir():
        return []
    result: list[dict[str, Any]] = []
    for workspace_root in sorted(path for path in dev_root.iterdir() if path.is_dir()):
        try:
            payload, database = _marked_workspace(
                workspace_root,
                repository_root=root,
            )
        except WorkspacePruneRefused:
            continue
        branch = str(payload.get("branch_identity", "")).strip() or None
        reasons = _protection_reasons(
            workspace_name=workspace_root.name,
            branch=branch,
            context=resolved_context,
        )
        lock_reason = _active_lock_reason(workspace_root)
        if lock_reason is not None:
            reasons.append(lock_reason)
        size_bytes, latest = _workspace_size_and_latest(workspace_root)
        result.append(
            {
                "path": str(database.resolve()),
                "root_path": str(workspace_root.resolve()),
                "origin": "launcher-marker",
                "branch": branch,
                "updated_at": datetime.fromtimestamp(
                    latest,
                    tz=timezone.utc,
                ).isoformat(),
                "size_bytes": size_bytes,
                "referenced": "registered-worktree" in reasons,
                "prunable": not reasons,
                "protection_reasons": reasons,
            }
        )
    for database in sorted(dev_root.glob("*.db")):
        workspace_name = database.stem
        branch = branches.get(workspace_name)
        related = [
            path
            for path in (
                database,
                Path(f"{database}-wal"),
                Path(f"{database}-shm"),
            )
            if path.is_file()
        ]
        latest = max(path.stat().st_mtime for path in related)
        reasons = _protection_reasons(
            workspace_name=workspace_name,
            branch=branch,
            context=resolved_context,
        )
        result.append(
            {
                "path": str(database.resolve()),
                "root_path": None,
                "origin": "legacy-direct-database",
                "branch": branch,
                "updated_at": datetime.fromtimestamp(
                    latest,
                    tz=timezone.utc,
                ).isoformat(),
                "size_bytes": sum(path.stat().st_size for path in related),
                "referenced": "registered-worktree" in reasons,
                "prunable": not reasons,
                "protection_reasons": reasons,
            }
        )
    return result


def prune_branch_workspace(
    repository_root: Path,
    *,
    database: Path,
    context: RepositoryWorkspaceContext | None = None,
) -> dict[str, Any]:
    root = repository_root.resolve()
    dev_root = (root / ".dev-workspaces").resolve()
    target = database.expanduser()
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    marked_root = target.parent
    is_marked = marked_root.parent == dev_root and target.name == "workspace.db"
    is_legacy = target.parent == dev_root and target.suffix == ".db"
    if not is_marked and not is_legacy:
        raise WorkspacePruneRefused(
            "prune対象は.dev-workspaces内の明示されたWorkspace DBだけです"
        )
    if not target.is_file():
        raise WorkspacePruneRefused(f"Workspace DBが見つかりません: {target}")

    resolved_context = context or repository_workspace_context(root)
    if is_marked:
        payload, declared_database = _marked_workspace(
            marked_root,
            repository_root=root,
        )
        if declared_database.resolve() != target:
            raise WorkspacePruneRefused("launcher markerのDB宣言と対象が一致しません")
        branch = str(payload.get("branch_identity", "")).strip() or None
        workspace_name = marked_root.name
        lock_reason = _active_lock_reason(marked_root)
    else:
        branch = _branch_by_workspace_name(resolved_context).get(target.stem)
        workspace_name = target.stem
        lock_reason = None
    reasons = _protection_reasons(
        workspace_name=workspace_name,
        branch=branch,
        context=resolved_context,
    )
    if lock_reason is not None:
        reasons.append(lock_reason)
    if reasons:
        raise WorkspacePruneRefused(
            f"Workspace pruneを拒否しました ({', '.join(reasons)}): {target}"
        )

    removed: list[str] = []
    if is_marked:
        for candidate in marked_root.rglob("*"):
            if _is_reparse_point(candidate):
                raise WorkspacePruneRefused(
                    f"symlink/reparse pointを含むWorkspaceはpruneできません: {candidate}"
                )
        shutil.rmtree(marked_root)
        removed.append(str(marked_root))
    else:
        candidates = [
            target,
            Path(f"{target}-wal"),
            Path(f"{target}-shm"),
        ]
        library = dev_root / f"{target.stem}-data-library"
        for candidate in candidates:
            if candidate.is_file():
                candidate.unlink()
                removed.append(str(candidate))
        if library.is_dir():
            shutil.rmtree(library)
            removed.append(str(library))
    return {
        "status": "pruned",
        "database": str(target),
        "branch": branch,
        "removed": removed,
    }
