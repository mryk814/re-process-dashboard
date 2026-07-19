from io import BytesIO

from openpyxl import Workbook


def _candidate(name: str) -> dict:
    return {
        "name": name,
        "composition": {"C": 0.08, "Si": 0.3, "Mn": 1.5},
        "thickness_mm": 1.4,
        "line_speed_m_min": 103.0,
        "coating": "GI",
        "heat_pattern": [
            {"time_s": 0, "temperature_c": 25},
            {"time_s": 300, "temperature_c": 810},
            {"time_s": 650, "temperature_c": 120},
        ],
    }


def _project(name: str) -> dict:
    return {
        "name": name,
        "description": "独立した検討",
        "purpose": "プロジェクト分離の確認",
        "task_id": "annealed-properties-v1",
        "target_values": {"TS": 500},
        "notes": "",
    }


def _xlsx_candidate(name: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["name", "C", "Si", "Mn", "time_s_1", "temperature_c_1", "time_s_2", "temperature_c_2"])
    sheet.append([name, 0.08, 0.3, 1.5, 0, 25, 300, 810])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_project_crud_preserves_default_and_isolates_candidates_and_screening(client) -> None:
    default = client.get("/api/project").json()
    created = client.post("/api/projects", json=_project("新規プロジェクト"))
    assert created.status_code == 201
    project = created.json()
    assert project["id"] != "default"
    assert {item["id"] for item in client.get("/api/projects").json()} >= {"default", project["id"]}
    assert client.get(f"/api/projects/{project['id']}").json()["name"] == "新規プロジェクト"

    changed = _project("更新後プロジェクト")
    assert client.put(f"/api/projects/{project['id']}", json=changed).json()["name"] == "更新後プロジェクト"
    assert client.get("/api/project").json()["name"] == default["name"]
    assert client.get("/api/projects/missing").status_code == 404

    candidate = client.post(f"/api/candidates?project_id={project['id']}", json=_candidate("P2候補"))
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]
    assert [item["id"] for item in client.get(f"/api/candidates?project_id={project['id']}").json()] == [candidate_id]
    assert candidate_id not in {item["id"] for item in client.get("/api/candidates").json()}

    screening_body = {
        "base_candidate_id": candidate_id,
        "samples": 48,
        "target": "TS",
        "target_value": 500,
        "variables": {"composition.C": {"mode": "range", "min": 0.06, "max": 0.1}},
    }
    run = client.post(f"/api/screening?project_id={project['id']}", json=screening_body)
    assert run.status_code == 201
    run_id = run.json()["id"]
    assert [item["id"] for item in client.get(f"/api/screening?project_id={project['id']}").json()] == [run_id]
    assert client.get("/api/screening").json() == []
    assert client.get(f"/api/screening/{run_id}").status_code == 404
    assert client.post("/api/screening", json=screening_body).status_code == 404


def test_candidate_limit_is_enforced_for_every_creation_route(client) -> None:
    project = client.post("/api/projects", json=_project("上限確認")).json()
    project_id = project["id"]
    base = client.post(f"/api/candidates?project_id={project_id}", json=_candidate("基準")).json()
    snapshot = client.post(f"/api/candidates/{base['id']}/snapshots").json()
    screening = client.post(
        f"/api/screening?project_id={project_id}",
        json={
            "base_candidate_id": base["id"],
            "samples": 48,
            "target": "TS",
            "target_value": 500,
            "variables": {"composition.C": {"mode": "range", "min": 0.06, "max": 0.1}},
        },
    ).json()
    for index in range(2, 11):
        assert client.post(f"/api/candidates?project_id={project_id}", json=_candidate(f"候補{index}")).status_code == 201
    assert len(client.get(f"/api/candidates?project_id={project_id}").json()) == 10

    direct = client.post(f"/api/candidates?project_id={project_id}", json=_candidate("11件目"))
    assert direct.status_code == 409 and "最大10件" in direct.json()["detail"]
    assert client.post(f"/api/lineage/AN-00001/candidate?project_id={project_id}").status_code == 409
    assert client.post(f"/api/screening/{screening['id']}/points/0/candidate?project_id={project_id}").status_code == 409
    assert client.post(f"/api/snapshots/{snapshot['id']}/restore").status_code == 409
    imported = client.post(
        f"/api/candidates/import?project_id={project_id}",
        files={"file": ("candidate.xlsx", _xlsx_candidate("Excel候補"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert imported.status_code == 409
    assert len(client.get(f"/api/candidates?project_id={project_id}").json()) == 10
