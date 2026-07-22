from copy import deepcopy
from io import BytesIO

from openpyxl import Workbook
import numpy as np

from material_workbench.feature_pipeline import build_feature_bundle
from material_workbench.schemas import CandidateInput
from material_workbench.services import candidate_from_lineage, import_candidates_xlsx


def _screening_body(candidate: dict) -> dict:
    return {
        "base_candidate_id": candidate["id"],
        "base_inputs": candidate["inputs"],
        "samples": 48,
        "target": "TS",
        "target_value": 500,
        "variables": {
            "composition.C": {"mode": "range", "min": 0.04, "max": 0.12},
            "process.ls_mpm": {"mode": "range", "min": 80, "max": 130},
        },
    }


def test_project_metadata_persists(client) -> None:
    payload = {"name": "DP検討", "description": "焼鈍条件の比較", "purpose": "TS 500 MPa", "task_id": "annealed-properties-v1", "target_values": {"TS": 500.0, "EL": 40.0}, "notes": "初回"}
    updated = client.put("/api/project", json=payload)
    assert updated.status_code == 200
    assert client.get("/api/project").json()["target_values"] == {"TS": 500.0, "EL": 40.0}


def test_latin_hypercube_is_deterministic_bounded_and_convertible(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    first = client.post("/api/screening", json=_screening_body(candidate)).json()
    second = client.post("/api/screening", json=_screening_body(candidate)).json()
    assert len(first["points"]) == 48
    assert [point["inputs"] for point in first["points"]] == [point["inputs"] for point in second["points"]]
    assert all(0.04 <= point["inputs"]["composition.C"] <= 0.12 for point in first["points"])
    assert all(80 <= point["inputs"]["process.ls_mpm"] <= 130 for point in first["points"])
    created = client.post(f"/api/screening/{first['id']}/candidates", json={"point_indices": [0]})
    assert created.status_code == 201
    assert created.json()["candidates"][0]["name"].startswith("Screen")
    assert first["base_candidate_id"] == candidate["id"]
    assert first["base_inputs"] == candidate["inputs"]
    assert first["model_provenance"]["model"]["version"]
    assert first["score_contract"] == {
        "version": "screening-score/v1",
        "preference": "lower_is_better",
        "direction": "at_least",
        "target_value": 500.0,
        "probability_available": True,
        "fallback": "directional_shortfall",
        "display_label": "目標以上ほど有望",
    }
    assert all(point["prediction"]["goal_direction"] == "at_least" for point in first["points"])
    assert [point["score"] for point in first["representative_points"]] == sorted(
        point["score"] for point in first["representative_points"]
    )
    assert client.get(f"/api/screening/{first['id']}").json()["base_canonical_input"] == first["base_canonical_input"]
    assert any(run["id"] == first["id"] for run in client.get("/api/screening").json())


def test_screening_uses_unsaved_base_inputs_without_updating_the_candidate(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    payload = _screening_body(candidate)
    payload["variables"] = {"composition.C": {"mode": "range", "min": 0.04, "max": 0.12}}
    payload["base_inputs"] = deepcopy(candidate["inputs"])
    payload["base_inputs"]["process"]["ls_mpm"] = 117.25
    payload["base_inputs"]["heat_pattern"][0]["stage_name"] = "探索用の工程名"

    response = client.post("/api/screening", json=payload)

    assert response.status_code == 201, response.text
    points = response.json()["points"]
    assert all(point["candidate"]["inputs"]["process"]["ls_mpm"] == 117.25 for point in points)
    assert all(point["candidate"]["inputs"]["heat_pattern"][0]["stage_name"] == "探索用の工程名" for point in points)
    persisted = client.get(f"/api/projects/default/candidates/{candidate['id']}").json()
    assert persisted["inputs"] == candidate["inputs"]


def test_screening_rejects_invalid_field_values_and_empty_candidate_set(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    invalid = _screening_body(candidate)
    invalid["variables"]["process.removed_field"] = {"mode": "fixed", "value": 999}
    assert client.post("/api/screening", json=invalid).status_code == 422
    for item in client.get("/api/projects/default/candidates").json():
        assert client.delete(f"/api/projects/default/candidates/{item['id']}?expected_revision={item['revision']}").status_code == 204
    no_base = _screening_body(candidate)
    no_base["base_candidate_id"] = None
    assert client.post("/api/screening", json=no_base).status_code == 422


def test_screening_without_target_uses_support_distance_contract(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    payload = _screening_body(candidate)
    payload["target_value"] = None

    response = client.post("/api/screening", json=payload)

    assert response.status_code == 201
    result = response.json()
    assert result["score_contract"]["fallback"] == "support_distance"
    assert all(point["score"] == point["support"]["distance"] for point in result["points"])


def test_lineage_candidate_actuals_and_snapshot_restore(client) -> None:
    lineage_candidate = client.post("/api/lineage/AN-00001/candidate")
    assert lineage_candidate.status_code == 201
    candidate = lineage_candidate.json()
    assert len(candidate["inputs"]["heat_pattern"]) >= 2
    assert candidate["provenance"]["source_ref"]["data_source_digest"] == client.get("/api/bootstrap").json()["meta"]["source_sha256"]
    actual = client.post(f"/api/projects/default/candidates/{candidate['id']}/actuals", params={"expected_revision": candidate["revision"]}, json={"property": "TS", "mean": 505.2, "std": 4.2, "replicates": 3, "unit": "MPa", "experiment_no": "EXP-01", "measured_at": "2026-07-20", "note": "確認用"})
    assert actual.status_code == 201
    assert actual.json()["snapshot_id"]
    changed = {key: value for key, value in candidate.items() if key in {"name", "inputs", "provenance"}}
    changed["expected_revision"] = candidate["revision"]
    changed["inputs"]["process"]["ls_mpm"] = candidate["inputs"]["process"]["ls_mpm"] + 30
    assert client.put(f"/api/projects/default/candidates/{candidate['id']}", json=changed).status_code == 200
    comparison = client.get(f"/api/projects/default/candidates/{candidate['id']}/prediction-vs-actual").json()
    assert comparison["actuals"][0]["mean"] == 505.2
    assert comparison["comparisons"][0]["snapshot_id"] == actual.json()["snapshot_id"]
    assert comparison["comparisons"][0]["prediction"]["canonical_input"]["process"]["ls_mpm"] != changed["inputs"]["process"]["ls_mpm"]
    assert comparison["comparisons"][0]["provenance"]["training_data"]["source_sha256"]
    assert client.post(f"/api/projects/default/candidates/{candidate['id']}/actuals", params={"expected_revision": candidate["revision"] + 1}, json={"property": "TS", "mean": 500, "unit": "%"}).status_code == 422
    snapshot = client.post(f"/api/projects/default/candidates/{candidate['id']}/snapshots").json()
    restored = client.post(f"/api/projects/default/snapshots/{snapshot['id']}/restore")
    assert restored.status_code == 201
    assert restored.json()["id"] != candidate["id"]


def test_lineage_candidate_preserves_stage_order_and_boundaries(client) -> None:
    expected = candidate_from_lineage(client.app.state.data, "AN-00009")
    response = client.post("/api/lineage/AN-00009/candidate")
    assert response.status_code == 201
    payload = {key: value for key, value in response.json().items() if key not in {"id", "project_id", "created_at", "updated_at"}}
    actual = CandidateInput.model_validate(payload)
    assert actual.inputs.heat_pattern == expected.inputs.heat_pattern
    assert actual.inputs.heat_pattern is not None
    assert any(point.segment_start for point in actual.inputs.heat_pattern)
    assert all(right.time_s > left.time_s for left, right in zip(actual.inputs.heat_pattern, actual.inputs.heat_pattern[1:]))


def test_hot_lineage_candidate_uses_hot_rolling_inputs(client) -> None:
    expected = candidate_from_lineage(client.app.state.data, "HR-00001")
    assert expected.inputs.heat_pattern is None
    assert set(expected.inputs.process) == {
        "soaking_temperature_c",
        "finish_temperature_c",
        "entry_thickness_mm",
        "exit_thickness_mm",
        "hold_temperature_c",
        "hold_time_min",
    }
    assert expected.provenance.source_ref.entity_type == "hot_rolling"


def test_candidate_excel_import_and_exports(client) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["name", "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N", "ls_mpm", "time_s_1", "temperature_c_1", "segment_start_1", "stage_name_1", "time_s_2", "temperature_c_2", "segment_start_2", "stage_name_2", "time_s_3", "temperature_c_3", "segment_start_3", "stage_name_3"])
    sheet.append(["Excel候補", 0.08, 0.3, 1.5, 0.01, 0.005, 0.04, 0.0, 0.02, 0.01, 0.0, 0.01, 0.0003, 0.002, 0.004, 100, 0, 25, False, "加熱", 300, 810, True, "均熱", 650, 120, False, "冷却"])
    buffer = BytesIO()
    workbook.save(buffer)
    response = client.post("/api/projects/default/candidates/import", files={"file": ("candidates.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 200
    assert response.json()["created"] == 1
    exported = client.get("/api/projects/default/candidates/export.xlsx")
    assert exported.status_code == 200 and exported.content[:2] == b"PK"
    round_tripped, errors = import_candidates_xlsx(exported.content)
    assert not errors
    source = CandidateInput.model_validate({key: value for key, value in response.json()["candidates"][0].items() if key not in {"id", "project_id", "created_at", "updated_at"}})
    restored = next(candidate for candidate in round_tripped if candidate.name == source.name)
    assert restored.model_dump() == source.model_dump()
    defaults = client.app.state.task_registry.runtime_for("annealed-properties-v1").composition_defaults
    assert np.allclose(build_feature_bundle(restored, defaults).values, build_feature_bundle(source, defaults).values)
    quality = client.get("/api/quality/export.csv")
    quality_csv = quality.content.decode("utf-8-sig")
    assert quality.status_code == 200 and "issue_id" in quality_csv
    assert "focus_entity_key" in quality_csv and "suggested_view" in quality_csv
    assert quality.headers["content-type"].startswith("text/csv")


def test_candidate_delete_preserves_actuals_and_snapshots(client) -> None:
    source = client.get("/api/projects/default/candidates").json()[0]
    payload = {key: value for key, value in source.items() if key not in {"id", "project_id", "created_at", "updated_at"}}
    payload["name"] = "削除検証"
    candidate = client.post("/api/projects/default/candidates", json=payload).json()
    actual = client.post(f"/api/projects/default/candidates/{candidate['id']}/actuals", params={"expected_revision": candidate["revision"]}, json={"property": "TS", "mean": 500, "unit": "MPa"}).json()
    assert client.delete(f"/api/projects/default/candidates/{candidate['id']}?expected_revision={candidate['revision']}").status_code == 204
    assert client.get(f"/api/projects/default/candidates/{candidate['id']}").status_code == 404
    assert client.get(f"/api/projects/default/candidates/{candidate['id']}?include_archived=true").json()["archived_at"] is not None
    assert client.get(f"/api/projects/default/candidates/{candidate['id']}/actuals").status_code == 200
    assert client.app.state.store.get_snapshot(actual["snapshot_id"]) is not None
    assert len(client.app.state.store.list_actuals(candidate["id"])) == 1
