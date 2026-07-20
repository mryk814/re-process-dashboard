from __future__ import annotations


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
    project_payload = {
        "name": "atomic copy project",
        "task_id": "annealed-properties-v1",
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
