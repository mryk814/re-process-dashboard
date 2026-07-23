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
    assert set(payload["task_contract_digests"]) >= {
        "annealed-properties-v1",
        "hot-rolled-properties-v1",
        "flank-wear-v1",
    }
    assert all(
        package["task_contract_digest"]
        == payload["task_contract_digests"][package["task_id"]]
        for package in payload["model_packages"]
    )


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


def test_cohort_comparison_is_not_a_project_reference_dataset(client) -> None:
    options = client.get("/api/project-creation-options").json()
    members = [
        {"dataset_revision_id": item["dataset_revision"]["id"], "ordinal": index, "cohort_key": f"c{index}"}
        for index, item in enumerate(options["datasets"][:2])
    ]
    view_response = client.post("/api/data-library/views", json={
        "view_id": "comparison-not-project-input",
        "revision": 1,
        "name": "設備比較",
        "kind": "cohort_comparison",
        "members": members,
    })
    assert view_response.status_code == 201, view_response.text

    package = next(item for item in options["model_packages"] if item["task_id"] == "annealed-properties-v1")
    created = client.post("/api/projects", json={
        "name": "比較セットを誤用する検討",
        "task_id": "annealed-properties-v1",
        "dataset_view_revision_id": view_response.json()["id"],
        "model_package_ref_id": package["id"],
    })
    assert created.status_code == 422
    assert "比較Activity" in created.json()["message"]


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


def test_continuation_can_switch_prediction_task_within_the_same_series(client) -> None:
    options = client.get("/api/project-creation-options").json()
    original = client.get("/api/projects/default").json()
    other_series = client.get("/api/projects/hot-rolling-default").json()["project_series_id"]
    hot_package = next(item for item in options["model_packages"] if item["task_id"] == "hot-rolled-properties-v1")
    payload = {
        "name": "同じテーマの熱延検討",
        "task_id": "hot-rolled-properties-v1",
        "dataset_view_revision_id": original["dataset_view_revision_id"],
        "model_package_ref_id": hot_package["id"],
        "project_series_id": original["project_series_id"],
        "predecessor_project_id": original["id"],
        "continuation_reason": "同じ材料テーマを熱延特性でも評価するため",
    }

    mismatched_series = client.post("/api/projects", json={**payload, "project_series_id": other_series})
    assert mismatched_series.status_code == 422
    assert "継続元と異なる一連の検討" in mismatched_series.json()["message"]

    created = client.post("/api/projects", json=payload)

    assert created.status_code == 201, created.text
    project = created.json()
    assert project["task_id"] == "hot-rolled-properties-v1"
    assert project["project_series_id"] == original["project_series_id"]
    assert project["predecessor_project_id"] == original["id"]


def test_project_with_successor_cannot_be_deleted(client) -> None:
    first = client.post("/api/projects", json={
        "name": "系譜の起点", "task_id": "annealed-properties-v1",
    })
    assert first.status_code == 201, first.text
    successor = client.post("/api/projects", json={
        "name": "系譜の続き",
        "task_id": "annealed-properties-v1",
        "predecessor_project_id": first.json()["id"],
        "continuation_reason": "データ追加",
    })
    assert successor.status_code == 201, successor.text

    deleted = client.delete(f"/api/projects/{first.json()['id']}")
    assert deleted.status_code == 409
    assert deleted.json()["code"] == "project_has_successors"


def test_dataset_view_revision_conflict_returns_409(client) -> None:
    dataset = client.get("/api/project-creation-options").json()["datasets"][0]["dataset_revision"]
    payload = {
        "view_id": "stable-view-revision",
        "revision": 1,
        "name": "最初の名前",
        "kind": "single",
        "members": [{"dataset_revision_id": dataset["id"], "ordinal": 0}],
    }
    assert client.post("/api/data-library/views", json=payload).status_code == 201
    conflict = client.post("/api/data-library/views", json={**payload, "name": "別の名前"})
    assert conflict.status_code == 409
