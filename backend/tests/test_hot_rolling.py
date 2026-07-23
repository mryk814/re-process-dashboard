from __future__ import annotations

import pytest


def test_hot_rolling_task_candidates_and_horseshoe_uncertainty(client) -> None:
    assert client.get("/api/hot-rolling/task-definition").status_code == 404
    project_id = "hot-rolling-default"
    task = client.get(f"/api/projects/{project_id}/task-definition")
    assert task.status_code == 200
    definition = task.json()["task_definition"]
    assert definition["id"] == "hot-rolled-properties-v1"
    process = next(group for group in definition["input_groups"] if group["key"] == "process")
    assert {item["path"] for item in process["fields"]} == {
        "process.soaking_temperature_c", "process.finish_temperature_c", "process.entry_thickness_mm",
        "process.exit_thickness_mm", "process.hold_temperature_c", "process.hold_time_min",
    }
    assert {item["key"] for item in definition["outputs"]} == {"TS"}
    package = client.get(f"/api/projects/{project_id}/model-package").json()
    assert package["task_id"] == "hot-rolled-properties-v1"
    assert package["id"] == "hot-rolled-tutorial-v1"
    assert package["active_runtimes"] == ["builtin.posterior_linear.v1"]
    assert package["quality_report"]["split"] == "leave-one-parent-condition-out"
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
    assert result["support"]["distance"] >= 0
    assert set(result["support"]["components"]) == {"composition", "metallurgy", "process"}
    assert all(value >= 0 for value in result["support"]["components"].values())
    for prediction in result["predictions"].values():
        assert prediction["lower"] < prediction["upper"]
        components = prediction["uncertainty_components"]
        assert prediction["predictive_family"] == "normal"
        assert components["epistemic_std"] >= 0
        assert components["aleatoric_std"] > 0
        assert components["total_predictive_std"] == pytest.approx((components["epistemic_std"] ** 2 + components["aleatoric_std"] ** 2) ** 0.5, abs=2e-6)
    assert result["model_meta"]["model"]["method"] == "Ridge approximate posterior for tiny teaching data"
    assert result["model_meta"]["prediction_interval"]["method"] == "posterior_predictive_moment_matched_normal"


def test_hot_rolling_candidate_edit_is_persisted(client) -> None:
    project_id = "hot-rolling-default"
    candidate = client.get(f"/api/projects/{project_id}/candidates").json()[0]
    candidate["inputs"]["process"]["hold_temperature_c"] = 1150.0
    payload = {key: candidate[key] for key in ("name", "inputs", "provenance")}
    payload["expected_revision"] = candidate["revision"]
    response = client.put(f"/api/projects/{project_id}/candidates/{candidate['id']}", json=payload)
    assert response.status_code == 200
    assert response.json()["inputs"]["process"]["hold_temperature_c"] == 1150.0
    saved = next(item for item in client.get(f"/api/projects/{project_id}/candidates").json() if item["id"] == candidate["id"])
    assert saved["inputs"]["process"]["hold_temperature_c"] == 1150.0


def test_hot_rolling_response_curve_uses_existing_numeric_inputs(client) -> None:
    project_id = "hot-rolling-default"
    candidate = client.get(f"/api/projects/{project_id}/candidates").json()[0]
    response = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-curve",
        params={
            "expected_revision": candidate["revision"],
            "target": "TS",
            "variable": "process.finish_temperature_c",
            "points": 7,
        },
    )

    assert response.status_code == 200
    curve = response.json()
    assert curve["target"] == "TS"
    assert curve["variable"]["id"] == "process.finish_temperature_c"
    assert curve["variable"]["label"] == "仕上げ温度"
    assert curve["variable"]["unit"] == "°C"
    assert curve["variable"]["training_range"] == {"min": 830.762, "max": 931.334}
    assert len(curve["points"]) == 7
    assert curve["points"][0]["x"] == 830.762
    assert curve["points"][-1]["x"] == 931.334
    assert all(point["lower"] < point["upper"] for point in curve["points"])

    unsupported = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-curve",
        params={
            "expected_revision": candidate["revision"],
            "target": "TS",
            "variable": "process.not_a_field",
        },
    )
    assert unsupported.status_code == 422

    candidate["inputs"]["process"].update({"soaking_temperature_c": 850.0, "finish_temperature_c": 840.0})
    constrained_payload = {key: candidate[key] for key in ("name", "inputs", "provenance")}
    constrained_payload["expected_revision"] = candidate["revision"]
    constrained = client.put(
        f"/api/projects/{project_id}/candidates/{candidate['id']}",
        json=constrained_payload,
    ).json()
    clipped = client.get(
        f"/api/projects/{project_id}/candidates/{candidate['id']}/response-curve",
        params={
            "expected_revision": constrained["revision"],
            "target": "TS",
            "variable": "process.finish_temperature_c",
        },
    )
    assert clipped.status_code == 200
    assert clipped.json()["variable"]["max"] == 850.0


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
            "base_inputs": candidate["inputs"],
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
    assert set(candidate_from_point["inputs"]) == {"composition", "process", "categorical", "heat_pattern", "heat_time_basis"}
    assert candidate_from_point["provenance"]["source_kind"] == "screening"


def test_hot_rolling_project_runs_the_full_common_candidate_flow(client) -> None:
    reference = client.get("/api/projects/hot-rolling-default").json()
    project = client.post(
        "/api/projects",
        json={
            "name": "熱延E2E",
            "task_id": "hot-rolled-properties-v1",
            "target_values": {"TS": 500},
            "dataset_view_revision_id": reference["dataset_view_revision_id"],
            "model_package_ref_id": reference["model_package_ref_id"],
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
    preview = client.post(
        f"/api/projects/{project_id}/candidates/{candidate_id}/preview",
        params={"expected_revision": updated.json()["revision"]},
    )
    assert preview.status_code == 200
    target_prediction = preview.json()["predictions"]["TS"]
    assert target_prediction["goal_value"] == 500
    assert 0 <= target_prediction["goal_probability"] <= 1
    assert target_prediction["goal_direction"] == "at_least"

    range_project = project.json()
    range_project["target_values"] = {"TS": {"lower": 450, "upper": 550}}
    saved_range = client.put(f"/api/projects/{project_id}", json=range_project)
    assert saved_range.status_code == 200
    assert saved_range.json()["target_values"]["TS"] == {"lower": 450, "upper": 550}
    range_preview = client.post(
        f"/api/projects/{project_id}/candidates/{candidate_id}/preview",
        params={"expected_revision": updated.json()["revision"]},
    )
    assert range_preview.status_code == 200
    range_prediction = range_preview.json()["predictions"]["TS"]
    assert range_prediction["goal_value"] is None
    assert range_prediction["goal_lower"] == 450
    assert range_prediction["goal_upper"] == 550
    assert range_prediction["goal_direction"] == "between"
    assert 0 <= range_prediction["goal_probability"] <= 1

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
