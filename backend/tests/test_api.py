from copy import deepcopy

from material_workbench.schemas import CandidateInput

ELEMENTS = ("C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca")


def _payload(name: str = "試験候補") -> dict:
    return {
        "name": name,
        "inputs": {
            "composition": {**{key: 0.0 for key in ELEMENTS}, "C": 0.08, "Si": 0.3, "Mn": 1.5},
            "process": {"thickness_mm": 1.4, "line_speed_m_min": 103.0},
            "categorical": {"coating": "GI"},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 25},
                {"time_s": 280, "temperature_c": 800},
                {"time_s": 340, "temperature_c": 810},
                {"time_s": 650, "temperature_c": 120},
            ],
        },
    }


def test_heat_pattern_rejects_non_monotonic_time() -> None:
    invalid = _payload()
    invalid["inputs"]["heat_pattern"][2]["time_s"] = 280
    try:
        CandidateInput.model_validate(invalid)
    except ValueError as exc:
        assert "厳密な昇順" in str(exc)
    else:
        raise AssertionError("non-monotonic heat pattern must not be accepted")


def test_candidate_rejects_unknown_or_non_physical_composition(client) -> None:
    unknown = _payload()
    unknown["inputs"]["composition"]["Unobtainium"] = 0.1
    assert client.post("/api/projects/default/candidates", json=unknown).status_code == 422
    negative = _payload()
    negative["inputs"]["composition"]["C"] = -0.01
    assert client.post("/api/projects/default/candidates", json=negative).status_code == 422


def _validation_error(payload: dict) -> ValueError:
    try:
        CandidateInput.model_validate(payload)
    except ValueError as exc:
        return exc
    raise AssertionError("invalid candidate must not be accepted")


def test_health_and_candidate_prediction_flow_is_deterministic(client) -> None:
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/model-package").status_code == 404
    package = client.get("/api/projects/default/model-package").json()
    assert package["id"] == "annealed-gp-2026-07"
    assert len(package["manifest_sha256"]) == 64
    assert package["quality_report"]["split"] == "leave-one-parent-condition-out"
    assert {item["target"] for item in package["quality_report"]["targets"]} == {"TS", "YS", "EL", "lambda"}
    assert {item["runtime_type"] for item in package["supported_runtimes"]} == {
        "builtin.linear.v1", "builtin.exact_gp.v1", "sklearn.skops.v1", "lightgbm.booster.v1",
        "gpytorch.static_exact_rbf.v1", "numpyro.dense_posterior.v1",
    }
    task = client.get("/api/projects/default/task-definition").json()
    definition = task["task_definition"]
    assert definition["id"] == "annealed-properties-v1"
    composition = next(group for group in definition["input_groups"] if group["key"] == "composition")
    assert [item["path"].removeprefix("composition.") for item in composition["fields"]] == [
        "C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca",
    ]
    assert {item["key"] for item in definition["outputs"]} == {"TS", "YS", "EL", "lambda"}
    assert all(item["goal_direction"] == "at_least" for item in definition["outputs"])
    assert task["runtime_capability"]["operations"]["response_curve"] is True
    candidate = client.post("/api/projects/default/candidates", json=_payload()).json()
    first = client.post(f"/api/projects/default/candidates/{candidate['id']}/preview").json()
    second = client.post(f"/api/projects/default/candidates/{candidate['id']}/preview").json()
    assert first["mode"] == "preview"
    assert first["predictions"] == second["predictions"]
    assert {"TS", "YS", "EL", "lambda"} <= set(first["predictions"])
    assert first["support"]["status"] in {"supported", "caution", "extrapolated"}
    assert 0 <= first["support"]["percentile"] <= 100
    assert {"composition", "metallurgy", "process", "heat_pattern"} == set(first["support"]["components"])
    assert first["support"]["reference_count"] > 1
    assert first["model_meta"]["prediction_interval"]["method"] == "gaussian_process_predictive_distribution"
    assert first["model_meta"]["prediction_interval"]["grouping"] == "parent_key"
    assert all(prediction["uncertainty_components"] for prediction in first["predictions"].values())
    assert first["canonical_input"]["input_schema_version"] == "candidate-v1"
    atomic_result = client.post(f"/api/projects/default/candidates/{candidate['id']}/predict").json()
    detailed = atomic_result["prediction"]
    assert detailed["mode"] == "detailed"
    assert len(detailed["response_curve"]) == 9
    curves = client.get(f"/api/projects/default/candidates/{candidate['id']}/response-curves").json()
    assert set(curves) == {"TS", "YS", "EL", "lambda"}
    assert all(len(points) == 9 for points in curves.values())
    assert atomic_result["snapshot"]["payload"]["prediction"] == detailed
    similar = client.get(f"/api/projects/default/candidates/{candidate['id']}/similar").json()
    assert len(similar) == 6
    assert {item["layer"] for item in similar} == {"training", "historical"}
    assert all({"composition", "metallurgy", "process", "heat_pattern"} == set(item["components"]) for item in similar)


def test_snapshot_is_immutable_after_candidate_edit(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("固定化テスト")).json()
    snapshot = client.post(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()
    original = deepcopy(snapshot["payload"])
    changed = _payload("編集後")
    changed["inputs"]["process"]["line_speed_m_min"] = 145
    assert client.put(f"/api/projects/default/candidates/{candidate['id']}", json={**changed, "expected_revision": candidate["revision"]}).status_code == 200
    stored = client.get(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()
    assert stored[0]["payload"] == original
    assert stored[0]["payload"]["candidate_id"] == candidate["id"]
    assert stored[0]["payload"]["raw_candidate"]["name"] == "固定化テスト"
    assert "feature_vector" in stored[0]["payload"]["canonical_input"]
    assert "TS" in stored[0]["payload"]["canonical_input"]["normalized_feature_vectors"]
    provenance = stored[0]["payload"]["provenance"]
    assert provenance["model"]["version"]
    assert provenance["feature_pipeline"]["version"]
    assert provenance["training_data"]["source_sha256"]
    assert provenance["similarity"]["version"]


def test_electron_file_origin_is_allowed_without_credentials(client) -> None:
    response = client.options(
        "/api/health",
        headers={"Origin": "null", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "null"


def test_quality_lineage_and_bootstrap(client) -> None:
    bootstrap = client.get("/api/bootstrap").json()
    assert bootstrap["meta"]["quality_issues"] == 36
    assert bootstrap["candidates"]
    quality = client.get("/api/quality").json()
    assert quality["by_category"]["関連ファイル欠損"] == 19
    assert quality["reference_scenarios"] == quality["issues"]
    assert quality["detected_total"] == len(quality["detected_issues"])
    assert {issue["issue_type"] for issue in quality["detected_issues"]} <= {"missing_key", "orphan_entity", "duplicate_key", "invalid_reference"}
    assert {"issue_id", "source_sheet", "entity_key", "detail"} <= set(quality["detected_issues"][0])
    lineage = client.get("/api/lineage/AN-00001")
    assert lineage.status_code == 200
    assert "relations" in lineage.json()
    node = lineage.json()["node"]
    assert node["entity_type"] == "焼鈍"
    assert node["source_sheet"] == "焼鈍"
    assert node["source_row"]["焼鈍_key"] == "AN-00001"
    assert node["composition"]
    assert len(node["heat_pattern"]) == 14
    assert node["property_summary"]["TS[MPa]"]["count"] > 0
    assert isinstance(node["property_summary"]["TS[MPa]"]["mean"], float)
    assert isinstance(node["property_summary"]["TS[MPa]"]["std"], float)
    assert node["connected_observations"]
    assert node["connected_observations"][0]["id"]
    assert node["observation_groups"]
    assert all(group["stage"] in {"熱延後", "焼鈍後"} for group in node["observation_groups"])
    assert lineage.json()["graph"]["relation_row_count"] > 0
    assert any(edge["route_rows"] for edge in lineage.json()["graph"]["edges"])
    assert lineage.json()["candidate_eligible"] is True
    assert any(edge["source"] == "HR-00001" and edge["target"] == "AN-00001" for edge in lineage.json()["graph"]["edges"])


def test_lineage_index_and_isolated_nodes_are_inspectable(client) -> None:
    index = client.get("/api/lineage", params={"query": "AN-00001"}).json()
    assert index["relation_rows"] > 1_800
    assert index["total_entities"] > index["relation_rows"]
    assert index["items"][0]["key"] == "AN-00001"
    assert index["items"][0]["family"]
    assert index["items"][0]["route"]
    assert index["items"][0]["peak_temperature_c"] > 0
    assert index["items"][0]["observation_summary"]
    assert client.get("/api/lineage", params={"query": index["items"][0]["family"], "entity_type": "焼鈍"}).json()["items"]
    isolated = client.get("/api/lineage/CR-00010")
    assert isolated.status_code == 200
    assert isolated.json()["node"]["entity_type"] == "冷延"
    assert isolated.json()["graph"]["relation_row_count"] == 0
    assert isolated.json()["candidate_eligible"] is False
    invalid_key = next(
        issue["entity_key"]
        for issue in client.get("/api/quality").json()["detected_issues"]
        if issue["issue_type"] == "invalid_reference"
    )
    invalid_index = client.get("/api/lineage", params={"query": invalid_key}).json()
    assert invalid_index["items"] == [{"key": invalid_key, "entity_type": "熱延引張", "has_issue": True}]
    invalid = client.get(f"/api/lineage/{invalid_key}")
    assert invalid.status_code == 200
    assert invalid.json()["node"]["missing_source"] is True


def test_lineage_keeps_hot_rolled_and_annealed_observations_separate(client) -> None:
    payload = client.get("/api/lineage/AN-00003").json()
    node = payload["node"]
    ts_groups = [group for group in node["observation_groups"] if group["property"] == "TS[MPa]"]
    assert {group["stage"] for group in ts_groups} == {"熱延後", "焼鈍後"}
    assert all(group["count"] == len(group["observations"]) for group in ts_groups)
    annealed_properties = {group["property"] for group in node["observation_groups"] if group["stage"] == "焼鈍後"}
    assert {"均一伸び[%]", "r値[-]", "n値[-]"} <= annealed_properties
    assert any(point["stage_category"] for point in node["heat_pattern"])
    assert any(point["set_temperature_c"] is not None for point in node["heat_pattern"])
    edge_pairs = {(edge["source"], edge["target"]) for edge in payload["graph"]["edges"]}
    assert ("HR-00003", "CR-00002") in edge_pairs
    assert ("CR-00002", "AN-00003") in edge_pairs
    assert ("HR-00003", "AN-00003") not in edge_pairs
