from __future__ import annotations

import sqlite3

import pytest

from material_workbench.project_runtime_resolver import ProjectRuntimeResolutionError


def test_resolver_rebuilds_runtime_from_project_pins_and_caches_it(client) -> None:
    project = client.app.state.store.get_project("default")
    assert project is not None
    resolver = client.app.state.project_runtime_resolver

    first = resolver.resolve(project)
    second = resolver.resolve(project)

    assert first is second
    assert first.runtime.task_id == project.task_id
    assert first.runtime.data.source_sha256
    assert first.data_explorer is not None
    assert first.data_explorer.data is first.runtime.data


def test_resolver_rejects_a_changed_excel_instead_of_using_current_runtime(client) -> None:
    project = client.app.state.store.get_project("default")
    assert project is not None
    resolver = client.app.state.project_runtime_resolver
    resolver._cache.clear()
    with sqlite3.connect(client.app.state.store.path) as conn:
        conn.execute(
            "UPDATE data_assets SET sha256=? WHERE id=("
            "SELECT d.data_asset_id FROM dataset_revisions d "
            "JOIN dataset_view_members m ON m.dataset_revision_id=d.id "
            "WHERE m.dataset_view_revision_id=?)",
            ("0" * 64, project.dataset_view_revision_id),
        )

    with pytest.raises(ProjectRuntimeResolutionError, match="登録時から変わっています"):
        resolver.resolve(project)
