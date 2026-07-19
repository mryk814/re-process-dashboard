from io import BytesIO

from openpyxl import Workbook
import numpy as np

from material_workbench.feature_pipeline import build_feature_bundle
from material_workbench.schemas import CandidateInput
from material_workbench.services import candidate_from_lineage, import_candidates_xlsx


def _screening_body(candidate_id: str) -> dict:
    return {
        "base_candidate_id": candidate_id,
        "samples": 48,
        "target": "TS",
        "target_value": 500,
        "variables": {
            "composition.C": {"mode": "range", "min": 0.04, "max": 0.12},
            "line_speed_m_min": {"mode": "range", "min": 80, "max": 130},
            "coating": {"mode": "list", "values": ["なし", "GI", "GA"]},
            "thickness_mm": {"mode": "fixed", "value": 1.4},
        },
    }


def test_project_metadata_persists(client) -> None:
    payload = {"name": "DP検討", "description": "焼鈍条件の比較", "purpose": "TS 500 MPa", "task_id": "annealed-properties-v1", "target_values": {"TS": 500.0, "EL": 40.0}, "notes": "初回"}
    updated = client.put("/api/project", json=payload)
    assert updated.status_code == 200
    assert client.get("/api/project").json()["target_values"] == {"TS": 500.0, "EL": 40.0}


def test_latin_hypercube_is_deterministic_bounded_and_convertible(client) -> None:
    candidate = client.get("/api/candidates").json()[0]
    first = client.post("/api/screening", json=_screening_body(candidate["id"])).json()
    second = client.post("/api/screening", json=_screening_body(candidate["id"])).json()
    assert len(first["points"]) == 48
    assert [point["inputs"] for point in first["points"]] == [point["inputs"] for point in second["points"]]
    assert all(0.04 <= point["inputs"]["composition.C"] <= 0.12 for point in first["points"])
    assert {point["inputs"]["coating"] for point in first["points"]} == {"なし", "GI", "GA"}
    created = client.post(f"/api/screening/{first['id']}/points/0/candidate")
    assert created.status_code == 201
    assert created.json()["name"].startswith("Screen")
    assert first["base_candidate_id"] == candidate["id"]
    assert first["model_provenance"]["model"]["version"]
    assert client.get(f"/api/screening/{first['id']}").json()["base_canonical_input"] == first["base_canonical_input"]
    assert any(run["id"] == first["id"] for run in client.get("/api/screening").json())


def test_screening_rejects_invalid_field_values_and_empty_candidate_set(client) -> None:
    candidate = client.get("/api/candidates").json()[0]
    invalid = _screening_body(candidate["id"])
    invalid["variables"]["coating"] = {"mode": "fixed", "value": "BAD"}
    assert client.post("/api/screening", json=invalid).status_code == 422
    for item in client.get("/api/candidates").json():
        assert client.delete(f"/api/candidates/{item['id']}").status_code == 204
    no_base = _screening_body(candidate["id"])
    no_base["base_candidate_id"] = None
    assert client.post("/api/screening", json=no_base).status_code == 404


def test_lineage_candidate_actuals_and_snapshot_restore(client) -> None:
    lineage_candidate = client.post("/api/lineage/AN-00001/candidate")
    assert lineage_candidate.status_code == 201
    candidate = lineage_candidate.json()
    assert len(candidate["heat_pattern"]) >= 2
    actual = client.post(f"/api/candidates/{candidate['id']}/actuals", json={"property": "TS", "mean": 505.2, "std": 4.2, "replicates": 3, "unit": "MPa", "experiment_no": "EXP-01", "measured_at": "2026-07-20", "note": "確認用"})
    assert actual.status_code == 201
    assert actual.json()["snapshot_id"]
    changed = {key: value for key, value in candidate.items() if key not in {"id", "project_id", "created_at", "updated_at"}}
    changed["line_speed_m_min"] = candidate["line_speed_m_min"] + 30
    assert client.put(f"/api/candidates/{candidate['id']}", json=changed).status_code == 200
    comparison = client.get(f"/api/candidates/{candidate['id']}/prediction-vs-actual").json()
    assert comparison["actuals"][0]["mean"] == 505.2
    assert comparison["comparisons"][0]["snapshot_id"] == actual.json()["snapshot_id"]
    assert comparison["comparisons"][0]["prediction"]["canonical_input"]["process"]["line_speed_m_min"] != changed["line_speed_m_min"]
    assert comparison["comparisons"][0]["provenance"]["training_data"]["source_sha256"]
    assert client.post(f"/api/candidates/{candidate['id']}/actuals", json={"property": "TS", "mean": 500, "unit": "%"}).status_code == 422
    snapshot = client.post(f"/api/candidates/{candidate['id']}/snapshots").json()
    restored = client.post(f"/api/snapshots/{snapshot['id']}/restore")
    assert restored.status_code == 201
    assert restored.json()["id"] != candidate["id"]


def test_lineage_candidate_preserves_stage_order_and_boundaries(client) -> None:
    expected = candidate_from_lineage(client.app.state.data, "AN-00009")
    response = client.post("/api/lineage/AN-00009/candidate")
    assert response.status_code == 201
    payload = {key: value for key, value in response.json().items() if key not in {"id", "project_id", "created_at", "updated_at"}}
    actual = CandidateInput.model_validate(payload)
    assert actual.heat_pattern == expected.heat_pattern
    assert any(point.segment_start for point in actual.heat_pattern)
    assert all(right.time_s > left.time_s for left, right in zip(actual.heat_pattern, actual.heat_pattern[1:]))


def test_candidate_excel_import_and_exports(client) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["name", "C", "Si", "Mn", "thickness_mm", "line_speed_m_min", "coating", "time_s_1", "temperature_c_1", "segment_start_1", "time_s_2", "temperature_c_2", "segment_start_2", "time_s_3", "temperature_c_3", "segment_start_3"])
    sheet.append(["Excel候補", 0.08, 0.3, 1.5, 1.4, 100, "GI", 0, 25, False, 300, 810, True, 650, 120, False])
    buffer = BytesIO()
    workbook.save(buffer)
    response = client.post("/api/candidates/import", files={"file": ("candidates.xlsx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 200
    assert response.json()["created"] == 1
    exported = client.get("/api/candidates/export.xlsx")
    assert exported.status_code == 200 and exported.content[:2] == b"PK"
    round_tripped, errors = import_candidates_xlsx(exported.content)
    assert not errors
    source = CandidateInput.model_validate({key: value for key, value in response.json()["candidates"][0].items() if key not in {"id", "project_id", "created_at", "updated_at"}})
    restored = next(candidate for candidate in round_tripped if candidate.name == source.name)
    assert restored.model_dump() == source.model_dump()
    defaults = client.app.state.runtime.composition_defaults
    assert np.allclose(build_feature_bundle(restored, defaults).values, build_feature_bundle(source, defaults).values)
    quality = client.get("/api/quality/export.csv")
    assert quality.status_code == 200 and "issue_id" in quality.content.decode("utf-8-sig")


def test_candidate_delete_cascades_its_actuals_and_snapshots(client) -> None:
    candidate = client.post("/api/candidates", json={"name": "削除検証", "composition": {"C": 0.08}, "thickness_mm": 1.4, "line_speed_m_min": 100, "coating": "GI", "heat_pattern": [{"time_s": 0, "temperature_c": 25}, {"time_s": 300, "temperature_c": 810}]}).json()
    actual = client.post(f"/api/candidates/{candidate['id']}/actuals", json={"property": "TS", "mean": 500, "unit": "MPa"}).json()
    assert client.delete(f"/api/candidates/{candidate['id']}").status_code == 204
    assert client.app.state.store.get_snapshot(actual["snapshot_id"]) is None
    assert client.app.state.store.list_actuals(candidate["id"]) == []
