from __future__ import annotations

import pytest


def test_hot_rolling_task_candidates_and_gp_uncertainty(client) -> None:
    task = client.get("/api/hot-rolling/task-definition")
    assert task.status_code == 200
    definition = task.json()
    assert definition["task_id"] == "hot-rolled-properties-v1"
    assert {item["field"] for item in definition["inputs"]} == {
        "reheat_temperature_c", "hold_time_min", "finish_temperature_c",
        "coiling_temperature_c", "cooling_rate_c_s", "entry_thickness_mm", "exit_thickness_mm",
    }
    assert {item["key"] for item in definition["outputs"]} == {"TS", "YS", "EL"}

    candidates = client.get("/api/hot-rolling/candidates").json()
    assert len(candidates) == 3
    selected = candidates[0]
    preview = client.post(f"/api/hot-rolling/candidates/{selected['id']}/preview")
    assert preview.status_code == 200
    result = preview.json()
    assert result["task_id"] == "hot-rolled-properties-v1"
    assert set(result["predictions"]) == {"TS", "YS", "EL"}
    assert result["support"]["status"] in {"supported", "caution", "extrapolated"}
    assert set(result["support"]["components"]) == {"composition", "metallurgy", "process", "route"}
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
