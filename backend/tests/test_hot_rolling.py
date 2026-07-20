from __future__ import annotations

import pytest


def test_hot_rolling_task_candidates_and_gp_uncertainty(client) -> None:
    assert client.get("/api/hot-rolling/task-definition").status_code == 404
    project_id = "hot-rolling-default"
    task = client.get(f"/api/projects/{project_id}/task-definition")
    assert task.status_code == 200
    definition = task.json()["task_definition"]
    assert definition["id"] == "hot-rolled-properties-v1"
    process = next(group for group in definition["input_groups"] if group["key"] == "process")
    assert {item["path"] for item in process["fields"]} == {
        "process.reheat_temperature_c", "process.hold_time_min", "process.finish_temperature_c",
        "process.coiling_temperature_c", "process.cooling_rate_c_s", "process.entry_thickness_mm", "process.exit_thickness_mm",
    }
    assert {item["key"] for item in definition["outputs"]} == {"TS"}
    package = client.get(f"/api/projects/{project_id}/model-package").json()
    assert package["task_id"] == "hot-rolled-properties-v1"
    assert {item["target"] for item in package["quality_report"]["targets"]} == {"TS"}

    candidates = client.get(f"/api/projects/{project_id}/candidates").json()
    assert len(candidates) == 3
    selected = candidates[0]
    preview = client.post(
        f"/api/projects/{project_id}/candidates/{selected['id']}/preview",
        params={"expected_revision": selected["revision"]},
    )
    assert preview.status_code == 200
    result = preview.json()
    assert result["task_id"] == "hot-rolled-properties-v1"
    assert result["candidate_id"] == selected["id"]
    assert result["mode"] == "preview"
    assert result["heat_pattern"] == []
    assert set(result["predictions"]) == {"TS"}
    assert result["support"]["status"] in {"supported", "caution", "extrapolated"}
    assert result["support"]["distance"] == 0.4823
    assert set(result["support"]["components"]) == {"composition", "metallurgy", "process", "categorical"}
    assert result["support"]["components"] == {
        "composition": 0.4837,
        "metallurgy": 0.4421,
        "process": 0.7078,
        "categorical": 0.0,
    }
    for prediction in result["predictions"].values():
        assert prediction["lower"] < prediction["upper"]
        components = prediction["uncertainty_components"]
        assert components["total_predictive_variance"] == pytest.approx(components["latent_model_variance"] + components["observation_noise_variance"], abs=2e-6)


def test_hot_rolling_candidate_edit_is_persisted(client) -> None:
    project_id = "hot-rolling-default"
    candidate = client.get(f"/api/projects/{project_id}/candidates").json()[0]
    candidate["inputs"]["process"]["coiling_temperature_c"] = 575.0
    payload = {key: candidate[key] for key in ("name", "inputs", "provenance")}
    payload["expected_revision"] = candidate["revision"]
    response = client.put(f"/api/projects/{project_id}/candidates/{candidate['id']}", json=payload)
    assert response.status_code == 200
    assert response.json()["inputs"]["process"]["coiling_temperature_c"] == 575.0
    saved = next(item for item in client.get(f"/api/projects/{project_id}/candidates").json() if item["id"] == candidate["id"])
    assert saved["inputs"]["process"]["coiling_temperature_c"] == 575.0


def test_hot_rolling_detailed_snapshot_and_actual_use_the_common_project_api(client) -> None:
    project_id = "hot-rolling-default"
    candidate = client.get(f"/api/projects/{project_id}/candidates").json()[0]

    detailed = client.post(f"/api/projects/{project_id}/candidates/{candidate['id']}/predict", params={"expected_revision": candidate["revision"]})
    assert detailed.status_code == 200
    assert detailed.json()["prediction"]["mode"] == "detailed"
    snapshot_id = detailed.json()["snapshot"]["id"]
    actual = client.post(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/actuals",
        params={"expected_revision": candidate["revision"]},
        json={"property": "TS", "mean": 510.0, "unit": "MPa"},
    )
    assert actual.status_code == 201
    assert actual.json()["snapshot_id"] != snapshot_id
    assert len(client.get(f"/api/projects/{project_id}/candidates/{candidate['id']}/snapshots").json()) == 2
    assert len(client.get(f"/api/projects/{project_id}/candidates/{candidate['id']}/actuals").json()) == 1


def test_legacy_candidate_routes_are_removed_and_project_ownership_is_enforced(client) -> None:
    candidate = client.get("/api/projects/hot-rolling-default/candidates").json()[0]
    assert client.get("/api/hot-rolling/candidates").status_code == 404
    assert client.get("/api/candidates").status_code == 404
    assert client.get(f"/api/projects/default/candidates/{candidate['id']}").status_code == 404
    assert client.post(f"/api/projects/default/candidates/{candidate['id']}/preview", params={"expected_revision": candidate["revision"]}).status_code == 404


def test_hot_rolling_screening_keeps_project_scope_and_nested_candidate_contract(client) -> None:
    project_id = "hot-rolling-default"
    candidate = client.get(f"/api/projects/{project_id}/candidates").json()[0]
    response = client.post(
        f"/api/screening?project_id={project_id}",
        json={
            "base_candidate_id": candidate["id"],
            "samples": 48,
            "target": "TS",
            "target_value": 500,
            "variables": {"composition.C": {"mode": "range", "min": 0.04, "max": 0.12}},
        },
    )

    assert response.status_code == 201
    run = response.json()
    assert run["score_contract"]["direction"] == "at_least"
    created = client.post(
        f"/api/screening/{run['id']}/candidates?project_id={project_id}",
        json={"point_indices": [0]},
    )
    assert created.status_code == 201
    candidate_from_point = created.json()["candidates"][0]
    assert candidate_from_point["project_id"] == project_id
    assert set(candidate_from_point["inputs"]) == {"composition", "process", "categorical", "heat_pattern"}
    assert candidate_from_point["provenance"]["source_kind"] == "screening"


def test_hot_rolling_project_runs_the_full_common_candidate_flow(client) -> None:
    project = client.post(
        "/api/projects",
        json={
            "name": "熱延E2E",
            "task_id": "hot-rolled-properties-v1",
            "target_values": {"TS": 500},
        },
    )
    assert project.status_code == 201
    project_id = project.json()["id"]
    source = client.get("/api/projects/hot-rolling-default/candidates").json()[0]
    payload = {key: source[key] for key in ("name", "inputs", "provenance")}

    created = client.post(f"/api/projects/{project_id}/candidates", json=payload)
    assert created.status_code == 201
    candidate = created.json()
    candidate_id = candidate["id"]
    assert candidate["project_id"] == project_id
    assert client.get(f"/api/projects/{project_id}/candidates/{candidate_id}").status_code == 200

    updated_payload = {key: candidate[key] for key in ("name", "inputs", "provenance")}
    updated_payload["name"] = "熱延E2E 更新"
    updated_payload["expected_revision"] = candidate["revision"]
    updated = client.put(
        f"/api/projects/{project_id}/candidates/{candidate_id}",
        json=updated_payload,
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "熱延E2E 更新"
    assert client.post(f"/api/projects/{project_id}/candidates/{candidate_id}/preview", params={"expected_revision": updated.json()["revision"]}).status_code == 200

    disposable = client.post(f"/api/projects/{project_id}/candidates", json=payload).json()
    assert client.delete(f"/api/projects/{project_id}/candidates/{disposable['id']}?expected_revision={disposable['revision']}").status_code == 204

    current_revision = updated.json()["revision"]
    detailed = client.post(f"/api/projects/{project_id}/candidates/{candidate_id}/predict", params={"expected_revision": current_revision})
    assert detailed.status_code == 200
    snapshot_id = detailed.json()["snapshot"]["id"]
    assert client.get(f"/api/projects/{project_id}/candidates/{candidate_id}/snapshots").json()[0]["id"] == snapshot_id
    actual = client.post(
        f"/api/projects/{project_id}/candidates/{candidate_id}/actuals",
        params={"expected_revision": current_revision},
        json={"property": "TS", "mean": 510.0, "unit": "MPa"},
    )
    assert actual.status_code == 201
    actual_id = actual.json()["id"]
    assert client.delete(
        f"/api/projects/{project_id}/candidates/{candidate_id}/actuals/{actual_id}"
    ).status_code == 204
