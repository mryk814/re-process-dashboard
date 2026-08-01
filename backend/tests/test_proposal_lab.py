from __future__ import annotations

import json

import pytest

from decision_workbench.application.proposal_lab import _run_metric
from decision_workbench.contracts.screening_contracts import ScreeningRunResponse
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.store_support import StoreDataIntegrityError


def _create_run(client, *, strategy_id: str, seed: int) -> dict:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    proposal = {
        "strategy_id": strategy_id,
        "exploration_parameter": 1.5,
        "pool_multiplier": 2,
        "support_policy": "supported_first",
        "fallback_policy": "reject",
        "incumbent_value": 500,
    }
    response = client.post(
        "/api/screening",
        json={
            "purpose": "goal_search",
            "base_candidate_id": candidate["id"],
            "base_inputs": candidate["inputs"],
            "samples": 48,
            "seed": seed,
            "target": "TS",
            "target_goal": {"direction": "at_least", "lower": 500},
            "variables": {
                "composition.C": {"mode": "range", "min": 0.04, "max": 0.12},
                "process.ls_mpm": {"mode": "range", "min": 80, "max": 130},
            },
            "proposal": proposal,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_proposal_lab_saves_reproducible_multi_seed_adoption_evidence(client) -> None:
    runs = [
        _create_run(client, strategy_id=strategy_id, seed=seed)
        for strategy_id in ("sobol_ucb_v1", "sobol_ei_v1")
        for seed in (31, 47)
    ]
    request = {
        "run_ids": [run["id"] for run in runs],
        "evaluation_fixture_version": "saved-screening-replay/v1",
        "adoption_memos": [
            {
                "strategy_id": "sobol_ei_v1",
                "status": "experimental",
                "primary_criterion": "goal achievement and support",
                "rationale": "二つのseedで支持範囲外率を確認してから継続評価する",
                "trade_offs": ["incumbent固定が必要", "normal mean/std限定"],
            }
        ],
    }
    response = client.post(
        "/api/projects/default/proposal-lab/reports", json=request
    )
    assert response.status_code == 201, response.text
    report = response.json()
    assert report["protocol"]["seeds"] == [31, 47]
    assert report["protocol"]["generator_id"] == "sobol"
    assert report["protocol"]["proposal_count"] == 5
    assert report["protocol"]["runtime_capability_digest"].startswith("sha256:")
    assert report["protocol"]["incumbent_resolution_digest"].startswith("sha256:")
    assert report["protocol"]["training_identity_kind"] in {
        "training_snapshot",
        "legacy_training_data",
    }
    assert report["protocol"]["constraint_scope"] == (
        "known_design_space_and_outcome_only"
    )
    assert {item["strategy_id"] for item in report["strategy_summaries"]} == {
        "sobol_ucb_v1",
        "sobol_ei_v1",
    }
    assert all(
        item["acquisition_scope"] == "marginal"
        for item in report["strategy_summaries"]
    )
    assert {
        item["acquisition_id"] for item in report["strategy_summaries"]
    } == {"upper_confidence_bound", "expected_improvement"}
    assert all(
        item["model_call_count"] in {1, item["evaluated_count"]}
        for item in report["runs"]
    )
    assert all(item["constraint_unknown_rate"] == 0 for item in report["runs"])
    assert all(item["runtime_ms"] >= 0 for item in report["runs"])
    assert report["adoption_memos"][0]["registry_changed"] is False
    assert report["adoption_memos"][0]["status"] == "experimental"
    assert "joint acquisition" in report["limitations"][-1]

    repeated = client.post(
        "/api/projects/default/proposal-lab/reports", json=request
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == report["id"]

    listed = client.get("/api/projects/default/proposal-lab/reports")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [report["id"]]


def test_proposal_lab_rejects_unaligned_seed_sets(client) -> None:
    runs = [
        _create_run(client, strategy_id="sobol_ucb_v1", seed=31),
        _create_run(client, strategy_id="sobol_ucb_v1", seed=47),
        _create_run(client, strategy_id="sobol_ei_v1", seed=31),
        _create_run(client, strategy_id="sobol_ei_v1", seed=53),
    ]
    response = client.post(
        "/api/projects/default/proposal-lab/reports",
        json={
            "run_ids": [run["id"] for run in runs],
            "adoption_memos": [
                {
                    "strategy_id": "sobol_ei_v1",
                    "status": "no_adopt",
                    "primary_criterion": "support",
                    "rationale": "seed条件が揃っていない",
                }
            ],
        },
    )
    assert response.status_code == 422
    assert "同じ2個以上のseed" in response.text


def test_proposal_lab_migration_is_additive_for_existing_workspace(tmp_path) -> None:
    database = tmp_path / "workspace.db"
    store = Store(database)
    existing = store.create_screening_run(
        {"schema_version": "legacy-migration-evidence"},
        "default",
    )
    with store._connect() as connection:
        connection.execute("DROP TABLE proposal_lab_reports")

    upgraded = Store(database)
    assert upgraded.get_screening_run(existing["id"], "default") == existing
    with upgraded._connect() as connection:
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(proposal_lab_reports)"
            )
        }
    assert columns == {"id", "project_id", "payload", "created_at"}


def test_proposal_lab_separates_unknown_constraint_from_feasible(client) -> None:
    raw = _create_run(client, strategy_id="sobol_ucb_v1", seed=31)
    selected_indices = {
        item["point_index"] for item in raw["proposal_selection"]["selected"]
    }
    for point in raw["points"]:
        if point["index"] in selected_indices:
            point["secondary_goal_evaluations"] = {
                "unknown_feasibility": {
                    "score": None,
                    "method": "directional_shortfall",
                    "achieved": None,
                    "achievement_probability": None,
                }
            }
    metric = _run_metric(ScreeningRunResponse.model_validate(raw))
    assert metric.feasible_rate == 0
    assert metric.constraint_unknown_rate == 1


def test_proposal_lab_rejects_mixed_versions_and_removed_registry_ids(client) -> None:
    runs = [
        _create_run(client, strategy_id=strategy_id, seed=seed)
        for strategy_id in ("sobol_ucb_v1", "sobol_ei_v1")
        for seed in (31, 47)
    ]
    request = {
        "run_ids": [run["id"] for run in runs],
        "adoption_memos": [
            {
                "strategy_id": "sobol_ei_v1",
                "status": "experimental",
                "primary_criterion": "support",
                "rationale": "version identity test",
            }
        ],
    }
    store = client.app.state.store

    def replace_payload(run_id: str, transform) -> None:
        run = store.get_screening_run(run_id, "default")
        assert run is not None
        payload = {
            key: value
            for key, value in run.items()
            if key not in {"id", "project_id", "created_at"}
        }
        transform(payload)
        with store._connect() as connection:
            connection.execute(
                "UPDATE screening_runs SET payload=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), run_id),
            )

    replace_payload(
        runs[0]["id"],
        lambda payload: payload["proposal_strategy"].update(
            {"version": "mixed-version"}
        ),
    )
    mixed = client.post(
        "/api/projects/default/proposal-lab/reports", json=request
    )
    assert mixed.status_code == 422
    assert "version" in mixed.text

    replace_payload(
        runs[0]["id"],
        lambda payload: payload["proposal_strategy"].update(
            {"version": runs[0]["proposal_strategy"]["version"]}
        ),
    )
    for run in runs[2:]:
        replace_payload(
            run["id"],
            lambda payload: payload["proposal_strategy"].update(
                {"id": "removed_strategy_v1"}
            ),
        )
    request["adoption_memos"][0]["strategy_id"] = "removed_strategy_v1"
    removed = client.post(
        "/api/projects/default/proposal-lab/reports", json=request
    )
    assert removed.status_code == 422
    assert "registryにないProposal Strategy" in removed.text


def test_proposal_lab_report_id_collision_fails_closed(tmp_path) -> None:
    store = Store(tmp_path / "collision.db")
    shared_prefix = "a" * 24
    first_digest = f"sha256:{shared_prefix}{'1' * 40}"
    colliding_digest = f"sha256:{shared_prefix}{'2' * 40}"
    store.create_proposal_lab_report(
        project_id="default",
        payload={"report_digest": first_digest},
    )
    with pytest.raises(StoreDataIntegrityError, match="full digest"):
        store.create_proposal_lab_report(
            project_id="default",
            payload={"report_digest": colliding_digest},
        )
