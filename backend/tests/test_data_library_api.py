from __future__ import annotations


def test_data_library_exposes_semantic_dataset_records_and_creation_options(client) -> None:
    datasets = client.get("/api/data-library/datasets")
    assert datasets.status_code == 200
    items = datasets.json()
    assert len(items) == 2
    assert all(item["data_asset"]["sha256"] for item in items)
    assert all(item["profile_revision"]["profile_digest"] for item in items)
    assert all(item["dataset_revision"]["dataset_digest"] for item in items)
    assert {task for item in items for task in item["supported_task_ids"]} >= {
        "annealed-properties-v1",
        "hot-rolled-properties-v1",
        "flank-wear-v1",
    }

    options = client.get("/api/project-creation-options")
    assert options.status_code == 200
    payload = options.json()
    assert len(payload["dataset_views"]) == 2
    assert len(payload["model_packages"]) == 3
    assert payload["project_series"]


def test_project_creation_pins_explicit_references_and_rejects_rebinding(client) -> None:
    options = client.get("/api/project-creation-options").json()
    dataset = next(
        item for item in options["datasets"]
        if "annealed-properties-v1" in item["supported_task_ids"]
    )
    view = dataset["dataset_views"][0]
    package = next(
        item for item in options["model_packages"]
        if item["task_id"] == "annealed-properties-v1"
    )
    series = client.post(
        "/api/project-series", json={"name": "一連の材料検討", "description": "2026年の検討"}
    ).json()

    created = client.post("/api/projects", json={
        "name": "固定参照の確認",
        "task_id": "annealed-properties-v1",
        "dataset_view_revision_id": view["id"],
        "model_package_ref_id": package["id"],
        "project_series_id": series["id"],
    })
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["binding_provenance"] == "explicit"
    assert project["dataset_view_revision_id"] == view["id"]
    assert project["model_package_manifest_digest"] == package["manifest_digest"]
    assert project["project_series_id"] == series["id"]

    rejected = client.put(
        f"/api/projects/{project['id']}",
        json={"name": project["name"], "task_id": "hot-rolled-properties-v1"},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "project_task_locked"


def test_continuation_stays_in_series_and_requires_reason(client) -> None:
    original = client.get("/api/projects/default").json()
    missing_reason = client.post("/api/projects", json={
        "name": "継続検討",
        "task_id": original["task_id"],
        "predecessor_project_id": original["id"],
    })
    assert missing_reason.status_code == 422

    created = client.post("/api/projects", json={
        "name": "継続検討",
        "task_id": original["task_id"],
        "predecessor_project_id": original["id"],
        "continuation_reason": "実験データを追加したため",
    })
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["project_series_id"] == original["project_series_id"]
    assert project["predecessor_project_id"] == original["id"]
