from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from material_workbench.application.decision_activities import _with_values
from material_workbench.contracts.schemas import Candidate
from material_workbench.persistence.store import Store


ELEMENTS = ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")


def _candidate_payload() -> dict:
    return {
        "name": "ロバストネス確認",
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
                {"time_s": 280, "temperature_c": 800},
                {"time_s": 340, "temperature_c": 810},
                {"time_s": 650, "temperature_c": 120},
            ],
        },
    }


def _run_payload(revision: int, *, amount: float = 0.002, seed: int = 17) -> dict:
    return {
        "expected_revision": revision,
        "parameters": {
            "sample_count": 8,
            "seed": seed,
            "tolerance_profile": {
                "fields": {
                    "composition.C": {"kind": "absolute", "amount": amount},
                },
            },
        },
    }


def test_decision_activity_migration_is_additive_and_idempotent(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    Store(database)

    with sqlite3.connect(database) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(decision_activity_runs)")
        }
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id='decision-activity-run-v1'"
        ).fetchone()

    assert {
        "id", "semantic_identity", "project_id", "candidate_id",
        "activity_id", "activity_version", "payload", "created_at",
    } <= columns
    assert marker == ("immutable-decision-activity-run-v1",)


def test_line_speed_tolerance_preserves_physical_heat_positions() -> None:
    now = datetime.now(UTC)
    candidate = Candidate.model_validate({
        **_candidate_payload(),
        "id": "candidate",
        "project_id": "default",
        "revision": 1,
        "created_at": now,
        "updated_at": now,
    })

    varied = _with_values(candidate, {"process.ls_mpm": 206.0})

    assert [point.time_s for point in varied.inputs.heat_pattern or []] == [
        0.0, 140.0, 170.0, 325.0,
    ]


def test_robustness_activity_is_available_deterministic_and_persistent(client) -> None:
    candidate = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    availability = client.get(
        "/api/projects/default/decision-activities",
        params={
            "candidate_id": candidate["id"],
            "expected_revision": candidate["revision"],
        },
    )

    assert availability.status_code == 200
    assert availability.json()[0]["definition"]["activity_id"] == "robustness-analysis-v1"
    assert availability.json()[0]["available"] is True

    url = (
        f"/api/projects/default/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )
    first = client.post(url, json=_run_payload(candidate["revision"]))
    second = client.post(url, json=_run_payload(candidate["revision"]))

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json() == second.json()
    run = first.json()
    assert run["provenance"]["candidate_revision"] == candidate["revision"]
    assert run["provenance"]["model_package_digest"]
    assert run["provenance"]["feature_pipeline_digest"]
    assert run["result"]["accepted_samples"] == 8
    assert run["result"]["target_summaries"]
    target = run["result"]["target_summaries"][0]
    assert set(target) >= {"input_variation", "model_uncertainty"}
    assert target["input_variation"]["coverage"] == "central_90_percent"
    assert target["model_uncertainty"]["semantics"] == "runtime_predictive_interval"
    assert "因果効果ではありません" in " ".join(run["result"]["warnings"])

    listed = client.get(
        "/api/projects/default/decision-activity-runs",
        params={"candidate_id": candidate["id"]},
    )
    restored = client.get(
        f"/api/projects/default/decision-activity-runs/{run['id']}"
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [run["id"]]
    assert restored.json() == run


def test_robustness_activity_rejects_out_of_task_range_without_clipping(client) -> None:
    candidate = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    url = (
        f"/api/projects/default/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )

    response = client.post(
        url,
        json=_run_payload(candidate["revision"], amount=1_000.0),
    )

    assert response.status_code == 422
    assert "許容範囲を超えています" in response.json()["message"]


def test_activity_run_keeps_candidate_as_an_archived_record(client) -> None:
    candidate = client.post(
        "/api/projects/default/candidates", json=_candidate_payload()
    ).json()
    run_url = (
        f"/api/projects/default/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )
    assert client.post(
        run_url, json=_run_payload(candidate["revision"])
    ).status_code == 201

    deleted = client.delete(
        f"/api/projects/default/candidates/{candidate['id']}",
        params={"expected_revision": candidate["revision"]},
    )
    archived = client.get(
        f"/api/projects/default/candidates/{candidate['id']}",
        params={"include_archived": True},
    )

    assert deleted.status_code == 204
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None


def test_robustness_activity_uses_only_the_declared_composition_balance(client) -> None:
    project = next(
        item for item in client.get("/api/projects").json()
        if item["task_id"] == "mpea-room-tensile-v1"
    )
    candidate = client.get(
        f"/api/projects/{project['id']}/candidates"
    ).json()[0]
    url = (
        f"/api/projects/{project['id']}/candidates/{candidate['id']}"
        "/decision-activities/robustness-analysis-v1/runs"
    )
    payload = {
        "expected_revision": candidate["revision"],
        "parameters": {
            "sample_count": 8,
            "seed": 4,
            "tolerance_profile": {
                "fields": {
                    "composition.Ni": {"kind": "absolute", "amount": 0.2},
                },
            },
        },
    }

    accepted = client.post(url, json=payload)
    direct_balance = client.post(
        url,
        json={
            **payload,
            "parameters": {
                **payload["parameters"],
                "tolerance_profile": {
                    "fields": {
                        "composition.Fe": {"kind": "absolute", "amount": 0.2},
                    },
                },
            },
        },
    )

    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["result"]["accepted_samples"] == 8
    assert direct_balance.status_code == 422
    assert "balance項目" in direct_balance.json()["message"]
