from types import SimpleNamespace

import numpy as np
import pytest

from material_workbench.application.proposal_strategy_registry import (
    resolve_strategy,
    strategy_availability,
)
from material_workbench.contracts.objective_contracts import objective_from_screening
from material_workbench.contracts.proposal_contracts import ProposalStrategyRequest
from material_workbench.contracts.schemas import ScreeningGoal
from material_workbench.domain.proposal_acquisition import acquisition_value
from material_workbench.domain.proposal_generation import _latin_hypercube_unit, _sobol_unit
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.tasks.task_registry import load_task_contracts


def _prediction(mean: float, sigma: float) -> SimpleNamespace:
    return SimpleNamespace(
        value=mean,
        lower=mean - 1.6448536269514722 * sigma,
        upper=mean + 1.6448536269514722 * sigma,
        uncertainty_components={"total": sigma},
        goal_probability=None,
    )


def _objective():
    contract = load_task_contracts()["annealed-properties-v1"]
    return objective_from_screening(
        task=contract.task_definition,
        task_contract_digest=semantic_digest(
            contract.task_definition.model_dump(mode="json")
        ),
        target="TS",
        target_goal=ScreeningGoal(direction="at_least", lower=500),
        secondary_goals={},
    )


def test_sobol_is_seeded_bounded_and_reproducible() -> None:
    first = _sobol_unit(19, 3, 42)
    second = _sobol_unit(19, 3, 42)
    other = _sobol_unit(19, 3, 43)
    assert first.tolist() == second.tolist()
    assert first.tolist() != other.tolist()
    assert first.shape == (19, 3)
    assert ((0 <= first) & (first < 1)).all()


def test_latin_hypercube_keeps_the_legacy_seed_sequence() -> None:
    count = 11
    dimensions = 4
    seed = 20260719
    rng = np.random.default_rng(seed)
    legacy_columns = []
    for _ in range(dimensions):
        permutation = rng.permutation(count)
        legacy_columns.append(
            np.array(
                [
                    (permutation[index] + rng.random()) / count
                    for index in range(count)
                ]
            )
        )
    legacy = np.column_stack(legacy_columns)
    assert _latin_hypercube_unit(count, dimensions, seed).tolist() == legacy.tolist()


def test_ucb_and_expected_improvement_rank_promising_uncertain_points() -> None:
    goal = ScreeningGoal(direction="at_least", lower=500)
    safe_ucb, _ = acquisition_value(
        "upper_confidence_bound",
        prediction=_prediction(510, 2),
        goal=goal,
        support_distance=0,
        exploration_parameter=2,
        incumbent_value=None,
    )
    exploratory_ucb, _ = acquisition_value(
        "upper_confidence_bound",
        prediction=_prediction(507, 5),
        goal=goal,
        support_distance=0,
        exploration_parameter=2,
        incumbent_value=None,
    )
    assert exploratory_ucb < safe_ucb
    weak_ei, _ = acquisition_value(
        "expected_improvement",
        prediction=_prediction(505, 1),
        goal=goal,
        support_distance=0,
        exploration_parameter=2,
        incumbent_value=510,
    )
    strong_ei, components = acquisition_value(
        "expected_improvement",
        prediction=_prediction(509, 6),
        goal=goal,
        support_distance=0,
        exploration_parameter=2,
        incumbent_value=510,
    )
    assert strong_ei < weak_ei
    assert components["expected_improvement"] > 0


def test_registry_explains_capability_and_changes_fallback_identity() -> None:
    contract = load_task_contracts()["annealed-properties-v1"]
    objective = _objective()
    availability = {
        item.definition.strategy_id: item
        for item in strategy_availability(
            contract.runtime_capability,
            target="TS",
            objective=objective,
        )
    }
    assert availability["sobol_ucb_v1"].available
    assert not availability["sobol_ei_v1"].available
    assert "incumbent" in availability["sobol_ei_v1"].reasons[0]
    assert not availability["sobol_thompson_v1"].available
    assert availability["sobol_thompson_v1"].reasons
    actual, fallback_from = resolve_strategy(
        ProposalStrategyRequest(
            strategy_id="sobol_thompson_v1",
            fallback_policy="deterministic_goal",
        ),
        contract.runtime_capability,
        target="TS",
        objective=objective,
    )
    assert actual.strategy_id == "latin_hypercube_v1"
    assert fallback_from == "sobol_thompson_v1"


def test_api_persists_complete_acquisition_pool_and_is_reproducible(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    body = {
        "base_candidate_id": candidate["id"],
        "base_inputs": candidate["inputs"],
        "samples": 48,
        "seed": 197,
        "target": "TS",
        "target_goal": {"direction": "at_least", "lower": 500},
        "variables": {
            "composition.C": {"mode": "range", "min": 0.04, "max": 0.12},
            "process.ls_mpm": {"mode": "range", "min": 80, "max": 130},
        },
        "proposal": {
            "strategy_id": "sobol_ucb_v1",
            "exploration_parameter": 1.5,
            "pool_multiplier": 2,
            "support_policy": "supported_first",
            "fallback_policy": "reject",
        },
    }
    first_response = client.post("/api/screening", json=body)
    second_response = client.post("/api/screening", json=body)
    assert first_response.status_code == 201, first_response.text
    assert second_response.status_code == 201, second_response.text
    first = first_response.json()
    second = second_response.json()
    assert first["proposal_strategy"]["id"] == "sobol_ucb_v1"
    assert first["proposal_strategy"]["generator_id"] == "sobol"
    assert first["proposal_strategy"]["acquisition_id"] == "upper_confidence_bound"
    assert first["proposal_strategy"]["uncertainty_treatment"] == "predictive_standard_deviation"
    assert first["proposal_strategy"]["constraint_treatment"] == "feasibility_first_then_rank"
    assert first["proposal_diagnostics"]["evaluated_count"] == len(first["proposal_pool"])
    assert first["proposal_diagnostics"]["selected_count"] == 48
    assert [item["inputs"] for item in first["proposal_pool"]] == [
        item["inputs"] for item in second["proposal_pool"]
    ]
    assert [item["acquisition_score"] for item in first["proposal_pool"]] == [
        item["acquisition_score"] for item in second["proposal_pool"]
    ]
    assert all(
        item["acquisition_components"]["method"] == "ucb"
        for item in first["proposal_pool"]
    )


def test_api_reports_strategy_unavailability_and_records_real_fallback(client) -> None:
    availability = client.get(
        "/api/projects/default/proposal-strategies",
        params={"target": "TS"},
    )
    assert availability.status_code == 200
    by_id = {
        item["definition"]["strategy_id"]: item
        for item in availability.json()
    }
    assert by_id["sobol_ucb_v1"]["available"]
    assert not by_id["sobol_thompson_v1"]["available"]

    candidate = client.get("/api/projects/default/candidates").json()[0]
    body = {
        "base_candidate_id": candidate["id"],
        "base_inputs": candidate["inputs"],
        "samples": 48,
        "seed": 198,
        "target": "TS",
        "target_goal": {"direction": "at_least", "lower": 500},
        "variables": {
            "composition.C": {"mode": "range", "min": 0.04, "max": 0.12},
        },
        "proposal": {
            "strategy_id": "sobol_thompson_v1",
            "pool_multiplier": 2,
            "fallback_policy": "deterministic_goal",
        },
    }
    response = client.post("/api/screening", json=body)
    assert response.status_code == 201, response.text
    strategy = response.json()["proposal_strategy"]
    assert strategy["id"] == "latin_hypercube_v1"
    assert strategy["fallback_from"] == "sobol_thompson_v1"
