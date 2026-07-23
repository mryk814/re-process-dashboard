from io import BytesIO
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from openpyxl import Workbook

from material_workbench.app import _AppResources, create_app
from material_workbench.store import MAX_CANDIDATES_PER_PROJECT, Store

ELEMENTS = ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")
SOURCE = Path(__file__).parents[2] / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"


def _candidate(name: str) -> dict:
    return {
        "name": name,
        "inputs": {
            "composition": {**{key: 0.0 for key in ELEMENTS}, "C": 0.08, "Si": 0.3, "Mn": 1.5},
            "process": {"ls_mpm": 103.0},
            "categorical": {},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 25},
                {"time_s": 300, "temperature_c": 810},
                {"time_s": 650, "temperature_c": 120},
            ],
        },
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
    composition = {**{key: 0.0 for key in ELEMENTS}, "C": 0.08, "Si": 0.3, "Mn": 1.5}
    sheet.append(["name", *ELEMENTS, "ls_mpm", "time_s_1", "temperature_c_1", "time_s_2", "temperature_c_2"])
    sheet.append([name, *[composition[key] for key in ELEMENTS], 103, 0, 25, 300, 810])
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def test_project_crud_preserves_default_and_isolates_candidates_and_screening(client) -> None:
    default = client.get("/api/projects/default").json()
    created = client.post("/api/projects", json=_project("新規プロジェクト"))
    assert created.status_code == 201
    project = created.json()
    assert project["id"] != "default"
    assert {item["id"] for item in client.get("/api/projects").json()} >= {"default", project["id"]}
    assert client.get(f"/api/projects/{project['id']}").json()["name"] == "新規プロジェクト"

    changed = _project("更新後プロジェクト")
    changed["heat_stage_positions_m"] = {"加熱1": 42.5}
    updated = client.put(f"/api/projects/{project['id']}", json=changed).json()
    assert updated["name"] == "更新後プロジェクト"
    assert updated["heat_stage_positions_m"] == {"加熱1": 42.5}
    assert client.get(f"/api/projects/{project['id']}").json()["heat_stage_positions_m"] == {"加熱1": 42.5}
    assert client.get("/api/projects/default").json()["name"] == default["name"]
    assert client.get("/api/projects/missing").status_code == 404

    candidate = client.post(f"/api/projects/{project['id']}/candidates", json=_candidate("P2候補"))
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]
    assert [item["id"] for item in client.get(f"/api/projects/{project['id']}/candidates").json()] == [candidate_id]
    assert candidate_id not in {item["id"] for item in client.get("/api/projects/default/candidates").json()}

    screening_body = {
        "base_candidate_id": candidate_id,
        "base_inputs": candidate.json()["inputs"],
        "samples": 48,
        "target": "TS",
        "target_value": 500,
        "secondary_targets": {"YS": 350},
        "variables": {"composition.C": {"mode": "range", "min": 0.06, "max": 0.1}},
    }
    run = client.post(f"/api/screening?project_id={project['id']}", json=screening_body)
    assert run.status_code == 201
    assert set(run.json()["points"][0]["predictions"]) == {"TS", "YS", "EL", "lambda"}
    assert "YS" in run.json()["points"][0]["secondary_goal_evaluations"]
    run_id = run.json()["id"]
    batch = client.post(f"/api/screening/{run_id}/candidates?project_id={project['id']}", json={"point_indices": [0, 1]})
    assert batch.status_code == 201
    assert len(batch.json()["candidates"]) == 2
    assert {item["provenance"]["source_ref"]["point_index"] for item in batch.json()["candidates"]} == {0, 1}
    duplicate = client.post(f"/api/screening/{run_id}/candidates?project_id={project['id']}", json={"point_indices": [0]})
    assert duplicate.status_code == 201
    assert duplicate.json() == {"candidates": [], "skipped_point_indices": [0]}
    assert [item["id"] for item in client.get(f"/api/screening?project_id={project['id']}").json()] == [run_id]
    assert client.get("/api/screening").json() == []
    assert client.get(f"/api/screening/{run_id}").status_code == 404
    assert client.post("/api/screening", json=screening_body).status_code == 404
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    assert client.get(f"/api/projects/{project['id']}/candidates").status_code == 404
    assert project["id"] not in {item["id"] for item in client.get("/api/projects").json()}
    assert project["project_series_id"] not in {
        item["id"] for item in client.get("/api/project-series").json()
    }

    assert client.delete("/api/projects/default").status_code == 409
    assert client.delete("/api/projects/hot-rolling-default").status_code == 409
    assert client.delete("/api/projects/missing").status_code == 404


def test_project_group_move_is_atomic_and_preserves_scientific_state(client) -> None:
    source_group = client.post(
        "/api/project-series",
        json={"name": "旧グループ", "description": ""},
    ).json()
    target_group = client.post(
        "/api/project-series",
        json={"name": "移動先グループ", "description": ""},
    ).json()
    project = client.post(
        "/api/projects",
        json={**_project("所属変更"), "project_series_id": source_group["id"]},
    ).json()
    candidate = client.post(
        f"/api/projects/{project['id']}/candidates",
        json=_candidate("固定候補"),
    ).json()
    snapshot = client.post(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/snapshots",
    ).json()
    decision = {
        "candidate_id": candidate["id"],
        "snapshot_id": snapshot["id"],
        "note": "所属変更で変わらない判断",
    }
    client.put(f"/api/projects/{project['id']}/decision", json=decision).raise_for_status()
    before = client.get(f"/api/projects/{project['id']}").json()

    moved = client.put(
        f"/api/projects/{project['id']}/group",
        json={
            "project_series_id": target_group["id"],
            "expected_project_series_id": source_group["id"],
        },
    )
    assert moved.status_code == 200, moved.text
    after = moved.json()
    assert after["project_series_id"] == target_group["id"]
    for field in (
        "task_id",
        "dataset_view_revision_id",
        "task_contract_digest",
        "model_package_ref_id",
        "model_package_manifest_digest",
        "predecessor_project_id",
        "decision_candidate_id",
        "decision_snapshot_id",
        "decision_note",
    ):
        assert after[field] == before[field]
    assert client.get(f"/api/projects/{project['id']}/candidates").json() == [candidate]
    assert client.get(f"/api/projects/{project['id']}/snapshots/{snapshot['id']}").status_code == 200
    assert client.get(f"/api/project-series/{source_group['id']}").status_code == 404

    stale = client.put(
        f"/api/projects/{project['id']}/group",
        json={
            "project_series_id": source_group["id"],
            "expected_project_series_id": source_group["id"],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "project_group_conflict"
    assert client.get(f"/api/projects/{project['id']}").json()["project_series_id"] == target_group["id"]


def test_project_group_move_keeps_nonempty_source_and_rejects_unavailable_targets(client) -> None:
    source_group = client.post(
        "/api/project-series",
        json={"name": "複数所属", "description": ""},
    ).json()
    target_group = client.post(
        "/api/project-series",
        json={"name": "有効な移動先", "description": ""},
    ).json()
    archived_group = client.post(
        "/api/project-series",
        json={"name": "終了済み", "description": ""},
    ).json()
    client.put(
        f"/api/project-series/{archived_group['id']}",
        json={"name": archived_group["name"], "description": "", "archived": True},
    ).raise_for_status()
    first = client.post(
        "/api/projects",
        json={**_project("移動対象"), "project_series_id": source_group["id"]},
    ).json()
    client.post(
        "/api/projects",
        json={**_project("残留対象"), "project_series_id": source_group["id"]},
    ).raise_for_status()

    for unavailable_group_id in (archived_group["id"], "missing-group"):
        rejected = client.put(
            f"/api/projects/{first['id']}/group",
            json={
                "project_series_id": unavailable_group_id,
                "expected_project_series_id": source_group["id"],
            },
        )
        assert rejected.status_code == 422
        assert client.get(f"/api/projects/{first['id']}").json()["project_series_id"] == source_group["id"]

    moved = client.put(
        f"/api/projects/{first['id']}/group",
        json={
            "project_series_id": target_group["id"],
            "expected_project_series_id": source_group["id"],
        },
    )
    assert moved.status_code == 200
    assert client.get(f"/api/project-series/{source_group['id']}").status_code == 200


def test_screening_accepts_hot_rolling_process_fields_from_task_definition(client) -> None:
    base = client.get("/api/projects/hot-rolling-default/candidates").json()[0]
    response = client.post(
        "/api/screening?project_id=hot-rolling-default",
        json={
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "target": "TS",
            "target_value": 520,
            "variables": {
                "process.soaking_temperature_c": {"mode": "range", "min": 1170, "max": 1190},
            },
        },
    )
    assert response.status_code == 201, response.text
    point = response.json()["points"][0]
    assert set(point["predictions"]) == {"TS"}
    assert 1170 <= point["inputs"]["process.soaking_temperature_c"] <= 1190


def test_screening_samples_only_hot_rolling_points_that_satisfy_relational_constraints(client) -> None:
    base = client.get("/api/projects/hot-rolling-default/candidates").json()[0]
    response = client.post(
        "/api/screening?project_id=hot-rolling-default",
        json={
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "target": "TS",
            "variables": {
                "process.soaking_temperature_c": {"mode": "range", "min": 880, "max": 920},
                "process.finish_temperature_c": {"mode": "range", "min": 880, "max": 920},
            },
        },
    )
    assert response.status_code == 201
    points = response.json()["points"]
    assert len(points) == 48
    assert all(
        point["inputs"]["process.finish_temperature_c"] <= point["inputs"]["process.soaking_temperature_c"]
        for point in points
    )


def test_screening_accepts_heat_pattern_point_fields_from_base_candidate(client) -> None:
    base = client.get("/api/projects/default/candidates").json()[0]
    response = client.post(
        "/api/screening",
        json={
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "target": "TS",
            "variables": {
                "heat_pattern.1.temperature_c": {"mode": "range", "min": 780, "max": 820},
                "heat_pattern.1.time_s": {"mode": "range", "min": 40, "max": 50},
            },
        },
    )
    assert response.status_code == 201, response.text
    point = response.json()["points"][0]
    assert 780 <= point["inputs"]["heat_pattern.1.temperature_c"] <= 820
    assert 40 <= point["inputs"]["heat_pattern.1.time_s"] <= 50
    assert point["candidate"]["inputs"]["heat_pattern"][1]["temperature_c"] == point["inputs"]["heat_pattern.1.temperature_c"]


def test_project_accepts_each_registered_task_and_rejects_wrong_targets(client) -> None:
    payload = _project("熱延タスク")
    payload["task_id"] = "hot-rolled-properties-v1"
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201
    definition = client.get(f"/api/projects/{response.json()['id']}/task-definition").json()
    assert definition["task_definition"]["id"] == "hot-rolled-properties-v1"
    assert {item["key"] for item in definition["task_definition"]["outputs"]} == {"TS"}

    payload["target_values"] = {"YS": 400}
    invalid = client.post("/api/projects", json=payload)
    assert invalid.status_code == 422
    assert "タスクに存在しない目標特性" in invalid.json()["message"]


def test_project_display_decimal_overrides_are_sparse_persisted_and_task_scoped(client) -> None:
    project = client.post("/api/projects", json={**_project("表示桁数"), "display_decimals": {"composition.C": 4, "output.TS": 2}}).json()
    assert project["display_decimals"] == {"composition.C": 4, "output.TS": 2}
    assert client.get(f"/api/projects/{project['id']}").json()["display_decimals"] == project["display_decimals"]

    invalid = client.put(f"/api/projects/{project['id']}", json={**_project("表示桁数"), "display_decimals": {"output.unknown": 2}})
    assert invalid.status_code == 422
    assert "タスクに存在しない表示項目" in invalid.json()["message"]


def test_candidate_limit_is_enforced_for_every_creation_route(client) -> None:
    assert MAX_CANDIDATES_PER_PROJECT == 100
    project = client.post("/api/projects", json=_project("上限確認")).json()
    project_id = project["id"]
    base = client.post(f"/api/projects/{project_id}/candidates", json=_candidate("基準")).json()
    snapshot = client.post(f"/api/projects/{project_id}/candidates/{base['id']}/snapshots").json()
    screening = client.post(
        f"/api/screening?project_id={project_id}",
        json={
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "target": "TS",
            "target_value": 500,
            "variables": {"composition.C": {"mode": "range", "min": 0.06, "max": 0.1}},
        },
    ).json()
    for index in range(2, MAX_CANDIDATES_PER_PROJECT + 1):
        assert client.post(f"/api/projects/{project_id}/candidates", json=_candidate(f"候補{index}")).status_code == 201
    assert len(client.get(f"/api/projects/{project_id}/candidates").json()) == MAX_CANDIDATES_PER_PROJECT

    direct = client.post(f"/api/projects/{project_id}/candidates", json=_candidate(f"{MAX_CANDIDATES_PER_PROJECT + 1}件目"))
    assert direct.status_code == 409 and f"最大{MAX_CANDIDATES_PER_PROJECT}件" in direct.json()["message"]
    assert client.post(f"/api/projects/{project_id}/lineage/AN-00001/candidate").status_code == 409
    assert client.post(f"/api/screening/{screening['id']}/candidates?project_id={project_id}", json={"point_indices": [0]}).status_code == 409
    assert client.post(f"/api/projects/{project_id}/snapshots/{snapshot['id']}/restore").status_code == 409
    imported = client.post(
        f"/api/projects/{project_id}/candidates/import",
        files={"file": ("candidate.xlsx", _xlsx_candidate("Excel候補"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert imported.status_code == 409
    assert len(client.get(f"/api/projects/{project_id}/candidates").json()) == MAX_CANDIDATES_PER_PROJECT


def test_project_decision_is_scoped_persisted_and_cleared_with_candidate(client) -> None:
    assert client.post(
        "/api/projects",
        json={**_project("不正な初期判断"), "decision_candidate_id": "not-yet-created"},
    ).status_code == 422
    assert client.post(
        "/api/projects",
        json={**_project("理由だけ"), "decision_note": "候補がない"},
    ).status_code == 422
    project = client.post("/api/projects", json=_project("判断記録")).json()
    other = client.post("/api/projects", json=_project("別プロジェクト")).json()
    selected = client.post(
        f"/api/projects/{project['id']}/candidates",
        json=_candidate("次実験候補"),
    ).json()
    selected_snapshot = client.post(
        f"/api/projects/{project['id']}/candidates/{selected['id']}/snapshots",
    ).json()
    foreign = client.post(
        f"/api/projects/{other['id']}/candidates",
        json=_candidate("混在不可"),
    ).json()
    foreign_snapshot = client.post(
        f"/api/projects/{other['id']}/candidates/{foreign['id']}/snapshots",
    ).json()

    decision = {
        "candidate_id": selected["id"],
        "snapshot_id": selected_snapshot["id"],
        "note": "支持範囲内でTSの最低達成確率が最も高い",
    }
    saved = client.put(f"/api/projects/{project['id']}/decision", json=decision)
    assert saved.status_code == 200
    assert saved.json()["decision_candidate_id"] == selected["id"]
    assert saved.json()["decision_snapshot_id"] == selected_snapshot["id"]
    assert saved.json()["decision_note"] == decision["note"]

    invalid = {
        **decision,
        "candidate_id": foreign["id"],
        "snapshot_id": foreign_snapshot["id"],
    }
    assert client.put(f"/api/projects/{project['id']}/decision", json=invalid).status_code == 422
    wrong_snapshot = {**decision, "snapshot_id": foreign_snapshot["id"]}
    assert client.put(
        f"/api/projects/{project['id']}/decision",
        json=wrong_snapshot,
    ).status_code == 422

    assert client.delete(f"/api/projects/{project['id']}/candidates/{selected['id']}?expected_revision={selected['revision']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").json()["decision_candidate_id"] == selected["id"]
    assert client.get(f"/api/projects/{project['id']}/candidates/{selected['id']}").status_code == 404
    archived = client.get(f"/api/projects/{project['id']}/candidates/{selected['id']}?include_archived=true").json()
    assert archived["archived_at"] is not None

    cleared_response = client.put(
        f"/api/projects/{project['id']}/decision",
        json={"candidate_id": "", "snapshot_id": "", "note": ""},
    )
    assert cleared_response.status_code == 200
    assert client.delete(f"/api/projects/{project['id']}/candidates/{selected['id']}?expected_revision={archived['revision']}").status_code == 409
    cleared = client.get(f"/api/projects/{project['id']}").json()
    assert cleared["decision_candidate_id"] == ""
    assert cleared["decision_snapshot_id"] == ""
    assert cleared["decision_note"] == ""


def test_existing_project_database_migrates_without_losing_data(tmp_path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT 'annealed-properties-v1', target_values TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '')",
        )
        conn.execute(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy",
                "既存プロジェクト",
                "保持される説明",
                "既存データ保護",
                "annealed-properties-v1",
                '{"TS": 500}',
                "保持されるメモ",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )

    migrated = Store(database).get_project("legacy")
    assert migrated is not None
    assert migrated.name == "既存プロジェクト"
    assert migrated.target_values == {"TS": 500}
    assert migrated.notes == "保持されるメモ"
    assert migrated.decision_candidate_id == ""
    assert migrated.decision_snapshot_id == ""
    assert migrated.decision_note == ""


def test_existing_empty_database_is_not_reseeded(tmp_path, app_resources: _AppResources) -> None:
    database = tmp_path / "existing.db"
    database.touch()

    with TestClient(create_app(db_path=database, _resources=app_resources)) as existing_client:
        assert existing_client.get("/api/projects/default/candidates").json() == []
        assert existing_client.get("/api/projects/hot-rolling-default/candidates").json() == []
