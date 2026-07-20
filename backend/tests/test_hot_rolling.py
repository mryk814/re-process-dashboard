from __future__ import annotations

import pytest


def test_hot_rolling_task_candidates_and_gp_uncertainty(client) -> None:
    assert client.get("/api/hot-rolling/task-definition").status_code == 404
    project = client.post(
        "/api/projects",
        json={"name": "熱延検討", "task_id": "hot-rolled-properties-v1"},
    ).json()
    task = client.get(f"/api/projects/{project['id']}/task-definition")
    assert task.status_code == 200
    definition = task.json()["task_definition"]
    assert definition["id"] == "hot-rolled-properties-v1"
    process = next(group for group in definition["input_groups"] if group["key"] == "process")
    assert {item["path"] for item in process["fields"]} == {
        "process.reheat_temperature_c", "process.hold_time_min", "process.finish_temperature_c",
        "process.coiling_temperature_c", "process.cooling_rate_c_s", "process.entry_thickness_mm", "process.exit_thickness_mm",
    }
    assert {item["key"] for item in definition["outputs"]} == {"TS"}
    package = client.get(f"/api/projects/{project['id']}/model-package").json()
    assert package["task_id"] == "hot-rolled-properties-v1"
    assert {item["target"] for item in package["quality_report"]["targets"]} == {"TS"}

    candidates = client.get("/api/hot-rolling/candidates").json()
    assert len(candidates) == 3
    selected = candidates[0]
    preview = client.post(
        f"/api/projects/{project['id']}/candidates/{selected['id']}/preview",
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
    candidate = client.get("/api/hot-rolling/candidates").json()[0]
    candidate["coiling_temperature_c"] = 575.0
    response = client.put(f"/api/hot-rolling/candidates/{candidate['id']}", json=candidate)
    assert response.status_code == 200
    assert response.json()["coiling_temperature_c"] == 575.0
    saved = next(item for item in client.get("/api/hot-rolling/candidates").json() if item["id"] == candidate["id"])
    assert saved["coiling_temperature_c"] == 575.0
