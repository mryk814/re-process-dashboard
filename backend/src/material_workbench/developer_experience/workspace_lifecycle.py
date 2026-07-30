from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any


class WorkspacePruneRefused(ValueError):
    """Raised when a database is not an explicitly prunable branch workspace."""


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
    return reasons


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
    if target.parent != dev_root or target.suffix != ".db":
        raise WorkspacePruneRefused(
            "prune対象は.dev-workspaces直下の明示された.dbだけです"
        )
    if not target.is_file():
        raise WorkspacePruneRefused(f"Workspace DBが見つかりません: {target}")

    resolved_context = context or repository_workspace_context(root)
    branch = _branch_by_workspace_name(resolved_context).get(target.stem)
    reasons = _protection_reasons(
        workspace_name=target.stem,
        branch=branch,
        context=resolved_context,
    )
    if reasons:
        raise WorkspacePruneRefused(
            f"Workspace pruneを拒否しました ({', '.join(reasons)}): {target}"
        )

    candidates = [
        target,
        Path(f"{target}-wal"),
        Path(f"{target}-shm"),
    ]
    library = dev_root / f"{target.stem}-data-library"
    removed: list[str] = []
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
