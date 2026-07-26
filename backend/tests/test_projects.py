from io import BytesIO
from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from openpyxl import Workbook
import pytest

from material_workbench.app import _AppResources, create_app
from material_workbench.persistence.store import MAX_CANDIDATES_PER_PROJECT, Store

ELEMENTS = ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")
SOURCE = Path(__file__).parents[2] / "data" / "source" / "material_workbench_tutorial_v2.xlsx"


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


def _project(client, name: str, task_id: str = "annealed-properties-v1") -> dict:
    reference_id = "hot-rolling-default" if task_id == "hot-rolled-properties-v1" else "default"
    reference = client.get(f"/api/projects/{reference_id}").json()
    return {
        "name": name,
        "description": "独立した検討",
        "purpose": "プロジェクト分離の確認",
        "task_id": task_id,
        "target_values": {"TS": 500},
        "notes": "",
        "dataset_view_revision_id": reference["dataset_view_revision_id"],
        "model_package_ref_id": reference["model_package_ref_id"],
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


def test_project_design_space_migration_is_additive_and_idempotent(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    Store(database)

    with sqlite3.connect(database) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(projects)")
        }
        legacy = conn.execute(
            "SELECT design_space_json,design_space_digest,"
            "design_space_binding_provenance FROM projects WHERE id='default'"
        ).fetchone()
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations "
            "WHERE id='project-design-space-v1'"
        ).fetchone()

    assert {
        "design_space_json",
        "design_space_digest",
        "design_space_binding_provenance",
    } <= columns
    assert legacy == (None, None, "unbound_legacy")
    assert marker == ("immutable-project-design-space-v1",)
    with sqlite3.connect(database) as conn:
        objective_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(projects)")
        }
        legacy_objective = conn.execute(
            "SELECT objective_definition_json,objective_definition_digest,"
            "objective_binding_provenance FROM projects WHERE id='default'"
        ).fetchone()
        objective_marker = conn.execute(
            "SELECT checksum FROM schema_migrations "
            "WHERE id='project-objective-definition-v1'"
        ).fetchone()
        objective_revisions_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='project_objective_revisions'"
        ).fetchone()
    assert {
        "objective_definition_json",
        "objective_definition_digest",
        "objective_binding_provenance",
    } <= objective_columns
    assert legacy_objective == (None, None, "unbound_legacy")
    assert objective_marker == ("immutable-project-objective-definition-v1",)
    assert objective_revisions_table == ("project_objective_revisions",)


def test_project_crud_preserves_default_and_isolates_candidates_and_screening(client) -> None:
    default = client.get("/api/projects/default").json()
    created = client.post("/api/projects", json=_project(client, "新規プロジェクト"))
    assert created.status_code == 201
    project = created.json()
    assert project["id"] != "default"
    assert project["design_space"]["revision"] == 1
    assert project["design_space_digest"].startswith("sha256:")
    assert project["design_space_binding_provenance"] == "generated_default"
    assert project["objective_definition"]["revision"] == 1
    assert project["objective_definition"]["terms"][0]["output_key"] == "TS"
    assert project["objective_definition_digest"].startswith("sha256:")
    assert project["objective_binding_provenance"] == "generated_default"
    assert {item["id"] for item in client.get("/api/projects").json()} >= {"default", project["id"]}
    assert client.get(f"/api/projects/{project['id']}").json()["name"] == "新規プロジェクト"

    changed = _project(client, "更新後プロジェクト")
    changed["heat_stage_positions_m"] = {"加熱1": 42.5}
    changed["response_curve_points"] = 33
    updated = client.put(f"/api/projects/{project['id']}", json=changed).json()
    assert updated["name"] == "更新後プロジェクト"
    assert updated["heat_stage_positions_m"] == {"加熱1": 42.5}
    assert updated["response_curve_points"] == 33
    assert client.get(f"/api/projects/{project['id']}").json()["heat_stage_positions_m"] == {"加熱1": 42.5}
    assert client.get(f"/api/projects/{project['id']}").json()["design_space_digest"] == project["design_space_digest"]
    assert client.get("/api/projects/default").json()["name"] == default["name"]
    assert client.get("/api/projects/missing").status_code == 404

    revised_payload = {**changed, "target_values": {"YS": 400}}
    revised = client.put(
        f"/api/projects/{project['id']}",
        json=revised_payload,
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["objective_definition"]["revision"] == 2
    assert revised.json()["objective_definition_digest"] != project["objective_definition_digest"]
    assert revised.json()["objective_binding_provenance"] == "updated_revision"
    with sqlite3.connect(client.app.state.store.path) as conn:
        revisions = conn.execute(
            "SELECT revision,objective_digest FROM project_objective_revisions "
            "WHERE project_id=? ORDER BY revision",
            (project["id"],),
        ).fetchall()
    assert [item[0] for item in revisions] == [1, 2]
    objective_history = client.get(
        f"/api/projects/{project['id']}/objectives"
    )
    assert objective_history.status_code == 200
    assert [
        item["objective_definition"]["revision"]
        for item in objective_history.json()
    ] == [1, 2]
    assert {
        item["objective_definition_digest"] for item in objective_history.json()
    } == {item[1] for item in revisions}

    candidate = client.post(f"/api/projects/{project['id']}/candidates", json=_candidate("P2候補"))
    assert candidate.status_code == 201
    candidate_id = candidate.json()["id"]
    assert [item["id"] for item in client.get(f"/api/projects/{project['id']}/candidates").json()] == [candidate_id]
    assert candidate_id not in {item["id"] for item in client.get("/api/projects/default/candidates").json()}

    screening_body = {
        "purpose": "goal_search",
        "base_candidate_id": candidate_id,
        "base_inputs": candidate.json()["inputs"],
        "samples": 48,
        "target": "TS",
        "target_goal": {"direction": "at_least", "lower": 500},
        "secondary_goals": {"YS": {"direction": "at_least", "lower": 350}},
        "variables": {"composition.C": {"mode": "range", "min": 0.06, "max": 0.1}},
    }
    run = client.post(f"/api/screening?project_id={project['id']}", json=screening_body)
    assert run.status_code == 201
    assert run.json()["project_design_space_digest"] == project["design_space_digest"]
    assert run.json()["project_design_space_binding_provenance"] == "generated_default"
    assert run.json()["schema_version"] == "screening-run/v7"
    assert run.json()["objective_definition_digest"].startswith("sha256:")
    assert run.json()["objective_binding_provenance"] == "project_revision"
    assert run.json()["target"] == "YS"
    assert run.json()["target_goal"] == {"direction": "at_least", "lower": 400, "upper": None}
    assert run.json()["secondary_goals"] == {}
    assert run.json()["objective_execution"]["objective_digest"] == run.json()["objective_definition_digest"]
    assert run.json()["objective_execution"]["target"] == "YS"
    assert (
        run.json()["objective_definition"]["optimization_kind"]
        == "single_objective"
    )
    map_run = client.post(
        f"/api/screening?project_id={project['id']}",
        json={
            **screening_body,
            "purpose": "design_space_map",
            "target_goal": None,
            "secondary_goals": {},
            "proposal": {
                "strategy_id": "sobol_ucb_v1",
                "support_policy": "allow_with_warning",
            },
        },
    )
    assert map_run.status_code == 201, map_run.text
    map_payload = map_run.json()
    assert map_payload["purpose"] == "design_space_map"
    assert map_payload["objective_binding_provenance"] == "legacy_screening"
    assert len(map_payload["objective_definition"]["terms"]) == 1
    map_term = map_payload["objective_definition"]["terms"][0]
    assert map_term["output_key"] == "TS"
    assert map_term["unit"] == "MPa"
    assert map_term["role"] == "reporting_only"
    assert map_term["direction"] is None
    assert map_payload["objective_execution"] is None
    assert map_payload["score_contract"]["fallback"] == "support_distance"
    assert map_payload["batch_proposal"] is None
    assert map_payload["proposal_strategy"]["id"] == "latin_hypercube_v1"

    batch_run = client.post(
        f"/api/screening?project_id={project['id']}",
        json={
            **screening_body,
            "purpose": "experiment_batch",
            "source_run_id": run.json()["id"],
            "batch_definition": {
                "selector_id": "ranked_top_k_v1",
                "batch_size": 4,
                "candidate_pool_size": 16,
                "near_duplicate_threshold": 0,
            },
        },
    )
    assert batch_run.status_code == 201, batch_run.text
    batch_payload = batch_run.json()
    assert batch_payload["purpose"] == "experiment_batch"
    assert batch_payload["source_run_id"] == run.json()["id"]
    assert batch_payload["points"] == run.json()["points"]
    assert batch_payload["proposal_pool"] == run.json()["proposal_pool"]
    assert batch_payload["seed"] == run.json()["seed"]
    assert batch_payload["proposal_strategy"] == run.json()["proposal_strategy"]
    assert len(batch_payload["batch_proposal"]["selected"]) == 4

    ei_run = client.post(
        f"/api/screening?project_id={project['id']}",
        json={
            **screening_body,
            "proposal": {
                "strategy_id": "sobol_ei_v1",
                "incumbent_value": 300,
            },
        },
    )
    assert ei_run.status_code == 201, ei_run.text
    assert ei_run.json()["proposal_strategy"]["id"] == "sobol_ei_v1"
    assert (
        ei_run.json()["proposal_strategy"]["incumbent_resolution"]["source"]
        == "request_override"
    )
    restored_ei_batch = client.post(
        f"/api/screening?project_id={project['id']}",
        json={
            **screening_body,
            "purpose": "experiment_batch",
            "source_run_id": ei_run.json()["id"],
            # A reloaded UI does not need to reconstruct acquisition inputs.
            "proposal": {
                "strategy_id": "sobol_ei_v1",
                "incumbent_value": None,
            },
            "batch_definition": {
                "selector_id": "ranked_top_k_v1",
                "batch_size": 4,
                "candidate_pool_size": 16,
                "near_duplicate_threshold": 0,
            },
        },
    )
    assert restored_ei_batch.status_code == 201, restored_ei_batch.text
    assert restored_ei_batch.json()["points"] == ei_run.json()["points"]
    assert (
        restored_ei_batch.json()["proposal_strategy"]
        == ei_run.json()["proposal_strategy"]
    )

    incompatible_batch = client.post(
        f"/api/screening?project_id={project['id']}",
        json={
            **screening_body,
            "purpose": "experiment_batch",
            "source_run_id": run.json()["id"],
            "variables": {
                "composition.C": {"mode": "range", "min": 0.07, "max": 0.1}
            },
            "batch_definition": {
                "selector_id": "ranked_top_k_v1",
                "batch_size": 4,
                "candidate_pool_size": 16,
                "near_duplicate_threshold": 0,
            },
        },
    )
    assert incompatible_batch.status_code == 422
    assert "一致しません" in incompatible_batch.json()["message"]

    explicit_objective = run.json()["objective_definition"]
    explicit_objective["objective_id"] = "screening-objective-explicit"
    explicit_objective["incumbent"] = {
        "source": "candidate_revision",
        "candidate_id": candidate_id,
        "candidate_revision": candidate.json()["revision"],
    }
    explicit_run = client.post(
        f"/api/screening?project_id={project['id']}",
        json={**screening_body, "objective_definition": explicit_objective},
    )
    assert explicit_run.status_code == 201, explicit_run.text
    assert explicit_run.json()["objective_binding_provenance"] == "explicit"
    assert explicit_run.json()["target"] == "YS"
    assert (
        explicit_run.json()["objective_definition"]["incumbent"]["candidate_revision"]
        == candidate.json()["revision"]
    )
    incumbent_resolution = explicit_run.json()["proposal_strategy"]["incumbent_resolution"]
    assert incumbent_resolution["source"] == "objective_candidate_revision"
    assert incumbent_resolution["candidate_id"] == candidate_id
    assert incumbent_resolution["candidate_revision"] == candidate.json()["revision"]
    assert incumbent_resolution["value"] == explicit_run.json()["proposal_strategy"]["incumbent_value"]

    wrong_unit = {
        **explicit_objective,
        "objective_id": "screening-objective-wrong-unit",
        "terms": [
            {
                **term,
                "unit": "not-MPa",
            }
            for term in explicit_objective["terms"]
        ],
    }
    rejected_objective = client.post(
        f"/api/screening?project_id={project['id']}",
        json={**screening_body, "objective_definition": wrong_unit},
    )
    assert rejected_objective.status_code == 422
    assert "単位" in rejected_objective.json()["message"]
    assert set(run.json()["points"][0]["predictions"]) == {"TS", "YS", "EL", "lambda"}
    assert run.json()["points"][0]["secondary_goal_evaluations"] == {}
    assert run.json()["points"][0]["goal_evaluation"]["method"] in {
        "achievement_probability",
        "directional_shortfall",
    }
    run_id = run.json()["id"]
    batch = client.post(f"/api/screening/{run_id}/candidates?project_id={project['id']}", json={"point_indices": [0, 1]})
    assert batch.status_code == 201
    assert len(batch.json()["candidates"]) == 2
    assert {item["provenance"]["source_ref"]["point_index"] for item in batch.json()["candidates"]} == {0, 1}
    duplicate = client.post(f"/api/screening/{run_id}/candidates?project_id={project['id']}", json={"point_indices": [0]})
    assert duplicate.status_code == 201
    assert duplicate.json() == {"candidates": [], "skipped_point_indices": [0]}
    assert {
        item["id"]
        for item in client.get(f"/api/screening?project_id={project['id']}").json()
    } == {
            run_id,
            map_run.json()["id"],
            batch_run.json()["id"],
            ei_run.json()["id"],
            restored_ei_batch.json()["id"],
            explicit_run.json()["id"],
        }
    assert client.get("/api/screening").json() == []
    assert client.get(f"/api/screening/{run_id}").status_code == 404
    assert client.post("/api/screening", json=screening_body).status_code == 404
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.get(f"/api/projects/{project['id']}").status_code == 404
    archived = client.get(
        f"/api/projects/{project['id']}?include_archived=true"
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    assert client.get(f"/api/projects/{project['id']}/candidates").status_code == 404
    assert project["id"] not in {item["id"] for item in client.get("/api/projects").json()}
    assert project["project_series_id"] not in {
        item["id"] for item in client.get("/api/project-series").json()
    }
    restored = client.post(f"/api/projects/{project['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert client.get(f"/api/projects/{project['id']}").status_code == 200
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    purged = client.delete(
        f"/api/projects/{project['id']}/purge",
        params={"confirm_project_id": project["id"]},
    )
    assert purged.status_code == 204
    assert client.get(
        f"/api/projects/{project['id']}?include_archived=true"
    ).status_code == 404

    assert client.delete("/api/projects/default").status_code == 409
    assert client.delete("/api/projects/hot-rolling-default").status_code == 409
    assert client.delete("/api/projects/missing").status_code == 404


def test_project_series_is_optional_and_new_series_creation_is_explicit(client) -> None:
    before_ids = {
        item["id"] for item in client.get("/api/project-series").json()
    }

    standalone = client.post(
        "/api/projects",
        json=_project(client, "検討グループなし"),
    )
    assert standalone.status_code == 201, standalone.text
    assert standalone.json()["project_series_id"] is None
    assert {
        item["id"] for item in client.get("/api/project-series").json()
    } == before_ids

    grouped = client.post(
        "/api/projects",
        json={
            **_project(client, "明示的な検討グループ"),
            "new_project_series": {
                "name": "耐熱材の一連検討",
                "description": "明示作成",
            },
        },
    )
    assert grouped.status_code == 201, grouped.text
    group_id = grouped.json()["project_series_id"]
    assert group_id is not None
    created_group = client.get(f"/api/project-series/{group_id}").json()
    assert created_group["name"] == "耐熱材の一連検討"
    assert created_group["description"] == "明示作成"
    assert created_group["archived_at"] is None

    conflicting = client.post(
        "/api/projects",
        json={
            **_project(client, "排他指定"),
            "project_series_id": group_id,
            "new_project_series": {"name": "同時指定不可"},
        },
    )
    assert conflicting.status_code == 422


def test_new_project_series_rolls_back_when_project_insert_fails(client) -> None:
    database = client.app.state.store.path
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TRIGGER reject_atomic_project BEFORE INSERT ON projects "
            "WHEN NEW.name='transaction rollback' "
            "BEGIN SELECT RAISE(ABORT, 'forced project failure'); END"
        )

    before_ids = {
        item["id"] for item in client.get("/api/project-series").json()
    }
    with pytest.raises(sqlite3.IntegrityError, match="forced project failure"):
        client.post(
            "/api/projects",
            json={
                **_project(client, "transaction rollback"),
                "new_project_series": {"name": "残ってはいけない検討グループ"},
            },
        )
    assert {
        item["id"] for item in client.get("/api/project-series").json()
    } == before_ids


def test_project_design_space_narrows_screening_and_is_immutable(client) -> None:
    generated = client.post(
        "/api/projects", json=_project(client, "Design Space原型")
    ).json()
    space = generated["design_space"]
    space["design_space_id"] = "annealed-narrow-carbon"
    space["revision"] = 2
    for domain in space["numeric_domains"]:
        if domain["path"] == "composition.C":
            domain["range"] = {"min": 0.07, "max": 0.09}
            break
    created = client.post(
        "/api/projects",
        json={**_project(client, "Design Space固定"), "design_space": space},
    )
    assert created.status_code == 201, created.text
    project = created.json()
    assert project["design_space_binding_provenance"] == "explicit"

    continued = client.post(
        "/api/projects",
        json={
            **_project(client, "Design Spaceを継承"),
            "predecessor_project_id": project["id"],
            "continuation_reason": "同じ実行可能領域で続ける",
        },
    )
    assert continued.status_code == 201, continued.text
    assert continued.json()["design_space_digest"] == project["design_space_digest"]
    assert (
        continued.json()["design_space_binding_provenance"]
        == "inherited_predecessor"
    )
    assert (
        continued.json()["objective_definition_digest"]
        == project["objective_definition_digest"]
    )
    assert (
        continued.json()["objective_binding_provenance"]
        == "inherited_predecessor"
    )

    outside = _candidate("Design Space外候補")
    outside["inputs"]["composition"]["C"] = 0.10
    rejected = client.post(
        f"/api/projects/{project['id']}/candidates",
        json=outside,
    )
    assert rejected.status_code == 422
    assert "Project Design Spaceの範囲外" in (
        rejected.json().get("detail") or rejected.json().get("message", "")
    )

    candidate = client.post(
        f"/api/projects/{project['id']}/candidates",
        json=_candidate("Design Space候補"),
    ).json()
    snapshot = client.post(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/snapshots"
    )
    assert snapshot.status_code == 201, snapshot.text
    assert (
        snapshot.json()["payload"]["project_design_space_digest"]
        == project["design_space_digest"]
    )
    screening = client.post(
        "/api/screening",
        params={"project_id": project["id"]},
        json={
            "purpose": "design_space_map",
            "base_candidate_id": candidate["id"],
            "base_inputs": candidate["inputs"],
            "samples": 48,
            "target": "TS",
            "proposal": {"support_policy": "allow_with_warning"},
            "variables": {
                "composition.C": {"mode": "range", "min": 0.06, "max": 0.10}
            },
        },
    )
    assert screening.status_code == 422
    assert "Project Design Spaceの範囲" in (
        screening.json().get("detail") or screening.json().get("message", "")
    )

    stale_update = {
        **_project(client, "Design Space変更不可"),
        "design_space_digest": "sha256:" + "0" * 64,
    }
    locked = client.put(f"/api/projects/{project['id']}", json=stale_update)
    assert locked.status_code == 409
    assert locked.json()["code"] == "project_task_locked"


def test_project_with_cross_project_derived_candidate_cannot_be_deleted(client) -> None:
    source_project = client.post(
        "/api/projects",
        json=_project(client, "派生元プロジェクト"),
    ).json()
    derived_project = client.post(
        "/api/projects",
        json=_project(client, "派生先プロジェクト"),
    ).json()
    source = client.post(
        f"/api/projects/{source_project['id']}/candidates",
        json=_candidate("派生元候補"),
    ).json()
    derived_payload = {
        **_candidate("派生候補"),
        "provenance": {
            "source_kind": "copy",
            "source_ref": {
                "project_id": source_project["id"],
                "candidate_id": source["id"],
                "candidate_revision": source["revision"],
            },
        },
    }
    derived = client.post(
        f"/api/projects/{derived_project['id']}/candidates",
        json=derived_payload,
    )
    assert derived.status_code == 201

    archived = client.delete(f"/api/projects/{source_project['id']}")
    assert archived.status_code == 204
    rejected = client.delete(
        f"/api/projects/{source_project['id']}/purge",
        params={"confirm_project_id": source_project["id"]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "project_has_derived_candidates"
    assert "派生候補が別のプロジェクトにある" in rejected.json()["message"]
    assert client.get(
        f"/api/projects/{source_project['id']}?include_archived=true"
    ).status_code == 200
    chain = client.get(
        f"/api/projects/{derived_project['id']}/candidates/"
        f"{derived.json()['id']}/derivation-chain"
    )
    assert chain.status_code == 200
    assert chain.json()[0]["id"] == source["id"]


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
        json={**_project(client, "所属変更"), "project_series_id": source_group["id"]},
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
        json={**_project(client, "移動対象"), "project_series_id": source_group["id"]},
    ).json()
    client.post(
        "/api/projects",
        json={**_project(client, "残留対象"), "project_series_id": source_group["id"]},
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
            "purpose": "goal_search",
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "target": "TS",
            "target_goal": {"direction": "at_least", "lower": 520},
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
            "purpose": "design_space_map",
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "target": "TS",
            "proposal": {"support_policy": "allow_with_warning"},
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
            "purpose": "design_space_map",
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "target": "TS",
            "proposal": {"support_policy": "allow_with_warning"},
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
    payload = _project(client, "熱延タスク", "hot-rolled-properties-v1")
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
    project = client.post("/api/projects", json={**_project(client, "表示桁数"), "display_decimals": {"composition.C": 4, "output.TS": 2}}).json()
    assert project["display_decimals"] == {"composition.C": 4, "output.TS": 2}
    assert client.get(f"/api/projects/{project['id']}").json()["display_decimals"] == project["display_decimals"]

    invalid = client.put(f"/api/projects/{project['id']}", json={**_project(client, "表示桁数"), "display_decimals": {"output.unknown": 2}})
    assert invalid.status_code == 422
    assert "タスクに存在しない表示項目" in invalid.json()["message"]


def test_candidate_limit_is_enforced_for_every_creation_route(client) -> None:
    assert MAX_CANDIDATES_PER_PROJECT == 100
    project = client.post("/api/projects", json=_project(client, "上限確認")).json()
    project_id = project["id"]
    initial_capacity = client.get(
        f"/api/projects/{project_id}/candidate-capacity"
    )
    assert initial_capacity.status_code == 200
    assert initial_capacity.json() == {
        "schema_version": "candidate-capacity/v1",
        "limit": MAX_CANDIDATES_PER_PROJECT,
        "used": 0,
        "remaining": MAX_CANDIDATES_PER_PROJECT,
    }
    base = client.post(f"/api/projects/{project_id}/candidates", json=_candidate("基準")).json()
    snapshot = client.post(f"/api/projects/{project_id}/candidates/{base['id']}/snapshots").json()
    screening = client.post(
        f"/api/screening?project_id={project_id}",
        json={
            "purpose": "goal_search",
            "base_candidate_id": base["id"],
            "base_inputs": base["inputs"],
            "samples": 48,
            "target": "TS",
            "target_goal": {"direction": "at_least", "lower": 500},
            "variables": {"composition.C": {"mode": "range", "min": 0.06, "max": 0.1}},
        },
    ).json()
    for index in range(2, MAX_CANDIDATES_PER_PROJECT + 1):
        assert client.post(f"/api/projects/{project_id}/candidates", json=_candidate(f"候補{index}")).status_code == 201
    assert len(client.get(f"/api/projects/{project_id}/candidates").json()) == MAX_CANDIDATES_PER_PROJECT
    assert client.get(f"/api/projects/{project_id}/candidate-capacity").json() == {
        "schema_version": "candidate-capacity/v1",
        "limit": MAX_CANDIDATES_PER_PROJECT,
        "used": MAX_CANDIDATES_PER_PROJECT,
        "remaining": 0,
    }

    direct = client.post(f"/api/projects/{project_id}/candidates", json=_candidate(f"{MAX_CANDIDATES_PER_PROJECT + 1}件目"))
    assert direct.status_code == 409 and f"最大{MAX_CANDIDATES_PER_PROJECT}件" in direct.json()["message"]
    assert client.post(f"/api/projects/{project_id}/lineage/AN-01/candidate").status_code == 409
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
        json={**_project(client, "不正な初期判断"), "decision_candidate_id": "not-yet-created"},
    ).status_code == 422
    assert client.post(
        "/api/projects",
        json={**_project(client, "理由だけ"), "decision_note": "候補がない"},
    ).status_code == 422
    project = client.post("/api/projects", json=_project(client, "判断記録")).json()
    other = client.post("/api/projects", json=_project(client, "別プロジェクト")).json()
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
