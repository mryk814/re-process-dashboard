from __future__ import annotations

import json
from pathlib import Path

import pytest

from decision_workbench.bootstrap.dev_workspace_storage import (
    REPOSITORY_ROOT,
    validate_personal_or_dev_store,
)


def _marked_workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    workspace = REPOSITORY_ROOT / ".dev-workspaces" / f"pytest-{tmp_path.name}"
    workspace.mkdir(parents=True, exist_ok=True)
    manifest = workspace / "workspace-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "dev-workspace-manifest/v1",
                "workspace_id": workspace.name,
                "workspace_kind": "branch-default",
                "checkout_identity": "pytest",
                "checkout_root": str(REPOSITORY_ROOT),
                "branch_identity": "pytest",
                "resources": {
                    "database": "workspace.db",
                    "data_library": "data-library",
                    "profiles": "profiles",
                    "tasks": "tasks",
                    "models": "models",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKBENCH_DEV_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("WORKBENCH_DEV_WORKSPACE_MANIFEST", str(manifest))
    monkeypatch.setenv("WORKBENCH_WORKSPACE_ID", workspace.name)
    monkeypatch.setenv("WORKBENCH_WORKSPACE_KIND", "branch-default")
    return workspace


def test_launcher_marker_allows_only_declared_profile_task_and_model_stores(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _marked_workspace(monkeypatch, tmp_path)
    try:
        for resource_kind, name in (
            ("profile", "profiles"),
            ("task", "tasks"),
            ("model", "models"),
        ):
            path = workspace / name
            path.mkdir()
            assert validate_personal_or_dev_store(
                path,
                resource_kind=resource_kind,  # type: ignore[arg-type]
            ) == path.resolve()

        with pytest.raises(ValueError, match="not declared"):
            validate_personal_or_dev_store(
                workspace / "other",
                resource_kind="task",
            )
        with pytest.raises(ValueError, match="marker"):
            monkeypatch.delenv("WORKBENCH_DEV_WORKSPACE_MANIFEST")
            validate_personal_or_dev_store(
                workspace / "tasks",
                resource_kind="task",
            )
    finally:
        for candidate in sorted(workspace.glob("*")):
            candidate.unlink() if candidate.is_file() else candidate.rmdir()
        workspace.rmdir()


def test_external_personal_store_remains_allowed(tmp_path: Path) -> None:
    store = tmp_path / "profiles"
    assert validate_personal_or_dev_store(
        store,
        resource_kind="profile",
    ) == store.resolve()


def test_launcher_workspace_rejects_resource_symlink_before_resolving_outside(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = _marked_workspace(monkeypatch, tmp_path)
    external = tmp_path / "external-tasks"
    external.mkdir()
    tasks = workspace / "tasks"
    try:
        tasks.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable in this Windows session")
    try:
        with pytest.raises(ValueError, match="symlink/reparse"):
            validate_personal_or_dev_store(tasks, resource_kind="task")
    finally:
        tasks.unlink()
        (workspace / "workspace-manifest.json").unlink()
        workspace.rmdir()
