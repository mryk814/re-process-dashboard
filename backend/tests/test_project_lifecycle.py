from __future__ import annotations

import sqlite3

import pytest

from material_workbench.contracts.schemas import CandidateInput
from material_workbench.persistence.project_persistence_inventory import (
    PROJECT_PERSISTENCE,
    assert_project_persistence_inventory_complete,
    project_scoped_tables_from_schema,
)
from material_workbench.persistence.sqlite_connection import sqlite_connection

from backend.tests.test_projects import _candidate, _project


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
    for table in PROJECT_PERSISTENCE.candidate_tables:
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
        store.delete_candidate(
            candidate["id"], project["id"], candidate["revision"]
        )
    with sqlite_connection(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM candidate_revisions WHERE project_id=?",
            (project["id"],),
        ).fetchone()[0] == 1
        for table in (
            "chain_snapshot_records",
            "chain_distribution_runs",
            "chain_analysis_variant_records",
        ):
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE project_id=?",
                (project["id"],),
            ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM chain_execution_state WHERE scope_id=?",
            (scope_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM chain_execution_claims WHERE scope_id=?",
            (scope_id,),
        ).fetchone()[0] == 0

    active_purge = client.post(f"/api/projects/{project['id']}/restore")
    assert active_purge.status_code == 200
    assert client.delete(
        f"/api/projects/{project['id']}/purge",
        params={"confirm_project_id": project["id"]},
    ).status_code == 409
    assert client.delete(f"/api/projects/{project['id']}").status_code == 204
    assert client.delete(
        f"/api/projects/{project['id']}/purge",
        params={"confirm_project_id": "wrong"},
    ).status_code == 409
    assert client.delete(
        f"/api/projects/{project['id']}/purge",
        params={"confirm_project_id": project["id"]},
    ).status_code == 204

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
            assert connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column}=?",
                (project["id"],),
            ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM chain_execution_state WHERE scope_id=?",
            (scope_id,),
        ).fetchone()[0] == 0
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
    assert client.post(
        f"/api/projects/{dependent_project['id']}/candidates",
        json=dependent_payload,
    ).status_code == 201
    assert client.delete(
        f"/api/projects/{source_project['id']}"
    ).status_code == 204
    rejected = client.delete(
        f"/api/projects/{source_project['id']}/purge",
        params={"confirm_project_id": source_project["id"]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "project_has_derived_candidates"
