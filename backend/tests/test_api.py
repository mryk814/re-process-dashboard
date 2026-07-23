from copy import deepcopy

from material_workbench.schemas import CandidateInput

ELEMENTS = ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")


def _payload(name: str = "試験候補") -> dict:
    return {
        "name": name,
        "inputs": {
            "composition": {**{key: 0.0 for key in ELEMENTS}, "C": 0.08, "Si": 0.3, "Mn": 1.5},
            "process": {"ls_mpm": 103.0},
            "categorical": {},
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


def test_existing_candidate_payload_defaults_to_line_speed_time_basis() -> None:
    candidate = CandidateInput.model_validate(_payload())

    assert candidate.inputs.heat_time_basis == "line_speed"


def test_candidate_update_canonicalizes_line_speed_times_and_rejects_direct_edits(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("LS基準")).json()
    changed_speed = _payload("LS基準")
    changed_speed["inputs"]["process"]["ls_mpm"] = 206.0
    changed_speed["inputs"]["heat_pattern"][1]["time_s"] = 290

    updated = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**changed_speed, "expected_revision": candidate["revision"]},
    )

    assert updated.status_code == 200
    assert updated.json()["inputs"]["heat_time_basis"] == "line_speed"
    assert [point["time_s"] for point in updated.json()["inputs"]["heat_pattern"]] == [0.0, 140.0, 170.0, 325.0]

    direct_edit = deepcopy(updated.json())
    direct_edit["inputs"]["heat_pattern"][1]["time_s"] = 150.0
    rejected = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={
            key: value
            for key, value in direct_edit.items()
            if key not in {"id", "project_id", "created_at", "updated_at", "archived_at", "revision"}
        }
        | {"expected_revision": updated.json()["revision"]},
    )
    assert rejected.status_code == 422
    assert "経過時間基準" in rejected.json()["message"]


def test_elapsed_time_candidate_update_allows_independent_time_and_speed_edits(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("経過時間基準")).json()
    switch = _payload("経過時間基準")
    switch["inputs"]["heat_time_basis"] = "elapsed_time"
    switch["inputs"]["heat_pattern"][1]["time_s"] = 290
    switched = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**switch, "expected_revision": candidate["revision"]},
    )

    assert switched.status_code == 200
    assert [point["time_s"] for point in switched.json()["inputs"]["heat_pattern"]] == [0.0, 290.0, 340.0, 650.0]

    edit = _payload("経過時間基準")
    edit["inputs"]["heat_time_basis"] = "elapsed_time"
    edit["inputs"]["process"]["ls_mpm"] = 206.0
    edit["inputs"]["heat_pattern"][1]["time_s"] = 300.0
    edited = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**edit, "expected_revision": switched.json()["revision"]},
    )
    assert edited.status_code == 200
    assert edited.json()["inputs"]["process"]["ls_mpm"] == 206.0
    assert [point["time_s"] for point in edited.json()["inputs"]["heat_pattern"]] == [0.0, 300.0, 340.0, 650.0]


def test_time_basis_switch_accepts_simultaneous_line_speed_change(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("基準切替")).json()
    update = _payload("基準切替")
    update["inputs"]["heat_time_basis"] = "elapsed_time"
    update["inputs"]["process"]["ls_mpm"] = 120.0
    update["inputs"]["heat_pattern"][1]["time_s"] = 300.0

    response = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**update, "expected_revision": candidate["revision"]},
    )

    assert response.status_code == 200
    assert response.json()["inputs"]["process"]["ls_mpm"] == 120.0
    assert response.json()["inputs"]["heat_pattern"][1]["time_s"] == 300.0


def test_line_speed_candidate_accepts_speed_and_point_count_change_as_new_layout(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("点数変更")).json()
    changed = _payload("点数変更")
    changed["inputs"]["process"]["ls_mpm"] = 120.0
    scale = 103.0 / 120.0
    for point in changed["inputs"]["heat_pattern"]:
        point["time_s"] *= scale
    changed["inputs"]["heat_pattern"].append({"time_s": 700 * scale, "temperature_c": 80})

    response = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**changed, "expected_revision": candidate["revision"]},
    )

    assert response.status_code == 200
    assert len(response.json()["inputs"]["heat_pattern"]) == 5
    assert response.json()["inputs"]["heat_pattern"][-1]["time_s"] == 700 * scale


def test_line_speed_candidate_allows_point_count_change_without_speed_change(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("点追加")).json()
    changed = _payload("点追加")
    changed["inputs"]["heat_pattern"].insert(2, {"time_s": 310, "temperature_c": 805})

    response = client.put(
        f"/api/projects/default/candidates/{candidate['id']}",
        json={**changed, "expected_revision": candidate["revision"]},
    )

    assert response.status_code == 200
    assert [point["time_s"] for point in response.json()["inputs"]["heat_pattern"]] == [
        0.0,
        280.0,
        310.0,
        340.0,
        650.0,
    ]


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
        "builtin.linear.v1", "builtin.exact_gp.v1", "builtin.additive_terms.v1", "builtin.quantile_linear.v1", "builtin.posterior_linear.v1", "sklearn.skops.v1", "lightgbm.booster.v1",
        "gpytorch.static_exact_rbf.v1", "numpyro.dense_posterior.v1",
    }
    task = client.get("/api/projects/default/task-definition").json()
    definition = task["task_definition"]
    assert definition["id"] == "annealed-properties-v1"
    composition = next(group for group in definition["input_groups"] if group["key"] == "composition")
    assert [item["path"].removeprefix("composition.") for item in composition["fields"]] == [
        "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N",
    ]
    assert {item["key"] for item in definition["outputs"]} == {"TS", "YS", "EL", "lambda"}
    assert all(item["goal_direction"] == "at_least" for item in definition["outputs"])
    assert task["runtime_capability"]["operations"]["response_curve"] is True
    candidate = client.post("/api/projects/default/candidates", json=_payload()).json()
    params = {"expected_revision": candidate["revision"]}
    first = client.post(f"/api/projects/default/candidates/{candidate['id']}/preview", params=params).json()
    second = client.post(f"/api/projects/default/candidates/{candidate['id']}/preview", params=params).json()
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
    assert first["canonical_input"]["input_schema_version"] == "candidate-v2"
    assert first["canonical_input"]["heat_time_basis"] == "line_speed"
    atomic_result = client.post(f"/api/projects/default/candidates/{candidate['id']}/predict", params={"expected_revision": candidate["revision"]}).json()
    detailed = atomic_result["prediction"]
    assert detailed["mode"] == "detailed"
    assert detailed["response_curve"] is None
    curve = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/response-curve",
        params={**params, "target": "TS", "variable": "composition.C", "points": 9},
    ).json()
    assert curve["target"] == "TS"
    assert curve["variable"]["id"] == "composition.C"
    assert len(curve["points"]) == 9
    stage_payload = _payload("工程温度")
    stage_payload["inputs"]["heat_pattern"][1]["stage_name"] = "加熱1"
    stage_candidate = client.post("/api/projects/default/candidates", json=stage_payload).json()
    stage_curve = client.get(
        f"/api/projects/default/candidates/{stage_candidate['id']}/response-curve",
        params={"expected_revision": stage_candidate["revision"], "target": "TS", "variable": "heat.stage_temperature_c", "stage_name": "加熱1", "stage_position_m": 480.6667, "points": 5},
    )
    assert stage_curve.status_code == 200
    assert stage_curve.json()["variable"]["label"] == "加熱1 温度"
    point_time = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/response-curve",
        params={**params, "target": "TS", "variable": "heat.1.time_min", "points": 5},
    )
    assert point_time.status_code == 422
    assert "ラインスピード" in point_time.json()["message"]
    blank_stage = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/response-curve",
        params={**params, "target": "TS", "variable": "heat.stage_temperature_c", "stage_name": "   ", "stage_position_m": 10, "points": 5},
    )
    assert blank_stage.status_code == 422
    indexed_temperature = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/response-curve",
        params={**params, "target": "TS", "variable": "heat.1.temperature_c", "points": 5},
    )
    assert indexed_temperature.status_code == 422
    assert atomic_result["snapshot"]["payload"]["prediction"] == detailed
    similar = client.get(
        f"/api/projects/default/candidates/{candidate['id']}/similar",
        params=params,
    ).json()
    assert len(similar) == 6
    assert {item["layer"] for item in similar} == {"historical"}
    assert {item["source_scope"] for item in similar} == {"project_reference_data"}
    assert all({"composition", "metallurgy", "process", "heat_pattern"} == set(item["components"]) for item in similar)


def test_snapshot_is_immutable_after_candidate_edit(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("固定化テスト")).json()
    snapshot = client.post(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()
    original = deepcopy(snapshot["payload"])
    changed = _payload("編集後")
    changed["inputs"]["process"]["ls_mpm"] = 145
    assert client.put(f"/api/projects/default/candidates/{candidate['id']}", json={**changed, "expected_revision": candidate["revision"]}).status_code == 200
    stored = client.get(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()
    assert stored[0]["payload"] == original
    assert stored[0]["payload"]["candidate_id"] == candidate["id"]
    assert stored[0]["payload"]["raw_candidate"]["name"] == "固定化テスト"
    assert "feature_vector" in stored[0]["payload"]["canonical_input"]
    assert stored[0]["payload"]["canonical_input"]["heat_time_basis"] == "line_speed"
    assert "TS" in stored[0]["payload"]["canonical_input"]["normalized_feature_vectors"]
    provenance = stored[0]["payload"]["provenance"]
    assert provenance["model"]["version"]
    assert provenance["feature_pipeline"]["version"]
    assert provenance["training_data"]["source_sha256"]
    assert provenance["similarity"]["version"]


def test_candidate_provenance_is_typed_persisted_and_immutable(client) -> None:
    source = client.post("/api/projects/default/candidates", json=_payload("コピー元")).json()
    copied_payload = _payload("コピー先")
    copied_payload["provenance"] = {
        "source_kind": "copy",
        "source_ref": {
            "project_id": "default",
            "candidate_id": source["id"],
            "candidate_revision": source["revision"],
        },
    }
    copied = client.post("/api/projects/default/candidates", json=copied_payload)
    assert copied.status_code == 201
    assert copied.json()["provenance"] == copied_payload["provenance"]

    renamed = deepcopy(copied_payload)
    renamed["name"] = "名前だけ変更"
    renamed["expected_revision"] = copied.json()["revision"]
    assert client.put(
        f"/api/projects/default/candidates/{copied.json()['id']}", json=renamed
    ).status_code == 200

    rewritten = deepcopy(renamed)
    rewritten["provenance"] = {"source_kind": "direct", "source_ref": None}
    rejected = client.put(
        f"/api/projects/default/candidates/{copied.json()['id']}", json=rewritten
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "candidate_provenance_immutable"
    assert "作成元は変更できません" in rejected.json()["message"]

    missing_source = deepcopy(copied_payload)
    missing_source["provenance"]["source_ref"]["candidate_id"] = "missing"
    invalid = client.post("/api/projects/default/candidates", json=missing_source)
    assert invalid.status_code == 422
    assert "コピー元候補が見つかりません" in invalid.json()["message"]

    stale_source = deepcopy(copied_payload)
    stale_source["provenance"]["source_ref"]["candidate_revision"] = source["revision"] + 1
    invalid_revision = client.post("/api/projects/default/candidates", json=stale_source)
    assert invalid_revision.status_code == 422
    assert "revisionが一致しません" in invalid_revision.json()["message"]


def test_snapshot_source_can_be_deep_linked_only_inside_its_project(client) -> None:
    candidate = client.post("/api/projects/default/candidates", json=_payload("保存元")).json()
    snapshot = client.post(
        f"/api/projects/default/candidates/{candidate['id']}/snapshots"
    ).json()
    opened = client.get(f"/api/projects/default/snapshots/{snapshot['id']}")
    assert opened.status_code == 200
    assert opened.json()["candidate_id"] == candidate["id"]
    assert client.get(
        f"/api/projects/hot-rolling-default/snapshots/{snapshot['id']}"
    ).status_code == 404


def test_archived_copy_source_remains_resolvable(client) -> None:
    source = client.post("/api/projects/default/candidates", json=_payload("archive元")).json()
    client.post(f"/api/projects/default/candidates/{source['id']}/snapshots")
    archived = client.delete(
        f"/api/projects/default/candidates/{source['id']}",
        params={"expected_revision": source["revision"]},
    )
    assert archived.status_code == 204
    source_after_archive = client.get(
        f"/api/projects/default/candidates/{source['id']}",
        params={"include_archived": True},
    ).json()
    assert source_after_archive["archived_at"] is not None

    copied_payload = _payload("archive元から複製")
    copied_payload["provenance"] = {
        "source_kind": "copy",
        "source_ref": {
            "project_id": "default",
            "candidate_id": source["id"],
            "candidate_revision": source_after_archive["revision"],
        },
    }
    copied = client.post("/api/projects/default/candidates", json=copied_payload)
    assert copied.status_code == 201


def test_electron_file_origin_is_allowed_without_credentials(client) -> None:
    response = client.options(
        "/api/health",
        headers={"Origin": "null", "Access-Control-Request-Method": "GET"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "null"


def test_local_web_origin_allows_parallel_development_ports(client) -> None:
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://127.0.0.1:5212",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5212"


def test_quality_and_lineage(client) -> None:
    quality = client.get("/api/projects/default/quality").json()
    assert quality["total"] == 36
    assert quality["by_category"]["関連ファイル欠損"] == 19
    assert quality["reference_scenarios"] == quality["issues"]
    assert quality["detected_total"] == len(quality["detected_issues"])
    assert {issue["issue_type"] for issue in quality["detected_issues"]} <= {"missing_key", "orphan_entity", "duplicate_key", "invalid_reference"}
    assert {
        "issue_id", "source_sheet", "entity_key", "detail", "focus_entity_key",
        "related_entity_keys", "missing_reference_key", "suggested_view",
    } <= set(quality["detected_issues"][0])
    destinations = {issue["issue_type"]: issue for issue in quality["detected_issues"]}
    assert destinations["duplicate_key"]["suggested_view"] == "lineage"
    assert destinations["orphan_entity"]["focus_entity_key"]
    assert destinations["invalid_reference"]["missing_reference_key"]
    lineage = client.get("/api/projects/default/lineage/AN-00001")
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
    index = client.get("/api/projects/default/lineage", params={"query": "AN-00001"}).json()
    assert index["relation_rows"] > 1_800
    assert index["total_entities"] > index["relation_rows"]
    assert index["items"][0]["key"] == "AN-00001"
    assert index["items"][0]["family"]
    assert index["items"][0]["route"]
    assert index["items"][0]["peak_temperature_c"] > 0
    assert index["items"][0]["observation_summary"]
    assert client.get("/api/projects/default/lineage", params={"query": index["items"][0]["family"], "entity_type": "焼鈍"}).json()["items"]
    isolated = client.get("/api/projects/default/lineage/CR-00010")
    assert isolated.status_code == 200
    assert isolated.json()["node"]["entity_type"] == "冷延"
    assert isolated.json()["graph"]["relation_row_count"] == 0
    assert isolated.json()["candidate_eligible"] is False
    invalid_key = next(
        issue["entity_key"]
        for issue in client.get("/api/projects/default/quality").json()["detected_issues"]
        if issue["issue_type"] == "invalid_reference"
    )
    invalid_index = client.get("/api/projects/default/lineage", params={"query": invalid_key}).json()
    assert invalid_index["items"] == [{"key": invalid_key, "entity_type": "熱延引張", "has_issue": True}]
    invalid = client.get(f"/api/projects/default/lineage/{invalid_key}")
    assert invalid.status_code == 200
    assert invalid.json()["node"]["missing_source"] is True


def test_lineage_graph_can_expand_beyond_the_initial_node_limit(client) -> None:
    assert client.get("/api/projects/default/lineage/AN-00001", params={"limit": 0}).status_code == 422
    assert client.get("/api/projects/default/lineage/AN-00001", params={"limit": 201}).status_code == 422
    initial = client.get("/api/projects/default/lineage/AN-00001", params={"limit": 1})
    assert initial.status_code == 200
    initial_graph = initial.json()["graph"]
    assert initial_graph["node_limit"] == 1
    assert initial_graph["visible_node_count"] == 1
    assert initial_graph["nodes"][0]["key"] == "AN-00001"
    assert initial_graph["total_node_count"] > 1
    assert initial_graph["has_more"] is True
    assert initial_graph["omitted_node_count"] == initial_graph["total_node_count"] - 1

    expanded = client.get("/api/projects/default/lineage/AN-00001", params={"limit": 200})
    assert expanded.status_code == 200
    expanded_graph = expanded.json()["graph"]
    assert expanded_graph["visible_node_count"] == expanded_graph["total_node_count"]
    assert expanded_graph["has_more"] is False
    assert expanded_graph["omitted_node_count"] == 0


def test_lineage_keeps_hot_rolled_and_annealed_observations_separate(client) -> None:
    payload = client.get("/api/projects/default/lineage/AN-00003").json()
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


def test_lineage_keeps_out_of_range_observations_without_mutating_raw_values(client) -> None:
    response = client.get("/api/projects/default/lineage/HT-00024")
    assert response.status_code == 200
    observation = next(item for item in response.json()["node"]["connected_observations"] if item["id"] == "HT-00024")
    assert observation["outputs"]["TS[MPa]"] > 5_000
    assert "output_warnings" not in observation

    incompatible = client.post(
        "/api/projects/hot-rolling-default/lineage/AN-00001/candidate",
    )
    assert incompatible.status_code == 422
    incompatible_detail = client.get("/api/projects/hot-rolling-default/lineage/AN-00001").json()
    assert incompatible_detail["candidate_eligible"] is False

    compatible_detail = client.get("/api/projects/hot-rolling-default/lineage/HR-00001").json()
    assert compatible_detail["candidate_eligible"] is True
    compatible = client.post("/api/projects/hot-rolling-default/lineage/HR-00001/candidate")
    assert compatible.status_code == 201
    assert compatible.json()["project_id"] == "hot-rolling-default"
