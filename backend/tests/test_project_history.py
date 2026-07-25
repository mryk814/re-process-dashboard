from __future__ import annotations

import sqlite3

import pytest

from material_workbench.application.inference import InferenceService
from material_workbench.contracts.schemas import ActualMeasurementInput
from material_workbench.persistence.store import CandidateRevisionConflictError


def _candidate_payload(source: dict, name: str) -> dict:
    return {
        "name": name,
        "inputs": source["inputs"],
        "provenance": {"source_kind": "direct", "source_ref": None},
    }


def test_project_history_keeps_current_candidate_separate_from_fixed_snapshot(client) -> None:
    source = client.get("/api/projects/default/candidates").json()[0]
    created = client.post(
        "/api/projects/default/candidates",
        json=_candidate_payload(source, "履歴候補A"),
    ).json()
    detailed = client.post(
        f"/api/projects/default/candidates/{created['id']}/predict",
        params={"expected_revision": created["revision"]},
    ).json()
    snapshot = detailed["snapshot"]
    update = {
        **_candidate_payload(created, "履歴候補A r2"),
        "expected_revision": created["revision"],
    }
    updated = client.put(
        f"/api/projects/default/candidates/{created['id']}",
        json=update,
    ).json()

    history = client.get("/api/projects/default/history")
    assert history.status_code == 200
    item = next(entry for entry in history.json()["candidates"] if entry["candidate"]["id"] == created["id"])
    assert item["candidate"]["name"] == "履歴候補A r2"
    assert item["current"]["revision"] == updated["revision"]
    assert item["snapshots"][0]["id"] == snapshot["id"]
    assert item["snapshots"][0]["candidate_revision"] == created["revision"]
    assert item["snapshots"][0]["prediction_summary"] == snapshot["payload"]["prediction"]["predictions"]

    restored = client.post(f"/api/projects/default/snapshots/{snapshot['id']}/restore")
    assert restored.status_code == 201
    assert restored.json()["id"] != created["id"]
    assert restored.json()["provenance"] == {
        "source_kind": "snapshot",
        "source_ref": {"snapshot_id": snapshot["id"]},
    }


def test_project_history_includes_archived_candidates_actuals_and_decision(client) -> None:
    source = client.get("/api/projects/default/candidates").json()[0]
    created = client.post(
        "/api/projects/default/candidates",
        json=_candidate_payload(source, "判断候補"),
    ).json()
    actual = client.post(
        f"/api/projects/default/candidates/{created['id']}/actuals",
        params={"expected_revision": created["revision"]},
        json={"property": "TS", "mean": 510, "unit": "MPa"},
    ).json()
    decision = client.put(
        "/api/projects/default/decision",
        json={"candidate_id": created["id"], "snapshot_id": actual["snapshot_id"], "note": "採用理由"},
    )
    assert decision.status_code == 200
    archived = client.delete(
        f"/api/projects/default/candidates/{created['id']}",
        params={"expected_revision": created["revision"]},
    )
    assert archived.status_code == 204

    item = next(
        entry
        for entry in client.get("/api/projects/default/history").json()["candidates"]
        if entry["candidate"]["id"] == created["id"]
    )
    assert item["candidate"]["archived_at"] is not None
    assert [entry["id"] for entry in item["actuals"]] == [actual["id"]]
    assert item["decision"] == {
        "candidate_id": created["id"],
        "snapshot_id": actual["snapshot_id"],
        "note": "採用理由",
    }


def test_store_returns_the_actual_inserted_by_id(client) -> None:
    store = client.app.state.store
    candidate = client.get("/api/projects/default/candidates").json()[0]

    first = store.create_snapshot_and_actual(
        "default",
        candidate["id"],
        candidate["revision"],
        {"snapshot_schema_version": "prediction-snapshot-v2", "marker": "first"},
        ActualMeasurementInput(property="TS", mean=501, unit="MPa", experiment_no="EXP-1"),
    )
    second = store.create_snapshot_and_actual(
        "default",
        candidate["id"],
        candidate["revision"],
        {"snapshot_schema_version": "prediction-snapshot-v2", "marker": "second"},
        ActualMeasurementInput(property="TS", mean=502, unit="MPa", experiment_no="EXP-2"),
    )

    assert first.id != second.id
    assert first.experiment_no == "EXP-1"
    assert second.experiment_no == "EXP-2"
    assert {item.id for item in store.list_actuals(candidate["id"])} == {first.id, second.id}
    assert store.get_snapshot(first.snapshot_id)["payload"]["marker"] == "first"
    assert store.get_snapshot(second.snapshot_id)["payload"]["marker"] == "second"


def test_snapshot_rolls_back_when_actual_insert_fails(client) -> None:
    store = client.app.state.store
    candidate = client.get("/api/projects/default/candidates").json()[0]
    snapshots_before = len(store.list_snapshots(candidate["id"]))
    with sqlite3.connect(store.path) as conn:
        conn.execute(
            "CREATE TRIGGER reject_atomic_actual BEFORE INSERT ON actual_measurements "
            "BEGIN SELECT RAISE(ABORT, 'forced actual failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced actual failure"):
        store.create_snapshot_and_actual(
            "default",
            candidate["id"],
            candidate["revision"],
            {"snapshot_schema_version": "prediction-snapshot-v2"},
            ActualMeasurementInput(property="TS", mean=500, unit="MPa"),
        )

    assert len(store.list_snapshots(candidate["id"])) == snapshots_before
    assert store.list_actuals(candidate["id"]) == []


def test_actual_api_rechecks_revision_after_inference(client, monkeypatch) -> None:
    store = client.app.state.store
    candidate = client.get("/api/projects/default/candidates").json()[0]
    original = InferenceService.detailed_for

    def update_during_inference(self, project, current):
        result = original(self, project, current)
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "UPDATE candidates SET revision=revision+1 WHERE id=?",
                (candidate["id"],),
            )
        return result

    monkeypatch.setattr(InferenceService, "detailed_for", update_during_inference)

    response = client.post(
        f"/api/projects/default/candidates/{candidate['id']}/actuals",
        params={"expected_revision": candidate["revision"]},
        json={"property": "TS", "mean": 500, "unit": "MPa"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "revision_conflict"
    assert store.list_snapshots(candidate["id"]) == []
    assert store.list_actuals(candidate["id"]) == []


def test_project_history_rejects_unreadable_snapshot_payload(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    client.app.state.store.create_snapshot(candidate["id"], {"snapshot_schema_version": "unknown"})

    response = client.get("/api/projects/default/history")

    assert response.status_code == 409
    assert response.json()["code"] == "data_integrity_error"


def test_task_catalog_starters_validate_and_cross_task_copy_is_rejected(client) -> None:
    catalog = client.get("/api/task-definitions")
    assert catalog.status_code == 200
    entries = {item["definition"]["task_definition"]["id"]: item for item in catalog.json()}
    projects = {
        "annealed-properties-v1": "default",
        "hot-rolled-properties-v1": "hot-rolling-default",
    }
    for task_id, project_id in projects.items():
        created = client.post(
            f"/api/projects/{project_id}/candidates",
            json=entries[task_id]["starter_candidate"],
        )
        assert created.status_code == 201

    source = client.get("/api/projects/default/candidates").json()[0]
    forged_copy = {
        **entries["hot-rolled-properties-v1"]["starter_candidate"],
        "provenance": {
            "source_kind": "copy",
            "source_ref": {
                "project_id": "default",
                "candidate_id": source["id"],
                "candidate_revision": source["revision"],
            },
        },
    }
    rejected = client.post("/api/projects/hot-rolling-default/candidates", json=forged_copy)
    assert rejected.status_code == 422
    assert "異なる予測タスク" in rejected.json()["message"]


def test_project_and_initial_copy_are_created_atomically(client) -> None:
    source = client.get("/api/projects/default/candidates").json()[0]
    reference = client.get("/api/projects/default").json()
    project_payload = {
        "name": "atomic copy project",
        "task_id": "annealed-properties-v1",
        "dataset_view_revision_id": reference["dataset_view_revision_id"],
        "model_package_ref_id": reference["model_package_ref_id"],
        "initial_candidate": {
            **_candidate_payload(source, "初期コピー"),
            "provenance": {
                "source_kind": "copy",
                "source_ref": {
                    "project_id": "default",
                    "candidate_id": source["id"],
                    "candidate_revision": source["revision"],
                },
            },
        },
    }
    created = client.post("/api/projects", json=project_payload)
    assert created.status_code == 201
    candidates = client.get(f"/api/projects/{created.json()['id']}/candidates").json()
    assert len(candidates) == 1
    assert candidates[0]["provenance"]["source_ref"]["candidate_revision"] == source["revision"]

    before_ids = {project["id"] for project in client.get("/api/projects").json()}
    project_payload["name"] = "must rollback"
    project_payload["initial_candidate"]["provenance"]["source_ref"]["candidate_revision"] += 100
    rejected = client.post("/api/projects", json=project_payload)
    assert rejected.status_code == 422
    assert {project["id"] for project in client.get("/api/projects").json()} == before_ids
