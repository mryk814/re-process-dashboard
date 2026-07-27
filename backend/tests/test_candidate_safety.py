from __future__ import annotations

import sqlite3

from material_workbench.persistence.store import MAX_CANDIDATES_PER_PROJECT


def _create_candidate(client, name: str = "競合確認") -> dict:
    source = client.get("/api/projects/default/candidates").json()[0]
    payload = {key: source[key] for key in ("name", "inputs", "provenance")}
    payload["name"] = name
    response = client.post("/api/projects/default/candidates", json=payload)
    assert response.status_code == 201
    return response.json()


def _update_payload(candidate: dict, name: str) -> dict:
    return {
        "name": name,
        "inputs": candidate["inputs"],
        "provenance": candidate["provenance"],
        "expected_revision": candidate["revision"],
    }


def test_candidate_update_is_atomic_compare_and_swap_with_current_candidate(client) -> None:
    candidate = _create_candidate(client)
    url = f"/api/projects/default/candidates/{candidate['id']}"

    winner = client.put(url, json=_update_payload(candidate, "先着保存"))
    loser = client.put(url, json=_update_payload(candidate, "遅着保存"))

    assert winner.status_code == 200
    assert winner.json()["revision"] == candidate["revision"] + 1
    assert loser.status_code == 409
    assert loser.json()["code"] == "revision_conflict"
    assert loser.json()["current_candidate"] == winner.json()
    assert client.get(url).json()["name"] == "先着保存"
    missing_revision = {key: candidate[key] for key in ("name", "inputs", "provenance")}
    assert client.put(url, json=missing_revision).status_code == 422


def test_candidate_revisions_are_immutable_and_copy_can_pin_an_old_revision(client) -> None:
    source = _create_candidate(client, "派生元 v1")
    source_url = f"/api/projects/default/candidates/{source['id']}"
    updated = client.put(source_url, json=_update_payload(source, "派生元 v2")).json()

    revision_one = client.get(f"{source_url}/revisions/{source['revision']}")
    assert revision_one.status_code == 200
    assert revision_one.json()["name"] == "派生元 v1"
    assert client.get(f"{source_url}/revisions/{updated['revision']}").json()["name"] == "派生元 v2"

    derived_payload = {
        key: revision_one.json()[key]
        for key in ("inputs", "provenance")
    }
    derived_payload["name"] = "v1から派生"
    derived_payload["provenance"] = {
        "source_kind": "copy",
        "source_ref": {
            "project_id": "default",
            "candidate_id": source["id"],
            "candidate_revision": source["revision"],
        },
    }
    derived = client.post("/api/projects/default/candidates", json=derived_payload)

    assert derived.status_code == 201
    assert derived.json()["provenance"]["source_ref"]["candidate_revision"] == source["revision"]
    chain = client.get(
        f"/api/projects/default/candidates/{derived.json()['id']}/derivation-chain"
    )
    assert chain.status_code == 200
    assert [(item["id"], item["revision"], item["name"]) for item in chain.json()] == [
        (source["id"], source["revision"], "派生元 v1")
    ]

    # Removing the active source does not erase the immutable revision used by
    # the derived candidate.
    assert client.delete(
        f"{source_url}?expected_revision={updated['revision']}"
    ).status_code == 204
    assert client.get(source_url).status_code == 404
    archived_source = client.get(f"{source_url}?include_archived=true")
    assert archived_source.status_code == 200
    assert archived_source.json()["archived_at"] is not None
    assert client.get(f"{source_url}/revisions/{source['revision']}").json()["name"] == "派生元 v1"
    assert client.get(
        f"/api/projects/default/candidates/{derived.json()['id']}/derivation-chain"
    ).json()[0]["name"] == "派生元 v1"


def test_candidate_revision_migration_backfills_the_current_candidate(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    with sqlite3.connect(client.app.state.store.path) as conn:
        row = conn.execute(
            "SELECT name,payload FROM candidate_revisions "
            "WHERE candidate_id=? AND revision=?",
            (candidate["id"], candidate["revision"]),
        ).fetchone()
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations "
            "WHERE id='candidate-revision-history-v1'",
        ).fetchone()

    assert row is not None
    assert row[0] == candidate["name"]
    assert marker == ("immutable-candidate-revisions-v1",)


def test_detailed_prediction_uses_one_revision_consistently(client, monkeypatch) -> None:
    candidate = _create_candidate(client, "詳細予測revision")
    original = client.app.state.store.get_candidate
    calls = 0

    def counted(candidate_id, project_id=None, *, include_archived=False):
        nonlocal calls
        if candidate_id == candidate["id"]:
            calls += 1
        return original(candidate_id, project_id, include_archived=include_archived)

    monkeypatch.setattr(client.app.state.store, "get_candidate", counted)
    response = client.post(
        f"/api/projects/default/candidates/{candidate['id']}/predict",
        params={"expected_revision": candidate["revision"]},
    )

    assert response.status_code == 200
    assert calls == 1
    assert response.json()["snapshot"]["payload"]["raw_candidate"]["revision"] == candidate["revision"]


def test_revision_bound_prediction_and_actual_reject_stale_clients(client) -> None:
    candidate = _create_candidate(client, "stale operation")
    url = f"/api/projects/default/candidates/{candidate['id']}"
    updated = client.put(url, json=_update_payload(candidate, "外部更新済み"))
    assert updated.status_code == 200

    detailed = client.post(f"{url}/predict", params={"expected_revision": candidate["revision"]})
    preview = client.post(f"{url}/preview", params={"expected_revision": candidate["revision"]})
    curve = client.get(f"{url}/response-curve", params={
        "expected_revision": candidate["revision"],
        "target": "TS",
        "variable": "composition.C",
    })
    actual = client.post(
        f"{url}/actuals",
        params={"expected_revision": candidate["revision"]},
        json={"property": "TS", "mean": 500, "unit": "MPa"},
    )

    assert detailed.status_code == 409
    assert detailed.json()["code"] == "revision_conflict"
    assert preview.status_code == 409
    assert preview.json()["code"] == "revision_conflict"
    assert curve.status_code == 409
    assert curve.json()["code"] == "revision_conflict"
    assert actual.status_code == 409
    assert actual.json()["code"] == "revision_conflict"
    assert client.get(f"{url}/snapshots").json() == []
    assert client.get(f"{url}/actuals").json() == []


def test_referenced_candidate_is_archived_and_only_history_paths_can_use_it(client) -> None:
    candidate = _create_candidate(client, "履歴あり")
    url = f"/api/projects/default/candidates/{candidate['id']}"
    snapshot = client.post(f"{url}/snapshots").json()

    deleted = client.delete(f"{url}?expected_revision={candidate['revision']}")

    assert deleted.status_code == 204
    assert client.get(url).status_code == 404
    archived = client.get(f"{url}?include_archived=true").json()
    assert archived["archived_at"] is not None
    assert archived["revision"] == candidate["revision"] + 1
    assert candidate["id"] not in {item["id"] for item in client.get("/api/projects/default/candidates").json()}
    assert candidate["id"] in {item["id"] for item in client.get("/api/projects/default/candidates?include_archived=true").json()}
    assert client.get(f"{url}/snapshots").json()[0]["id"] == snapshot["id"]
    assert client.get(f"{url}/actuals").status_code == 200
    restored = client.post(f"/api/projects/default/snapshots/{snapshot['id']}/restore")
    assert restored.status_code == 201
    assert client.post(f"{url}/preview", params={"expected_revision": archived["revision"]}).status_code == 404
    assert client.post(f"{url}/predict", params={"expected_revision": archived["revision"]}).status_code == 404
    assert client.post(f"{url}/snapshots").status_code == 404
    assert client.post(f"{url}/actuals", params={"expected_revision": archived["revision"]}, json={"property": "TS", "mean": 500, "unit": "MPa"}).status_code == 404
    archived_update = _update_payload(archived, "編集不可")
    response = client.put(url, json=archived_update)
    assert response.status_code == 409
    assert response.json()["code"] == "candidate_archived"


def test_candidate_removal_always_archives_and_can_be_restored(client) -> None:
    referenced = _create_candidate(client, "screening基準")
    run = client.post(
        "/api/screening",
        json={
            "purpose": "goal_search",
            "base_candidate_id": referenced["id"],
            "base_inputs": referenced["inputs"],
            "samples": 48,
            "target": "TS",
            "target_goal": {"direction": "at_least", "lower": 500},
            "variables": {"composition.C": {"mode": "range", "min": 0.06, "max": 0.1}},
        },
    )
    assert run.status_code == 201
    assert client.delete(
        f"/api/projects/default/candidates/{referenced['id']}?expected_revision={referenced['revision']}"
    ).status_code == 204
    assert client.get(
        f"/api/projects/default/candidates/{referenced['id']}?include_archived=true"
    ).json()["archived_at"] is not None

    disposable = _create_candidate(client, "未参照")
    assert client.delete(
        f"/api/projects/default/candidates/{disposable['id']}?expected_revision={disposable['revision']}"
    ).status_code == 204
    archived_disposable = client.get(
        f"/api/projects/default/candidates/{disposable['id']}?include_archived=true"
    )
    assert archived_disposable.status_code == 200
    assert archived_disposable.json()["archived_at"] is not None

    restored = client.post(
        f"/api/projects/default/candidates/{disposable['id']}/restore"
    )
    assert restored.status_code == 200
    assert restored.json()["archived_at"] is None
    assert restored.json()["revision"] == disposable["revision"] + 2
    assert disposable["id"] in {
        item["id"] for item in client.get("/api/projects/default/candidates").json()
    }


def test_domain_error_codes_and_openapi_contract_are_distinct(client) -> None:
    project = client.get("/api/projects/default").json()
    locked = client.put("/api/projects/default", json={**project, "task_id": "hot-rolled-properties-v1"})
    assert locked.status_code == 409
    assert locked.json()["code"] == "project_task_locked"

    for index in range(MAX_CANDIDATES_PER_PROJECT - len(client.get("/api/projects/default/candidates").json())):
        assert _create_candidate(client, f"上限{index}")
    limit = client.post(
        "/api/projects/default/candidates",
        json={key: client.get("/api/projects/default/candidates").json()[0][key] for key in ("name", "inputs", "provenance")},
    )
    assert limit.status_code == 409
    assert limit.json()["code"] == "candidate_limit"

    openapi = client.get("/openapi.json").json()
    schemas = openapi["components"]["schemas"]
    assert "expected_revision" in schemas["CandidateUpdate"]["required"]
    assert "current_candidate" in schemas["ApiError"]["properties"]
    codes = set(schemas["ApiError"]["properties"]["code"]["enum"])
    assert {"revision_conflict", "candidate_limit", "candidate_archived", "project_task_locked", "data_integrity_error"} <= codes
    update_responses = openapi["paths"]["/api/projects/{project_id}/candidates/{candidate_id}"]["put"]["responses"]
    assert update_responses["409"]["content"]["application/json"]["schema"]["$ref"].endswith("/ApiError")


def test_candidate_archive_does_not_parse_unrelated_screening_history(client) -> None:
    candidate = _create_candidate(client, "不正履歴の基準")
    with sqlite3.connect(client.app.state.store.path) as conn:
        conn.execute(
            "INSERT INTO screening_runs VALUES (?,?,?,?)",
            ("broken-run", "default", "not-json", "2026-07-20T00:00:00+00:00"),
        )

    response = client.delete(
        f"/api/projects/default/candidates/{candidate['id']}?expected_revision={candidate['revision']}"
    )

    assert response.status_code == 204
    archived = client.get(
        f"/api/projects/default/candidates/{candidate['id']}?include_archived=true"
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
