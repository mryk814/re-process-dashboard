from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.persistence.project_persistence_inventory import (
    PROJECT_PERSISTENCE,
    assert_project_persistence_inventory_complete,
    project_scoped_tables_from_schema,
)
from decision_workbench.persistence.sqlite_connection import sqlite_connection

ELEMENTS = (
    "C",
    "Si",
    "Mn",
    "P",
    "S",
    "Al",
    "Cu",
    "Ni",
    "Cr",
    "Mo",
    "Ti",
    "B",
    "O",
    "N",
)


def _candidate(name: str) -> dict:
    return {
        "name": name,
        "inputs": {
            "composition": {
                **{key: 0.0 for key in ELEMENTS},
                "C": 0.08,
                "Si": 0.3,
                "Mn": 1.5,
            },
            "process": {"ls_mpm": 103.0},
            "categorical": {},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 25},
                {"time_s": 300, "temperature_c": 810},
                {"time_s": 650, "temperature_c": 120},
            ],
        },
    }


def _project(client, name: str) -> dict:
    reference = client.get("/api/projects/default").json()
    return {
        "name": name,
        "description": "独立した検討",
        "purpose": "プロジェクト分離の確認",
        "task_id": reference["task_id"],
        "target_values": {"TS": 500},
        "notes": "",
        "dataset_view_revision_id": reference["dataset_view_revision_id"],
        "model_package_ref_id": reference["model_package_ref_id"],
    }


def _project_with_objective(client, name: str) -> str:
    response = client.post("/api/projects", json=_project(client, name))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _prepare_historical_candidates(client, project_id: str):
    source = client.get("/api/projects/default/candidates").json()[0]
    candidates = []
    snapshots = []
    for name in ("判断候補A", "判断候補B"):
        candidate_response = client.post(
            f"/api/projects/{project_id}/candidates",
            json={
                "name": name,
                "inputs": source["inputs"],
                "provenance": {"source_kind": "direct", "source_ref": None},
            },
        )
        assert candidate_response.status_code == 201, candidate_response.text
        candidate = candidate_response.json()
        snapshot_response = client.post(
            f"/api/projects/{project_id}/candidates/{candidate['id']}/predict",
            params={"expected_revision": candidate["revision"]},
        )
        assert snapshot_response.status_code == 200, snapshot_response.text
        candidates.append(candidate)
        snapshots.append(snapshot_response.json()["snapshot"])
    return candidates, snapshots


def _case_payload(candidates, snapshots, cutoff: str) -> dict:
    return {
        "schema_version": "decision-case-create/v1",
        "decision_timestamp": cutoff,
        "candidates": [
            {
                "candidate_id": candidate["id"],
                "candidate_revision": candidate["revision"],
            }
            for candidate in candidates
        ],
        "snapshot_ids": [snapshot["id"] for snapshot in snapshots],
        "selection": {
            "status": "selected",
            "candidate": {
                "candidate_id": candidates[0]["id"],
                "candidate_revision": candidates[0]["revision"],
            },
        },
        "rationale": {
            "disposition": "selected",
            "rationale": "当時の支持範囲と目的値を確認した記録",
        },
        "outcome_policy": {
            "schema_version": "decision-outcome-policy/v1",
            "target_keys": ["TS", "EL"],
            "missing_actual_policy": "retain_partial",
        },
    }


def test_project_persistence_inventory_covers_schema_and_archive_guards(
    client,
) -> None:
    with sqlite_connection(client.app.state.store.path) as connection:
        assert_project_persistence_inventory_complete(connection)
        assert project_scoped_tables_from_schema(connection) == frozenset(
            PROJECT_PERSISTENCE.project_owned_tables
            + PROJECT_PERSISTENCE.control_tables
        )
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }

    for table in PROJECT_PERSISTENCE.direct_tables:
        if table == "projects":
            assert "guard_archived_project_update" in triggers
            assert "guard_project_delete" in triggers
            continue
        for operation in ("insert", "update", "delete"):
            assert f"guard_archived_{table}_{operation}" in triggers
    for table in PROJECT_PERSISTENCE.case_tables:
        for operation in ("insert", "update", "delete"):
            assert f"guard_archived_{table}_{operation}" in triggers
    for table in PROJECT_PERSISTENCE.candidate_tables:
        for operation in ("insert", "update", "delete"):
            assert f"guard_archived_{table}_{operation}" in triggers
    for table in PROJECT_PERSISTENCE.scope_tables:
        for operation in ("insert", "update", "delete"):
            assert f"guard_archived_{table}_{operation}" in triggers

    assert PROJECT_PERSISTENCE.external_reference_scans == (
        ("candidate_revisions", "payload"),
    )


def test_unregistered_project_scoped_table_fails_inventory_contract(client) -> None:
    with sqlite_connection(client.app.state.store.path) as connection:
        connection.execute(
            "CREATE TABLE project_scope_fixture ("
            "id TEXT PRIMARY KEY, project_id TEXT NOT NULL)"
        )
        with pytest.raises(AssertionError, match="project_scope_fixture"):
            assert_project_persistence_inventory_complete(connection)


def test_decision_case_attachment_follows_project_archive_and_purge(client) -> None:
    project_id = _project_with_objective(client, "Decision Case lifecycle")
    candidates, snapshots = _prepare_historical_candidates(client, project_id)
    case_response = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(candidates, snapshots, datetime.now(UTC).isoformat()),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert case_response.status_code == 201, case_response.text
    case_id = case_response.json()["id"]
    actuals = []
    for target, value, unit in (("TS", 510.0, "MPa"), ("EL", 21.0, "%")):
        response = client.post(
            f"/api/projects/{project_id}/candidates/{candidates[0]['id']}/actuals",
            params={"expected_revision": candidates[0]["revision"]},
            json={"property": target, "mean": value, "unit": unit},
        )
        assert response.status_code == 201, response.text
        actuals.append(response.json())
    attachment_response = client.post(
        f"/api/projects/{project_id}/decision-cases/{case_id}/actual-attachments",
        json={
            "schema_version": "decision-case-actual-attachment-create/v1",
            "actual_measurement_id": actuals[0]["id"],
        },
    )
    assert attachment_response.status_code == 201, attachment_response.text
    attachment_id = attachment_response.json()["id"]
    hindsight_project_id = _project_with_objective(
        client, "Decision Case lifecycle hindsight"
    )
    run_response = client.post(
        f"/api/projects/{project_id}/decision-cases/{case_id}/replay-runs",
        json={
            "schema_version": "decision-replay-request/v1",
            "alternative_policy": "primary-objective-point-estimate/v1",
            "hindsight_project_id": hindsight_project_id,
        },
    )
    assert run_response.status_code == 201, run_response.text
    run_id = run_response.json()["id"]

    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    store = client.app.state.store
    with sqlite_connection(store.path) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="project_archived"):
            connection.execute(
                "INSERT INTO decision_case_actual_attachments("
                "id,semantic_identity,case_id,actual_id,candidate_id,candidate_revision,"
                "prediction_snapshot_id,payload,attached_at) "
                "SELECT ?,?,case_id,?,candidate_id,candidate_revision,"
                "prediction_snapshot_id,payload,? "
                "FROM decision_case_actual_attachments WHERE id=?",
                (
                    "late-attachment",
                    "sha256:late-attachment",
                    actuals[1]["id"],
                    datetime.now(UTC).isoformat(),
                    attachment_id,
                ),
            )
        with pytest.raises(sqlite3.IntegrityError, match="project_archived"):
            connection.execute(
                "UPDATE decision_case_actual_attachments SET payload=? WHERE id=?",
                ("{}", attachment_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="project_archived"):
            connection.execute(
                "DELETE FROM decision_case_actual_attachments WHERE id=?",
                (attachment_id,),
            )

    purge = client.delete(
        f"/api/projects/{project_id}/purge",
        params={"confirm_project_id": project_id},
    )
    assert purge.status_code == 204, purge.text
    with sqlite_connection(store.path) as connection:
        assert connection.execute(
            "SELECT 1 FROM decision_case_actual_attachments WHERE id=?",
            (attachment_id,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM decision_replay_runs WHERE id=?", (run_id,)
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM decision_cases WHERE id=?", (case_id,)
        ).fetchone() is None
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_archive_preserves_chain_evidence_and_purge_removes_all(client) -> None:
    project = client.post(
        "/api/projects", json=_project(client, "Chain証跡ライフサイクル")
    ).json()
    candidate = client.post(
        f"/api/projects/{project['id']}/candidates",
        json=_candidate("証跡候補"),
    ).json()
    store = client.app.state.store
    scope_id = f"{project['id']}:{candidate['id']}"
    now = project["created_at"]
    with sqlite_connection(store.path) as connection:
        connection.execute(
            "INSERT INTO chain_snapshot_records VALUES (?,?,?,?,?,?,?)",
            (
                "chain-snapshot-lifecycle",
                project["id"],
                candidate["id"],
                candidate["revision"],
                "{}",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO chain_distribution_runs VALUES (?,?,?,?,?,?,?)",
            (
                "chain-distribution-lifecycle",
                project["id"],
                candidate["id"],
                candidate["revision"],
                "chain-digest",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO chain_analysis_variant_records VALUES (?,?,?,?,?,?,?)",
            (
                "chain-variant-lifecycle",
                project["id"],
                candidate["id"],
                candidate["revision"],
                "comparison-snapshot",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO chain_execution_state VALUES (?,?,?,?)",
            (scope_id, "request-before-archive", "{}", now),
        )
        connection.execute(
            "INSERT INTO chain_execution_claims VALUES (?,?,?,?)",
            (scope_id, "request-before-archive", 1, now),
        )

    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    with pytest.raises(sqlite3.IntegrityError, match="project_archived"):
        store.create_candidate(
            CandidateInput.model_validate(_candidate("遅延書き込み")),
            project["id"],
        )
    with pytest.raises(sqlite3.IntegrityError, match="project_archived"):
        store.delete_candidate(candidate["id"], project["id"], candidate["revision"])
    with sqlite_connection(store.path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM candidate_revisions WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0]
            == 1
        )
        for table in (
            "chain_snapshot_records",
            "chain_distribution_runs",
            "chain_analysis_variant_records",
        ):
            assert (
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE project_id=?",
                    (project["id"],),
                ).fetchone()[0]
                == 1
            )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chain_execution_state WHERE scope_id=?",
                (scope_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chain_execution_claims WHERE scope_id=?",
                (scope_id,),
            ).fetchone()[0]
            == 0
        )
        with pytest.raises(sqlite3.IntegrityError, match="project_archived"):
            connection.execute(
                "INSERT INTO chain_execution_claims VALUES (?,?,?,?)",
                (scope_id, "late-claim", 1, now),
            )
        with pytest.raises(sqlite3.IntegrityError, match="project_archived"):
            connection.execute(
                "UPDATE chain_execution_state SET request_id=? WHERE scope_id=?",
                ("late-state", scope_id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="project_archived"):
            connection.execute(
                "DELETE FROM chain_execution_state WHERE scope_id=?",
                (scope_id,),
            )

    active_purge = client.post(f"/api/projects/{project['id']}/restore")
    assert active_purge.status_code == 200
    assert (
        client.delete(
            f"/api/projects/{project['id']}/purge",
            params={"confirm_project_id": project["id"]},
        ).status_code
        == 409
    )
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert (
        client.delete(
            f"/api/projects/{project['id']}/purge",
            params={"confirm_project_id": "wrong"},
        ).status_code
        == 409
    )
    assert (
        client.delete(
            f"/api/projects/{project['id']}/purge",
            params={"confirm_project_id": project["id"]},
        ).status_code
        == 204
    )

    with sqlite_connection(store.path) as connection:
        for table in (
            "projects",
            "candidates",
            "candidate_revisions",
            "chain_snapshot_records",
            "chain_distribution_runs",
            "chain_analysis_variant_records",
        ):
            column = "id" if table == "projects" else "project_id"
            assert (
                connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column}=?",
                    (project["id"],),
                ).fetchone()[0]
                == 0
            )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM chain_execution_state WHERE scope_id=?",
                (scope_id,),
            ).fetchone()[0]
            == 0
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_archive_invalidates_an_in_flight_chain_writer(client) -> None:
    project = client.post(
        "/api/projects", json=_project(client, "Chain writer失効")
    ).json()
    candidate = client.post(
        f"/api/projects/{project['id']}/candidates",
        json=_candidate("実行候補"),
    ).json()
    store = client.app.state.store
    generation = store.claim_chain_execution(
        project["id"],
        candidate["id"],
        candidate["revision"],
        "request-before-archive",
    )
    assert generation == 1
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert (
        store.chain_execution_generation(
            project["id"], candidate["id"], "request-before-archive"
        )
        is None
    )
    assert (
        store.claim_chain_execution(
            project["id"],
            candidate["id"],
            candidate["revision"],
            "request-after-archive",
        )
        is None
    )


def test_archive_rolls_back_claim_revocation_when_project_update_fails(client) -> None:
    project = client.post(
        "/api/projects", json=_project(client, "Chain archive atomicity")
    ).json()
    candidate = client.post(
        f"/api/projects/{project['id']}/candidates",
        json=_candidate("実行候補"),
    ).json()
    store = client.app.state.store
    request_id = "request-before-failed-archive"
    assert (
        store.claim_chain_execution(
            project["id"],
            candidate["id"],
            candidate["revision"],
            request_id,
        )
        == 1
    )
    with sqlite_connection(store.path) as connection:
        connection.execute(
            "CREATE TRIGGER reject_test_archive "
            "BEFORE UPDATE OF archived_at ON projects "
            f"WHEN NEW.id='{project['id']}' "
            "BEGIN SELECT RAISE(ABORT,'injected_archive_failure'); END"
        )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="injected_archive_failure"):
            store.archive_project(project["id"])
    finally:
        with sqlite_connection(store.path) as connection:
            connection.execute("DROP TRIGGER reject_test_archive")

    assert store.get_project(project["id"]).archived_at is None
    assert (
        store.chain_execution_generation(
            project["id"],
            candidate["id"],
            request_id,
        )
        == 1
    )


def test_purge_rejects_non_copy_cross_project_provenance(client) -> None:
    source_project = client.post(
        "/api/projects", json=_project(client, "Snapshot証跡元")
    ).json()
    source_candidate = client.post(
        f"/api/projects/{source_project['id']}/candidates",
        json=_candidate("Snapshot元候補"),
    ).json()
    dependent_project = client.post(
        "/api/projects", json=_project(client, "Snapshot証跡利用先")
    ).json()
    snapshot_id = "cross-project-snapshot"
    with sqlite_connection(client.app.state.store.path) as connection:
        connection.execute(
            "INSERT INTO snapshots(id,candidate_id,payload,created_at) "
            "VALUES (?,?,?,?)",
            (
                snapshot_id,
                source_candidate["id"],
                "{}",
                source_project["created_at"],
            ),
        )
    dependent_payload = {
        **_candidate("Snapshot由来候補"),
        "provenance": {
            "source_kind": "snapshot",
            "source_ref": {"snapshot_id": snapshot_id},
        },
    }
    assert (
        client.post(
            f"/api/projects/{dependent_project['id']}/candidates",
            json=dependent_payload,
        ).status_code
        == 201
    )
    assert client.delete(f"/api/projects/{source_project['id']}").status_code == 204
    rejected = client.delete(
        f"/api/projects/{source_project['id']}/purge",
        params={"confirm_project_id": source_project["id"]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "project_has_derived_candidates"
