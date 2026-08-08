from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from decision_workbench.persistence.store import Store


def _candidate_payload(source: dict, name: str) -> dict:
    return {
        "name": name,
        "inputs": source["inputs"],
        "provenance": {"source_kind": "direct", "source_ref": None},
    }


def _project_with_objective(client, name: str) -> str:
    reference = client.get("/api/projects/default").json()
    created = client.post(
        "/api/projects",
        json={
            "name": name,
            "task_id": reference["task_id"],
            "dataset_view_revision_id": reference["dataset_view_revision_id"],
            "model_package_ref_id": reference["model_package_ref_id"],
            "target_values": {"TS": 500},
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _prepare_historical_candidates(client, project_id: str):
    source = client.get("/api/projects/default/candidates").json()[0]
    candidates = [
        client.post(
            f"/api/projects/{project_id}/candidates",
            json=_candidate_payload(source, name),
        ).json()
        for name in ("判断候補A", "判断候補B")
    ]
    snapshots = [
        client.post(
            f"/api/projects/{project_id}/candidates/{candidate['id']}/predict",
            params={"expected_revision": candidate["revision"]},
        ).json()["snapshot"]
        for candidate in candidates
    ]
    return candidates, snapshots


def _case_payload(candidates, snapshots, cutoff, *, actual_ids=(), no_decision=False):
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
        "selection": (
            {"status": "no_decision", "candidate": None}
            if no_decision
            else {
                "status": "selected",
                "candidate": {
                    "candidate_id": candidates[0]["id"],
                    "candidate_revision": candidates[0]["revision"],
                },
            }
        ),
        "rationale": (
            {
                "disposition": "no_decision" if no_decision else "selected",
                "rationale": "当時の支持範囲と目的値を確認した記録",
            }
        ),
        "actual_measurement_ids": list(actual_ids),
        "outcome_policy": {
            "schema_version": "decision-outcome-policy/v1",
            "target_keys": ["TS", "EL"],
            "missing_actual_policy": "retain_partial",
        },
    }


def test_decision_replay_migration_is_additive_idempotent_and_insert_only(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    Store(database)
    with sqlite3.connect(database) as conn:
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id='decision-replay-v1'"
        ).fetchone()
        case_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(decision_cases)")
        }
        run_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(decision_replay_runs)")
        }
    assert marker == ("append-only-decision-case-and-replay-run-v1",)
    assert {"semantic_identity", "decision_timestamp", "payload"} <= case_columns
    assert {"semantic_identity", "case_id", "payload"} <= run_columns

    assert not any(
        name.startswith(("update_decision_", "delete_decision_"))
        for name in dir(Store)
    )


def test_decision_case_and_replay_separate_historical_from_retrospective_evidence(client) -> None:
    project_id = _project_with_objective(client, "Replay fixture")
    candidates, snapshots = _prepare_historical_candidates(client, project_id)
    cutoff = datetime.now(UTC).isoformat()
    actual_response = client.post(
        f"/api/projects/{project_id}/candidates/{candidates[0]['id']}/actuals",
        params={"expected_revision": candidates[0]["revision"]},
        json={"property": "TS", "mean": 510.0, "unit": "MPa"},
    )
    assert actual_response.status_code == 201, actual_response.text
    actual = actual_response.json()

    created = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(
            candidates, snapshots, cutoff, actual_ids=(actual["id"],)
        ),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert created.status_code == 201, created.text
    case = created.json()
    assert case["schema_version"] == "decision-case/v1"
    assert len(case["historical_evidence"]) == 2
    assert len(case["retrospective_actuals"]) == 1
    assert case["selection"]["status"] == "selected"
    assert case["rationale"]["actor_id"] == "local-researcher"
    assert all(
        item["snapshot_created_at"] <= case["decision_timestamp"]
        for item in case["historical_evidence"]
    )
    assert (
        case["retrospective_actuals"][0]["actual"]["created_at"]
        > case["decision_timestamp"]
    )

    no_decision = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(candidates, snapshots, cutoff, no_decision=True),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert no_decision.status_code == 201, no_decision.text
    assert no_decision.json()["selection"] == {
        "status": "no_decision",
        "candidate": None,
    }
    assert no_decision.json()["retrospective_actuals"] == []

    replay = client.post(
        f"/api/projects/{project_id}/decision-cases/{case['id']}/replay-runs",
        json={
            "schema_version": "decision-replay-request/v1",
            "alternative_policy": "primary-objective-point-estimate/v1",
        },
    )
    assert replay.status_code == 201, replay.text
    run = replay.json()
    assert run["schema_version"] == "decision-replay-run/v1"
    assert len(run["result"]["historical"]) == 2
    assert [item["target"] for item in run["result"]["realized_outcomes"]] == [
        "TS"
    ]
    assert run["result"]["unobserved_targets"] == ["EL"]
    assert run["result"]["alternative_selection"] is not None
    assert len(run["result"]["current_package_reevaluation"]) == 2
    assert {
        item["evidence_layer"]
        for item in run["result"]["current_package_reevaluation"]
    } == {"hindsight"}
    assert [item["case_id"] for item in run["result"]["similar_cases"]] == [
        no_decision.json()["id"]
    ]
    assert run["result"]["similar_cases"][0]["snapshot_ids"] == [
        snapshot["id"] for snapshot in snapshots
    ]

    assert client.get(f"/api/projects/{project_id}/decision-cases").json()[0]["id"] in {
        case["id"], no_decision.json()["id"]
    }
    listed_runs = client.get(
        f"/api/projects/{project_id}/decision-replay-runs",
        params={"case_id": case["id"]},
    )
    assert [item["id"] for item in listed_runs.json()] == [run["id"]]


def test_decision_case_rejects_hindsight_snapshot_and_predecision_actual(client) -> None:
    project_id = _project_with_objective(client, "Replay cutoff fixture")
    candidates, snapshots = _prepare_historical_candidates(client, project_id)
    cutoff_before_snapshot = candidates[0]["updated_at"]
    hindsight = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(candidates, snapshots, cutoff_before_snapshot),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert hindsight.status_code == 422
    assert "historical evidenceへ含められません" in hindsight.text

    cutoff_after_snapshots = datetime.now(UTC).isoformat()
    # Deliberately create an Actual first, then put the decision timestamp after it.
    actual = client.post(
        f"/api/projects/{project_id}/candidates/{candidates[0]['id']}/actuals",
        params={"expected_revision": candidates[0]["revision"]},
        json={"property": "TS", "mean": 500.0, "unit": "MPa"},
    ).json()
    after_actual = datetime.now(UTC).isoformat()
    invalid_actual = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(
            candidates, snapshots, after_actual, actual_ids=(actual["id"],)
        ),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert cutoff_after_snapshots <= after_actual
    assert invalid_actual.status_code == 422
    assert "判断時刻以前のActual" in invalid_actual.text
