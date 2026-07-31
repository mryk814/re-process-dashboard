from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from decision_workbench.application.decision_activity_counterfactual import (
    compute,
    prepare,
)
from decision_workbench.application.decision_activity_registry import ActivityContext
from decision_workbench.contracts.decision_activity_contracts import (
    COUNTERFACTUAL_ACTIVITY,
    CounterfactualParameters,
    CounterfactualTargetEvaluation,
)
from decision_workbench.contracts.design_space_contracts import (
    DesignSpaceDefinition,
    NumericDomain,
)
from decision_workbench.contracts.objective_contracts import (
    ObjectiveDefinition,
    ObjectiveTerm,
)
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.task_contracts import (
    CANONICAL_CANDIDATE_SCHEMA_VERSION,
    TASK_DEFINITION_SCHEMA_VERSION,
    InputFieldDefinition,
    InputGroupDefinition,
    NumericRange,
    OutputDefinition,
    TaskDefinition,
)
from decision_workbench.task_composition.candidate_family_adapters import (
    CANONICAL_CANDIDATE_ADAPTER,
)
from decision_workbench.execution.inference_work_graph import semantic_digest


def _toy_contracts():
    bounds = NumericRange(min=0, max=10)
    task = TaskDefinition(
        schema_version=TASK_DEFINITION_SCHEMA_VERSION,
        id="counterfactual-toy-v1",
        label="反実仮想toy",
        canonical_candidate_schema_version=CANONICAL_CANDIDATE_SCHEMA_VERSION,
        input_groups=(
            InputGroupDefinition(
                key="composition",
                order=0,
                label="入力",
                fields=(
                    InputFieldDefinition(
                        path="composition.x",
                        kind="number",
                        order=0,
                        label="X",
                        unit="a.u.",
                        default_range=bounds,
                        allowed_range=bounds,
                        training_range=bounds,
                    ),
                    InputFieldDefinition(
                        path="composition.z",
                        kind="number",
                        order=1,
                        label="Z",
                        unit="a.u.",
                        default_range=bounds,
                        allowed_range=bounds,
                        training_range=bounds,
                    ),
                ),
            ),
        ),
        outputs=(
            OutputDefinition(
                key="Y",
                label="応答",
                unit="a.u.",
                goal_direction="at_least",
                plausibility_range=NumericRange(min=0, max=30),
                preferred_display_range=NumericRange(min=0, max=20),
            ),
        ),
        display_decimals={
            "composition.x": 3,
            "composition.z": 3,
            "output.Y": 3,
        },
    )
    digest = semantic_digest(task.model_dump(mode="json"))
    space = DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="toy-space",
        name="toy",
        task_id=task.id,
        task_contract_digest=digest,
        numeric_domains=(
            NumericDomain(path="composition.x", mode="range", range=bounds),
            NumericDomain(path="composition.z", mode="range", range=bounds),
        ),
    )
    objective = ObjectiveDefinition(
        objective_id="toy-objective",
        name="Y >= 5",
        task_id=task.id,
        task_contract_digest=digest,
        optimization_kind="single_objective",
        terms=(
            ObjectiveTerm(
                output_key="Y",
                unit="a.u.",
                role="primary_objective",
                direction="at_least",
                lower=5,
            ),
        ),
    )
    return task, space, objective


class _LinearRuntime:
    def predict_core(self, candidate, **_kwargs):
        value = 2 * candidate.inputs.composition["x"] + candidate.inputs.composition["z"]
        return {
            "predictions": {
                "Y": {
                    "value": value,
                    "lower": value - 0.1,
                    "upper": value + 0.1,
                    "unit": "a.u.",
                    "target_kind": "continuous",
                    "point_statistic": "mean",
                    "predictive_family": "normal",
                    "quantiles": {"0.05": value - 0.1, "0.95": value + 0.1},
                },
            },
            "model_meta": {},
        }

    def support_summary(self, _candidate):
        return {
            "status": "supported",
            "distance": 0,
            "percentile": 0,
            "message": "",
            "components": {},
            "reference_count": 10,
            "supported_threshold": 1,
            "caution_threshold": 2,
        }


def test_counterfactual_finds_the_known_minimal_change_and_is_deterministic() -> None:
    task, space, objective = _toy_contracts()
    now = datetime.now(UTC)
    candidate = Candidate.model_validate(
        {
            "id": "base",
            "project_id": "toy-project",
            "revision": 1,
            "name": "base",
            "inputs": {
                "composition": {"x": 0.0, "z": 0.0},
                "process": {},
            },
            "created_at": now,
            "updated_at": now,
        }
    )
    project = SimpleNamespace(
        id="toy-project",
        task_id=task.id,
        target_values={"Y": 5.0},
        design_space=space,
        design_space_digest=space.digest,
        objective_definition=objective,
        objective_definition_digest=objective.digest,
    )
    parameters = CounterfactualParameters(
        sample_count=48,
        result_count=3,
        seed=11,
        max_changed_fields=2,
    )

    def validate(item: Candidate) -> None:
        space.validate_against(task)
        for value in item.inputs.composition.values():
            if not 0 <= value <= 10:
                raise ValueError("outside")

    context = ActivityContext(
        project=project,
        candidate=candidate,
        task_definition=task,
        candidate_family=CANONICAL_CANDIDATE_ADAPTER,
        runtime=_LinearRuntime(),
        parameters=parameters,
        validate_candidate=validate,
        resolve_candidate=lambda _id, _revision: candidate,
    )
    prepared = prepare(context)
    first = compute(context, prepared).result
    second = compute(context, prepared).result

    assert first == second
    assert first.status == "feasible"
    best = first.proposals[0]
    assert best.meets_objective is True
    assert best.changed_field_count == 1
    assert best.changes[0].path == "composition.x"
    assert best.changes[0].proposed_value == pytest.approx(2.5, abs=1e-6)
    assert best.change_distance == pytest.approx(0.25, abs=1e-6)
    target = best.target_evaluations[0]
    assert target.achieved is True
    assert target.shortfall == 0
    assert target.prediction is not None
    assert target.prediction.value == target.predicted_value
    assert target.prediction.quantiles == pytest.approx(
        {
            "0.05": target.predicted_value - 0.1,
            "0.95": target.predicted_value + 0.1,
        }
    )
    assert COUNTERFACTUAL_ACTIVITY.version == "1.1.0"


def test_counterfactual_target_evaluation_reads_legacy_results_without_interval() -> None:
    legacy = CounterfactualTargetEvaluation.model_validate(
        {
            "target": "Y",
            "unit": "a.u.",
            "predicted_value": 5.0,
            "achieved": True,
            "normalized_shortfall": 0.0,
            "role": "primary_objective",
        }
    )

    assert legacy.prediction is None
    assert legacy.shortfall is None


def _copy_project_with_target(client, target: float):
    reference = client.get("/api/projects/default").json()
    base = client.get("/api/projects/default/candidates").json()[0]
    response = client.post(
        "/api/projects",
        json={
            "name": "目標到達案の検証",
            "task_id": reference["task_id"],
            "dataset_view_revision_id": reference["dataset_view_revision_id"],
            "model_package_ref_id": reference["model_package_ref_id"],
            "target_values": {"TS": target},
            "initial_candidate": {
                "name": "基準",
                "inputs": base["inputs"],
                "provenance": {
                    "source_kind": "copy",
                    "source_ref": {
                        "project_id": "default",
                        "candidate_id": base["id"],
                        "candidate_revision": base["revision"],
                    },
                },
            },
        },
    )
    assert response.status_code == 201, response.text
    project = response.json()
    candidate = client.get(
        f"/api/projects/{project['id']}/candidates"
    ).json()[0]
    return project, candidate


def test_counterfactual_run_pins_evidence_and_promotes_only_the_selected_proposal(
    client,
) -> None:
    # The starter now comes from an observed, supported training condition.
    # 550 MPa keeps this fixed exercise reachable within its pinned Design Space.
    project, candidate = _copy_project_with_target(client, 550)
    url = (
        f"/api/projects/{project['id']}/candidates/{candidate['id']}"
        "/decision-activities/counterfactual-target-reach-v1/runs"
    )
    payload = {
        "expected_revision": candidate["revision"],
        "parameters": {
            "schema_version": "counterfactual-parameters/v1",
            "sample_count": 48,
            "result_count": 3,
            "seed": 7,
            "max_changed_fields": 4,
            "categorical_change_penalty": 1,
            "immutable_paths": [],
        },
    }

    first = client.post(url, json=payload)
    second = client.post(url, json=payload)

    assert first.status_code == 201, first.text
    assert first.json() == second.json()
    run = first.json()
    result = run["result"]
    assert result["status"] == "feasible"
    assert result["proposals"]
    assert result["design_space_digest"] == project["design_space_digest"]
    assert (
        result["objective_definition_digest"]
        == project["objective_definition_digest"]
    )
    assert run["provenance"]["candidate_revision"] == candidate["revision"]
    assert (
        run["provenance"]["objective_definition_digest"]
        == project["objective_definition_digest"]
    )
    legacy_run_id = f"legacy-{run['id']}"
    with sqlite3.connect(client.app.state.store.path) as connection:
        stored_payload = json.loads(
            connection.execute(
                "SELECT payload FROM decision_activity_runs WHERE id = ?",
                (run["id"],),
            ).fetchone()[0]
        )
        stored_payload["definition"]["version"] = "1.0.0"
        stored_payload["provenance"]["activity_version"] = "1.0.0"
        for proposal in stored_payload["result"]["proposals"]:
            for evaluation in proposal["target_evaluations"]:
                evaluation.pop("prediction", None)
                evaluation.pop("shortfall", None)
        connection.execute(
            """
            UPDATE decision_activity_runs
            SET id = ?, semantic_identity = ?, activity_version = ?, payload = ?
            WHERE id = ?
            """,
            (
                legacy_run_id,
                f"legacy-{run['id']}",
                "1.0.0",
                json.dumps(stored_payload),
                run["id"],
            ),
        )

    restored_legacy = client.get(
        f"/api/projects/{project['id']}/decision-activity-runs/{legacy_run_id}"
    )
    assert restored_legacy.status_code == 200
    legacy_target = restored_legacy.json()["result"]["proposals"][0][
        "target_evaluations"
    ][0]
    assert legacy_target["prediction"] is None
    assert legacy_target["shortfall"] is None

    current_version = client.post(url, json=payload)
    assert current_version.status_code == 201, current_version.text
    assert current_version.json()["id"] != legacy_run_id
    assert current_version.json()["definition"]["version"] == "1.1.0"
    assert current_version.json()["result"]["proposals"][0]["target_evaluations"][0][
        "prediction"
    ] is not None
    before = client.get(
        f"/api/projects/{project['id']}/candidates"
    ).json()

    selected = result["proposals"][0]
    promoted = client.post(
        f"/api/projects/{project['id']}/decision-activity-runs/{legacy_run_id}"
        f"/proposals/{selected['proposal_id']}/candidate"
    )

    assert promoted.status_code == 201, promoted.text
    created = promoted.json()
    assert created["inputs"] == selected["inputs"]
    assert created["provenance"]["source_kind"] == "decision_activity"
    assert created["provenance"]["source_ref"]["run_id"] == legacy_run_id
    after = client.get(f"/api/projects/{project['id']}/candidates").json()
    assert len(after) == len(before) + 1
    repeated = client.post(
        f"/api/projects/{project['id']}/decision-activity-runs/{legacy_run_id}"
        f"/proposals/{selected['proposal_id']}/candidate"
    )
    assert repeated.status_code == 201
    assert repeated.json()["id"] == created["id"]
    assert len(client.get(f"/api/projects/{project['id']}/candidates").json()) == len(
        after
    )


def test_counterfactual_reports_infeasible_without_creating_candidates(client) -> None:
    project, candidate = _copy_project_with_target(client, 10_000)
    before = client.get(f"/api/projects/{project['id']}/candidates").json()
    response = client.post(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}"
        "/decision-activities/counterfactual-target-reach-v1/runs",
        json={
            "expected_revision": candidate["revision"],
            "parameters": {
                "schema_version": "counterfactual-parameters/v1",
                "sample_count": 48,
                "result_count": 3,
                "seed": 8,
                "max_changed_fields": 4,
                "categorical_change_penalty": 1,
                "immutable_paths": [],
            },
        },
    )

    assert response.status_code == 201, response.text
    result = response.json()["result"]
    assert result["status"] == "infeasible"
    assert result["proposals"] == []
    assert result["infeasibility"]
    assert len(client.get(f"/api/projects/{project['id']}/candidates").json()) == len(
        before
    )
