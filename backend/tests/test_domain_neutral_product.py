from __future__ import annotations


def test_flank_wear_can_complete_the_shared_decision_journey(client) -> None:
    task = next(
        item
        for item in client.get("/api/task-definitions").json()
        if item["definition"]["task_definition"]["id"] == "flank-wear-v1"
    )
    datasets = client.get(
        "/api/data-library/datasets",
        params={"include_gallery": "true"},
    )
    assert datasets.status_code == 200, datasets.text
    assert any(
        item["profile_revision"]["profile_id"] == "cutting-flank-wear-v1"
        for item in datasets.json()
    )

    project_response = client.post(
        "/api/projects",
        json={
            "name": "Domain-neutral acceptance: 工具摩耗",
            "task_id": "flank-wear-v1",
            "target_values": {"VB_max": 200},
        },
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    assert project["dataset_view_revision_id"]
    assert project["model_package_ref_id"]

    candidate_response = client.post(
        f"/api/projects/{project['id']}/candidates",
        json=task["starter_candidate"],
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()
    candidate_url = (
        f"/api/projects/{project['id']}/candidates/{candidate['id']}"
    )

    preview = client.post(
        f"{candidate_url}/preview",
        params={"expected_revision": candidate["revision"]},
    )
    assert preview.status_code == 200, preview.text
    assert set(preview.json()["predictions"]) == {"VB_mean", "VB_max"}

    screening = client.post(
        "/api/screening",
        params={"project_id": project["id"]},
        json={
            "purpose": "design_space_map",
            "base_candidate_id": candidate["id"],
            "base_inputs": candidate["inputs"],
            "samples": 48,
            "seed": 543,
            "target": "VB_max",
            "proposal": {"support_policy": "allow_with_warning"},
            "variables": {
                "process.cutting_speed_mpm": {
                    "mode": "range",
                    "min": 150,
                    "max": 250,
                },
            },
        },
    )
    assert screening.status_code == 201, screening.text
    assert screening.json()["project_id"] == project["id"]

    activity = client.post(
        (
            f"{candidate_url}/decision-activities/"
            "robustness-analysis-v1/runs"
        ),
        json={
            "expected_revision": candidate["revision"],
            "parameters": {
                "schema_version": "robustness-parameters/v1",
                "sample_count": 8,
                "seed": 543,
                "tolerance_profile": {
                    "fields": {
                        "process.cutting_speed_mpm": {
                            "kind": "absolute",
                            "amount": 1,
                        },
                    },
                },
            },
        },
    )
    assert activity.status_code == 201, activity.text

    snapshot_response = client.post(f"{candidate_url}/snapshots")
    assert snapshot_response.status_code == 201, snapshot_response.text
    snapshot = snapshot_response.json()
    actual_response = client.post(
        f"{candidate_url}/actuals",
        params={"expected_revision": candidate["revision"]},
        json={
            "property": "VB_max",
            "mean": 188,
            "std": 4,
            "replicates": 3,
            "unit": "µm",
            "experiment_no": "DOMAIN-NEUTRAL-543",
        },
    )
    assert actual_response.status_code == 201, actual_response.text
    actual = actual_response.json()
    decision = client.put(
        f"/api/projects/{project['id']}/decision",
        json={
            "candidate_id": candidate["id"],
            "snapshot_id": actual.get("snapshot_id", snapshot["id"]),
            "note": "工具摩耗の実測と予測を確認",
        },
    )
    assert decision.status_code == 200, decision.text

    history = client.get(f"/api/projects/{project['id']}/history")
    assert history.status_code == 200, history.text
    item = next(
        entry
        for entry in history.json()["candidates"]
        if entry["candidate"]["id"] == candidate["id"]
    )
    assert len(item["actuals"]) == 1
    assert item["snapshots"]
    assert item["decision"]["note"] == "工具摩耗の実測と予測を確認"
    activities = client.get(
        f"/api/projects/{project['id']}/decision-activity-runs",
        params={"candidate_id": candidate["id"]},
    )
    assert activities.status_code == 200, activities.text
    assert len(activities.json()) == 1
