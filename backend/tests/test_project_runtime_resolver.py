from __future__ import annotations

import sqlite3

import pytest

from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolutionError


def test_resolver_rebuilds_runtime_from_project_pins_and_caches_it(client) -> None:
    project = client.app.state.store.get_project("default")
    assert project is not None
    resolver = client.app.state.project_runtime_resolver

    first = resolver.resolve(project)
    second = resolver.resolve(project)

    assert first is second
    assert first.runtime.task_id == project.task_id
    assert first.context_runtime.task_id == project.task_id
    assert first.runtime.data.source_sha256
    assert first.data_explorer is not None
    assert first.data_explorer.data is first.runtime.data


def test_preview_reports_target_specific_model_support_and_context_scope(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    preview = client.post(
        f"/api/projects/default/candidates/{candidate['id']}/preview",
        params={"expected_revision": candidate["revision"]},
    )
    assert preview.status_code == 200, preview.text
    support = preview.json()["model_support"]
    assert set(support) == {"TS", "YS", "EL", "lambda"}
    assert support["TS"]["reference_count"] == 143
    assert support["lambda"]["reference_count"] == 134

    similar = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/similar",
        params={"expected_revision": candidate["revision"], "limit": 3},
    )
    assert similar.status_code == 200
    assert all(item["source_scope"] == "project_reference_data" for item in similar.json())


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


def test_resolver_keeps_pinned_archived_records_usable(client) -> None:
    project = client.app.state.store.get_project("default")
    assert project is not None
    resolver = client.app.state.project_runtime_resolver
    resolver.resolve(project)
    client.app.state.workspace_catalog.archive_dataset_view_revision(project.dataset_view_revision_id)
    resolver._cache.clear()

    assert resolver.resolve(project).runtime.task_id == project.task_id


def test_same_asset_with_different_profile_digest_does_not_reuse_training_data(client, monkeypatch) -> None:
    project = client.app.state.store.get_project("default")
    assert project is not None
    resolver = client.app.state.project_runtime_resolver
    original = resolver._dataset_resources
    calls = 0

    def different_revision(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        return (*result[:3], "sha256:application-profile") if calls == 1 else result

    monkeypatch.setattr(resolver, "_dataset_resources", different_revision)
    resolver._cache.clear()
    resolved = resolver.resolve(project)

    assert resolved.runtime.data is not resolved.context_runtime.data
