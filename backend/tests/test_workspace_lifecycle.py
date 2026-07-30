from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from material_workbench.developer_experience.workspace_lifecycle import (
    RepositoryWorkspaceContext,
    WorkspacePruneRefused,
    branch_workspace_name,
    list_branch_workspaces,
    prune_branch_workspace,
)


def _context(
    *,
    current: str = "codex/current",
    worktrees: tuple[str, ...] = ("main", "codex/current", "codex/referenced"),
) -> RepositoryWorkspaceContext:
    branches = {
        "main",
        "codex/current",
        "codex/referenced",
        "codex/stale",
        current,
        *worktrees,
    }
    return RepositoryWorkspaceContext(
        current_branch=current,
        branches=tuple(sorted(branches)),
        worktree_branches=frozenset(worktrees),
    )


def _database(root: Path, branch: str) -> Path:
    path = root / ".dev-workspaces" / f"{branch_workspace_name(branch)}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"workspace:{branch}".encode())
    return path


def test_workspace_list_is_read_only_and_reports_branch_time_and_size(
    tmp_path: Path,
) -> None:
    current = _database(tmp_path, "codex/current")
    stale = _database(tmp_path, "codex/stale")
    before = sha256(current.read_bytes() + stale.read_bytes()).hexdigest()

    result = list_branch_workspaces(tmp_path, context=_context())

    after = sha256(current.read_bytes() + stale.read_bytes()).hexdigest()
    assert before == after
    assert [item["branch"] for item in result] == [
        "codex/current",
        "codex/stale",
    ]
    assert result[0]["updated_at"]
    assert result[0]["size_bytes"] == current.stat().st_size
    assert result[0]["protection_reasons"] == [
        "current-branch",
        "registered-worktree",
    ]
    assert result[1]["prunable"] is True


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("main", "main"),
        ("codex/current", "current-branch"),
        ("codex/referenced", "registered-worktree"),
    ],
)
def test_workspace_prune_refuses_protected_branch(
    tmp_path: Path,
    branch: str,
    expected: str,
) -> None:
    database = _database(tmp_path, branch)

    with pytest.raises(WorkspacePruneRefused, match=expected):
        prune_branch_workspace(tmp_path, database=database, context=_context())

    assert database.exists()


def test_workspace_prune_refuses_the_current_detached_checkout(
    tmp_path: Path,
) -> None:
    current = "detached-abc1234"
    database = _database(tmp_path, current)

    with pytest.raises(WorkspacePruneRefused, match="current-branch"):
        prune_branch_workspace(
            tmp_path,
            database=database,
            context=_context(current=current),
        )

    assert database.exists()


def test_workspace_prune_refuses_unrecognized_and_outside_database(
    tmp_path: Path,
) -> None:
    unknown = tmp_path / ".dev-workspaces" / "manual.db"
    unknown.parent.mkdir(parents=True)
    unknown.write_bytes(b"saved")
    outside = tmp_path / "data" / "workbench.db"
    outside.parent.mkdir()
    outside.write_bytes(b"saved")

    with pytest.raises(WorkspacePruneRefused, match="unrecognized-database"):
        prune_branch_workspace(
            tmp_path,
            database=unknown,
            context=_context(),
        )
    with pytest.raises(WorkspacePruneRefused, match="\\.dev-workspaces"):
        prune_branch_workspace(tmp_path, database=outside, context=_context())

    assert unknown.read_bytes() == b"saved"
    assert outside.read_bytes() == b"saved"


def test_workspace_prune_removes_only_explicit_stale_workspace_family(
    tmp_path: Path,
) -> None:
    stale = _database(tmp_path, "codex/stale")
    Path(f"{stale}-wal").write_bytes(b"wal")
    Path(f"{stale}-shm").write_bytes(b"shm")
    library = stale.parent / f"{stale.stem}-data-library"
    library.mkdir()
    (library / "dataset.csv").write_text("x\n1\n", encoding="utf-8")
    source = tmp_path / "data" / "source" / "source.xlsx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"source")
    package = tmp_path / "models" / "packages" / "package" / "manifest.json"
    package.parent.mkdir(parents=True)
    package.write_text("{}", encoding="utf-8")
    main = _database(tmp_path, "main")

    result = prune_branch_workspace(
        tmp_path,
        database=stale,
        context=_context(),
    )

    assert result["branch"] == "codex/stale"
    assert not stale.exists()
    assert not Path(f"{stale}-wal").exists()
    assert not Path(f"{stale}-shm").exists()
    assert not library.exists()
    assert source.read_bytes() == b"source"
    assert package.read_text(encoding="utf-8") == "{}"
    assert main.exists()
