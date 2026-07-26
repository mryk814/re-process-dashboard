from material_workbench.application.objective_execution import (
    build_objective_execution_plan,
)
from material_workbench.contracts.objective_contracts import (
    ObjectiveDefinition,
    ObjectiveIncumbent,
    ObjectiveTerm,
)


def _objective(*terms: ObjectiveTerm, kind: str = "single_objective") -> ObjectiveDefinition:
    return ObjectiveDefinition(
        objective_id="test-objective",
        name="test",
        task_id="task",
        task_contract_digest="sha256:test",
        optimization_kind=kind,
        terms=terms,
    )


def test_objective_execution_translates_primary_constraints_and_reporting() -> None:
    objective = _objective(
        ObjectiveTerm(
            output_key="TS",
            unit="MPa",
            role="primary_objective",
            direction="at_least",
            lower=500,
        ),
        ObjectiveTerm(
            output_key="YS",
            unit="MPa",
            role="hard_outcome_constraint",
            direction="between",
            lower=300,
            upper=450,
        ),
        ObjectiveTerm(output_key="EL", unit="%", role="reporting_only"),
        kind="constrained_single_objective",
    )

    plan = build_objective_execution_plan(objective)

    assert plan.target == "TS"
    assert plan.target_goal.model_dump() == {
        "direction": "at_least",
        "lower": 500,
        "upper": None,
    }
    assert plan.secondary_goals["YS"].direction == "between"
    assert plan.evidence.hard_constraint_outputs == ("YS",)
    assert plan.evidence.reporting_outputs == ("EL",)


def test_objective_execution_rejects_contract_only_objective_kinds() -> None:
    pareto = _objective(
        ObjectiveTerm(
            output_key="TS",
            unit="MPa",
            role="primary_objective",
            direction="maximize",
        ),
        ObjectiveTerm(
            output_key="EL",
            unit="%",
            role="primary_objective",
            direction="maximize",
        ),
        kind="pareto_multi_objective",
    )

    try:
        build_objective_execution_plan(pareto)
    except ValueError as exc:
        assert "Pareto" in str(exc)
    else:
        raise AssertionError("Pareto Objective must not be advertised as executable")


def test_observed_best_records_project_actual_population_evidence(client) -> None:
    project = client.get("/api/projects/default").json()
    candidate = client.get("/api/projects/default/candidates").json()[0]
    actuals = []
    for index, mean in enumerate((505.0, 540.0), start=1):
        response = client.post(
            f"/api/projects/default/candidates/{candidate['id']}/actuals",
            params={"expected_revision": candidate["revision"]},
            json={
                "property": "TS",
                "mean": mean,
                "std": 2,
                "replicates": 3,
                "unit": "MPa",
                "experiment_no": f"INC-{index}",
            },
        )
        assert response.status_code == 201, response.text
        actuals.append(response.json())
    objective = ObjectiveDefinition(
        objective_id="observed-best-test",
        name="Project実測の最良値",
        task_id=project["task_id"],
        task_contract_digest=project["task_contract_digest"],
        optimization_kind="single_objective",
        terms=(
            ObjectiveTerm(
                output_key="TS",
                unit="MPa",
                role="primary_objective",
                direction="at_least",
                lower=500,
            ),
        ),
        incumbent=ObjectiveIncumbent(
            source="observed_best",
            observed_scope="project_actuals",
        ),
    )
    response = client.post(
        "/api/screening?project_id=default",
        json={
            "base_candidate_id": candidate["id"],
            "base_inputs": candidate["inputs"],
            "samples": 48,
            "target": "YS",
            "target_goal": {"direction": "at_most", "upper": 1},
            "variables": {
                "composition.C": {"mode": "range", "min": 0.05, "max": 0.12}
            },
            "objective_definition": objective.model_dump(mode="json"),
        },
    )

    assert response.status_code == 201, response.text
    run = response.json()
    assert run["target"] == "TS"
    resolution = run["proposal_strategy"]["incumbent_resolution"]
    assert resolution["source"] == "observed_project_actuals"
    assert resolution["value"] == 540
    assert resolution["actual_id"] == actuals[1]["id"]
    assert resolution["record_count"] == 2
    assert resolution["filter_digest"].startswith("sha256:")
    assert resolution["population_digest"].startswith("sha256:")


def test_request_override_is_saved_as_manual_incumbent_provenance(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    response = client.post(
        "/api/screening?project_id=default",
        json={
            "base_candidate_id": candidate["id"],
            "base_inputs": candidate["inputs"],
            "samples": 48,
            "target": "TS",
            "target_goal": {"direction": "at_least", "lower": 500},
            "variables": {
                "composition.C": {"mode": "range", "min": 0.05, "max": 0.12}
            },
            "proposal": {"incumbent_value": 515},
        },
    )

    assert response.status_code == 201, response.text
    resolution = response.json()["proposal_strategy"]["incumbent_resolution"]
    assert resolution["source"] == "request_override"
    assert resolution["value"] == 515
