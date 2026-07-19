from copy import deepcopy

from material_workbench.schemas import CandidateInput


def _payload(name: str = "試験候補") -> dict:
    return {
        "name": name,
        "composition": {"C": 0.08, "Si": 0.3, "Mn": 1.5},
        "thickness_mm": 1.4,
        "line_speed_m_min": 103.0,
        "coating": "GI",
        "heat_pattern": [
            {"time_s": 0, "temperature_c": 25},
            {"time_s": 280, "temperature_c": 800},
            {"time_s": 340, "temperature_c": 810},
            {"time_s": 650, "temperature_c": 120},
        ],
    }


def test_heat_pattern_rejects_non_monotonic_time() -> None:
    invalid = _payload()
    invalid["heat_pattern"][2]["time_s"] = 280
    try:
        CandidateInput.model_validate(invalid)
    except ValueError as exc:
        assert "厳密な昇順" in str(exc)
    else:
        raise AssertionError("non-monotonic heat pattern must not be accepted")


def test_candidate_rejects_unknown_or_non_physical_composition() -> None:
    unknown = _payload()
    unknown["composition"]["Unobtainium"] = 0.1
    assert "未対応の組成元素" in str(_validation_error(unknown))
    negative = _payload()
    negative["composition"]["C"] = -0.01
    assert "0〜100" in str(_validation_error(negative))


def _validation_error(payload: dict) -> ValueError:
    try:
        CandidateInput.model_validate(payload)
    except ValueError as exc:
        return exc
    raise AssertionError("invalid candidate must not be accepted")


def test_health_and_candidate_prediction_flow_is_deterministic(client) -> None:
    assert client.get("/api/health").json()["ok"] is True
    package = client.get("/api/model-package").json()
    assert package["id"] == "annealed-ridge-2026-07"
    assert len(package["manifest_sha256"]) == 64
    assert {item["runtime_type"] for item in package["supported_runtimes"]} == {
        "builtin.linear.v1", "sklearn.skops.v1", "lightgbm.booster.v1",
        "gpytorch.static_exact_rbf.v1", "numpyro.dense_posterior.v1",
    }
    candidate = client.post("/api/candidates", json=_payload()).json()
    first = client.post(f"/api/candidates/{candidate['id']}/preview").json()
    second = client.post(f"/api/candidates/{candidate['id']}/preview").json()
    assert first["mode"] == "preview"
    assert first["predictions"] == second["predictions"]
    assert {"TS", "YS", "EL", "lambda"} <= set(first["predictions"])
    assert first["support"]["status"] in {"supported", "caution", "extrapolated"}
    assert 0 <= first["support"]["percentile"] <= 100
    assert {"composition", "metallurgy", "process", "heat_pattern"} == set(first["support"]["components"])
    assert first["support"]["reference_count"] > 1
    assert first["model_meta"]["prediction_interval"]["method"] == "grouped_oof_residual_quantiles"
    assert first["model_meta"]["prediction_interval"]["grouping"] == "parent_key"
    assert first["canonical_input"]["input_schema_version"] == "candidate-v1"
    atomic_result = client.post(f"/api/candidates/{candidate['id']}/predict").json()
    detailed = atomic_result["prediction"]
    assert detailed["mode"] == "detailed"
    assert len(detailed["response_curve"]) == 9
    assert atomic_result["snapshot"]["payload"]["prediction"] == detailed
    similar = client.get(f"/api/candidates/{candidate['id']}/similar").json()
    assert len(similar) == 6
    assert {item["layer"] for item in similar} == {"training", "historical"}
    assert all({"composition", "metallurgy", "process", "heat_pattern"} == set(item["components"]) for item in similar)


def test_snapshot_is_immutable_after_candidate_edit(client) -> None:
    candidate = client.post("/api/candidates", json=_payload("固定化テスト")).json()
    snapshot = client.post(f"/api/candidates/{candidate['id']}/snapshots").json()
    original = deepcopy(snapshot["payload"])
    changed = _payload("編集後")
    changed["line_speed_m_min"] = 145
    assert client.put(f"/api/candidates/{candidate['id']}", json=changed).status_code == 200
    stored = client.get(f"/api/candidates/{candidate['id']}/snapshots").json()
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
