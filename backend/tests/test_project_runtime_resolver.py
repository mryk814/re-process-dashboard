from __future__ import annotations

import sqlite3

import pytest

from material_workbench.application.inference import InferenceService
from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.tasks.project_runtime_resolver import (
    ProjectRuntimeResolutionError,
    ProjectRuntimeResolver,
)


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
    assert support["TS"]["reference_count"] == 7
    assert support["lambda"]["reference_count"] == 7

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
    assert resolved.runtime.model_package is resolved.context_runtime.model_package


def test_resolver_cache_is_bounded_lru(client, monkeypatch) -> None:
    project = client.app.state.store.get_project("default")
    assert project is not None
    resolver = ProjectRuntimeResolver(
        client.app.state.workspace_catalog,
        client.app.state.task_registry,
        max_cache_entries=2,
    )
    resolved = client.app.state.project_runtime_resolver.resolve(project)
    builds: list[str] = []

    def build(selected):
        builds.append(selected.dataset_view_revision_id)
        return resolved

    monkeypatch.setattr(resolver, "_build", build)
    first = project.model_copy(update={"dataset_view_revision_id": "view-1"})
    second = project.model_copy(update={"dataset_view_revision_id": "view-2"})
    third = project.model_copy(update={"dataset_view_revision_id": "view-3"})

    resolver.resolve(first)
    resolver.resolve(second)
    resolver.resolve(first)  # refresh first; second is now the least-recently used
    resolver.resolve(third)
    resolver.resolve(first)
    resolver.resolve(second)

    assert builds == ["view-1", "view-2", "view-3", "view-2"]
    assert len(resolver._cache) == 2


def test_resolver_cache_never_crosses_task_identity(client, monkeypatch) -> None:
    project = client.app.state.store.get_project("default")
    assert project is not None
    resolver = ProjectRuntimeResolver(
        client.app.state.workspace_catalog,
        client.app.state.task_registry,
        max_cache_entries=2,
    )
    resolved = client.app.state.project_runtime_resolver.resolve(project)
    builds: list[str] = []

    def build(selected):
        builds.append(selected.task_id)
        return resolved

    monkeypatch.setattr(resolver, "_build", build)
    other_task_id = next(
        task_id
        for task_id in client.app.state.task_registry.task_ids
        if task_id != project.task_id
    )
    resolver.resolve(project)
    resolver.resolve(project.model_copy(update={"task_id": other_task_id}))

    assert builds == [project.task_id, other_task_id]


def test_project_pinned_package_identity_drives_runtime_and_inference_key(client, monkeypatch) -> None:
    default = client.get("/api/projects/default").json()
    options = client.get("/api/project-creation-options").json()
    alternative = next(
        item
        for item in options["model_packages"]
        if item["task_id"] == default["task_id"]
        and item["id"] != default["model_package_ref_id"]
        and item["package_id"] == "annealed-lightgbm-standard-tutorial-v2"
    )
    created = client.post(
        "/api/projects",
        json={
            "name": "Project固定Packageの確認",
            "task_id": default["task_id"],
            "dataset_view_revision_id": default["dataset_view_revision_id"],
            "model_package_ref_id": alternative["id"],
        },
    )
    assert created.status_code == 201, created.text
    project = client.app.state.store.get_project(created.json()["id"])
    assert project is not None

    resolved = client.app.state.project_runtime_resolver.resolve(project)
    active = client.app.state.task_registry.entry_for(project.task_id)
    assert resolved.identity.package_manifest_digest == alternative["manifest_digest"]
    assert resolved.identity.package_digest == f"sha256:{alternative['manifest_digest']}"
    assert resolved.identity.package_digest != active.package_digest
    assert resolved.identity.runtime_type == "lightgbm.booster.v1"
    assert resolved.identity.runtime_type != active.runtime_type

    template = client.get("/api/projects/default/candidates").json()[0]
    candidate_response = client.post(
        f"/api/projects/{project.id}/candidates",
        json={"name": "固定Package候補", "inputs": template["inputs"]},
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = client.app.state.store.get_candidate(candidate_response.json()["id"], project.id)
    assert candidate is not None
    service = InferenceService(
        client.app.state.store,
        client.app.state.task_registry,
        InferenceWorkGraph(max_entries=2),
        client.app.state.project_runtime_resolver,
    )
    client.app.state.project_runtime_resolver._cache.clear()
    client.app.state.project_runtime_resolver._package_cache.clear()
    from material_workbench.modeling.model_packages import ModelPackageLoader

    original_load = ModelPackageLoader.load
    package_loads = 0

    def count_package_loads(loader, package_root):
        nonlocal package_loads
        package_loads += 1
        return original_load(loader, package_root)

    monkeypatch.setattr(ModelPackageLoader, "load", count_package_loads)
    key = service.key(project, candidate, "preview", uses_package=True, uses_support=True)
    first_request_loads = package_loads
    repeated_key = service.key(project, candidate, "preview", uses_package=True, uses_support=True)
    client.app.state.project_runtime_resolver._cache.clear()
    key_after_runtime_eviction = service.key(
        project, candidate, "preview", uses_package=True, uses_support=True
    )

    assert key.package_digest == resolved.identity.package_digest
    assert key.pipeline_digest == resolved.identity.pipeline_digest
    assert key.support_digest == resolved.identity.support_digest
    assert repeated_key == key
    assert key_after_runtime_eviction == key
    assert first_request_loads == 1
    assert package_loads == first_request_loads
    assert len(client.app.state.project_runtime_resolver._package_cache) == 1


def test_project_package_changes_curve_and_contour_training_ranges_together(client) -> None:
    datasets = client.get(
        "/api/data-library/datasets", params={"include_gallery": True}
    ).json()
    packages = client.get(
        "/api/data-library/model-packages", params={"include_gallery": True}
    ).json()
    dataset = next(
        item
        for item in datasets
        if item["data_asset"]["original_filename"]
        == "material_workbench_process_v1.xlsx"
    )
    package = next(
        item
        for item in packages
        if item["package_id"] == "annealed-gp-stable-ard-process-v2"
    )
    created = client.post(
        "/api/projects",
        json={
            "name": "Package学習範囲の確認",
            "task_id": "annealed-properties-v1",
            "dataset_view_revision_id": dataset["dataset_views"][0]["id"],
            "model_package_ref_id": package["id"],
        },
    )
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    template = client.get("/api/projects/default/candidates").json()[0]
    candidate_response = client.post(
        f"/api/projects/{project_id}/candidates",
        json={"name": "範囲確認", "inputs": template["inputs"]},
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()

    curve = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-curve",
        params={
            "expected_revision": candidate["revision"],
            "target": "TS",
            "variable": "composition.C",
            "points": 7,
        },
    )
    contour = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-contour",
        params={
            "expected_revision": candidate["revision"],
            "target": "TS",
            "x_variable": "composition.C",
            "y_variable": "composition.Mn",
            "points": 7,
        },
    )
    assert curve.status_code == 200, curve.text
    assert contour.status_code == 200, contour.text

    project = client.app.state.store.get_project(project_id)
    assert project is not None
    runtime = client.app.state.project_runtime_resolver.runtime_for(project)
    expected = runtime.training_range_for("TS", "composition.C")
    curve_range = curve.json()["variable"]["training_range"]
    contour_range = contour.json()["x_axis"]["training_range"]
    assert (curve_range["min"], curve_range["max"]) == tuple(
        round(value, 4) for value in expected
    )
    assert contour_range == curve_range
    tutorial_runtime = client.app.state.task_registry.runtime_for(
        "annealed-properties-v1"
    )
    assert tutorial_runtime.training_range_for("TS", "composition.C") != expected
