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


def _hindsight_project(client, name: str) -> str:
    return _project_with_objective(client, name)


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


def _case_payload(candidates, snapshots, cutoff, *, no_decision=False):
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
        attachment_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(decision_case_actual_attachments)"
            )
        }
    assert marker == ("append-only-decision-case-and-replay-run-v1",)
    assert {"semantic_identity", "decision_timestamp", "payload"} <= case_columns
    assert {"semantic_identity", "case_id", "payload"} <= run_columns
    assert {"case_id", "actual_id", "candidate_revision", "payload"} <= attachment_columns

    assert not any(
        name.startswith(("update_decision_", "delete_decision_"))
        for name in dir(Store)
    )


def test_decision_case_and_replay_separate_historical_from_retrospective_evidence(client) -> None:
    project_id = _project_with_objective(client, "Replay fixture")
    candidates, snapshots = _prepare_historical_candidates(client, project_id)
    cutoff = datetime.now(UTC).isoformat()
    created = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(candidates, snapshots, cutoff),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert created.status_code == 201, created.text
    case = created.json()
    assert case["schema_version"] == "decision-case/v1"
    assert len(case["historical_evidence"]) == 2
    assert case["selection"]["status"] == "selected"
    assert case["rationale"]["actor_id"] == "local-researcher"
    assert all(
        item["snapshot_created_at"] <= case["decision_timestamp"]
        for item in case["historical_evidence"]
    )
    historical_case = case.copy()
    # The Case exists before any Actual. The later Actual is connected only by
    # a separate append-only attachment and never changes the Case payload.
    actual_response = client.post(
        f"/api/projects/{project_id}/candidates/{candidates[0]['id']}/actuals",
        params={"expected_revision": candidates[0]["revision"]},
        json={"property": "TS", "mean": 510.0, "unit": "MPa"},
    )
    assert actual_response.status_code == 201, actual_response.text
    actual = actual_response.json()
    attached = client.post(
        f"/api/projects/{project_id}/decision-cases/{case['id']}/actual-attachments",
        json={
            "schema_version": "decision-case-actual-attachment-create/v1",
            "actual_measurement_id": actual["id"],
        },
    )
    assert attached.status_code == 201, attached.text
    attachment = attached.json()
    assert attachment["actual"]["id"] == actual["id"]
    assert attachment["candidate"]["candidate_revision"] == candidates[0]["revision"]
    assert client.get(
        f"/api/projects/{project_id}/decision-cases/{case['id']}"
    ).json() == historical_case
    assert client.get(
        f"/api/projects/{project_id}/decision-cases/{case['id']}/actual-attachments"
    ).json() == [attachment]
    duplicate = client.post(
        f"/api/projects/{project_id}/decision-cases/{case['id']}/actual-attachments",
        json={
            "schema_version": "decision-case-actual-attachment-create/v1",
            "actual_measurement_id": actual["id"],
        },
    )
    assert duplicate.status_code == 422
    assert "すでにDecision Caseへ追加" in duplicate.text

    hindsight_project_id = _hindsight_project(client, "Replay hindsight fixture")
    options = client.get(
        f"/api/projects/{project_id}/decision-cases/{case['id']}/hindsight-project-options"
    )
    assert options.status_code == 200, options.text
    assert hindsight_project_id in {item["project_id"] for item in options.json()}

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

    replay = client.post(
        f"/api/projects/{project_id}/decision-cases/{case['id']}/replay-runs",
        json={
            "schema_version": "decision-replay-request/v1",
            "alternative_policy": "primary-objective-point-estimate/v1",
            "hindsight_project_id": hindsight_project_id,
        },
    )
    assert replay.status_code == 201, replay.text
    run = replay.json()
    assert run["schema_version"] == "decision-replay-run/v1"
    assert len(run["result"]["historical"]) == 2
    assert [item["target"] for item in run["result"]["realized_outcomes"]] == [
        "TS"
    ]
    historical_ts = next(
        item for item in case["historical_evidence"]
        if item["candidate"]["candidate_id"] == candidates[0]["id"]
    )["predictions"]["TS"]["value"]
    assert run["result"]["realized_outcomes"][0]["predicted_value"] == historical_ts
    assert run["result"]["realized_outcomes"][0]["absolute_error"] == abs(
        510.0 - historical_ts
    )
    assert run["result"]["unobserved_targets"] == ["EL"]
    assert run["result"]["alternative_selection"] is not None
    assert run["result"]["actual_attachments"] == [attachment]
    assert run["result"]["realized_outcomes"][0]["attachment_id"] == attachment["id"]
    assert run["result"]["hindsight_project"]["project_id"] == hindsight_project_id
    assert len(run["result"]["hindsight_reevaluation"]) == 2
    assert {
        item["evidence_layer"]
        for item in run["result"]["hindsight_reevaluation"]
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
    created = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(candidates, snapshots, after_actual),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert cutoff_after_snapshots <= after_actual
    assert created.status_code == 201, created.text
    invalid_actual = client.post(
        f"/api/projects/{project_id}/decision-cases/{created.json()['id']}/actual-attachments",
        json={
            "schema_version": "decision-case-actual-attachment-create/v1",
            "actual_measurement_id": actual["id"],
        },
    )
    assert invalid_actual.status_code == 422
    assert "判断時刻以前のActual" in invalid_actual.text


def test_decision_case_rejects_actual_from_a_later_candidate_revision(client) -> None:
    project_id = _project_with_objective(client, "Replay revision fixture")
    candidates, snapshots = _prepare_historical_candidates(client, project_id)
    cutoff = datetime.now(UTC).isoformat()
    original = candidates[0]
    updated_response = client.put(
        f"/api/projects/{project_id}/candidates/{original['id']}",
        json={
            "name": f"{original['name']} updated",
            "inputs": original["inputs"],
            "provenance": original["provenance"],
            "expected_revision": original["revision"],
        },
    )
    assert updated_response.status_code == 200, updated_response.text
    updated = updated_response.json()
    assert updated["revision"] == original["revision"] + 1
    later_actual_response = client.post(
        f"/api/projects/{project_id}/candidates/{original['id']}/actuals",
        params={"expected_revision": updated["revision"]},
        json={"property": "TS", "mean": 520.0, "unit": "MPa"},
    )
    assert later_actual_response.status_code == 201, later_actual_response.text

    case = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(candidates, snapshots, cutoff),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert case.status_code == 201, case.text
    response = client.post(
        f"/api/projects/{project_id}/decision-cases/{case.json()['id']}/actual-attachments",
        json={
            "schema_version": "decision-case-actual-attachment-create/v1",
            "actual_measurement_id": later_actual_response.json()["id"],
        },
    )
    assert response.status_code == 422
    assert "固定Candidate revision" in response.text


def test_decision_replay_rejects_missing_stale_and_incompatible_hindsight_projects(client) -> None:
    project_id = _project_with_objective(client, "Replay hindsight guard fixture")
    candidates, snapshots = _prepare_historical_candidates(client, project_id)
    created = client.post(
        f"/api/projects/{project_id}/decision-cases",
        json=_case_payload(candidates, snapshots, datetime.now(UTC).isoformat()),
        headers={"X-Workbench-Human-Actor": "local-researcher"},
    )
    assert created.status_code == 201, created.text
    case_id = created.json()["id"]
    base_request = {
        "schema_version": "decision-replay-request/v1",
        "alternative_policy": "primary-objective-point-estimate/v1",
    }
    stale = client.post(
        f"/api/projects/{project_id}/decision-cases/{case_id}/replay-runs",
        json={**base_request, "hindsight_project_id": project_id},
    )
    assert stale.status_code == 422
    assert "別の後発Project" in stale.text
    missing = client.post(
        f"/api/projects/{project_id}/decision-cases/{case_id}/replay-runs",
        json={**base_request, "hindsight_project_id": "missing-hindsight-project"},
    )
    assert missing.status_code == 404

    reference = client.get("/api/projects/default").json()
    incompatible = client.post(
        "/api/projects",
        json={
            "name": "Objective-incompatible hindsight Project",
            "task_id": reference["task_id"],
            "dataset_view_revision_id": reference["dataset_view_revision_id"],
            "model_package_ref_id": reference["model_package_ref_id"],
            "target_values": {"TS": 530},
        },
    )
    assert incompatible.status_code == 201, incompatible.text
    incompatible_replay = client.post(
        f"/api/projects/{project_id}/decision-cases/{case_id}/replay-runs",
        json={**base_request, "hindsight_project_id": incompatible.json()["id"]},
    )
    assert incompatible_replay.status_code == 422
    assert "Objective" in incompatible_replay.text
