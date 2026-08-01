import pytest

from decision_workbench.contracts.objective_contracts import (
    ObjectiveDefinition,
    ObjectiveIncumbent,
    ObjectiveTerm,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.contracts.task_contracts import persisted_task_definition_payload
from decision_workbench.tasks.task_registry import load_task_contracts


def _contract():
    return load_task_contracts()["annealed-properties-v1"]


def _definition(*terms: ObjectiveTerm, kind: str = "single_objective") -> ObjectiveDefinition:
    fixture = _contract()
    return ObjectiveDefinition(
        objective_id="objective-test",
        name="Objective検証",
        task_id=fixture.task_definition.id,
        task_contract_digest=semantic_digest(
            persisted_task_definition_payload(fixture.task_definition)
        ),
        optimization_kind=kind,  # type: ignore[arg-type]
        terms=terms,
    )


def test_objective_distinguishes_single_constrained_and_pareto_semantics() -> None:
    fixture = _contract()
    primary = ObjectiveTerm(
        output_key="TS",
        unit="MPa",
        role="primary_objective",
        direction="maximize",
    )
    single = _definition(primary)
    single.validate_against(fixture.task_definition, fixture.runtime_capability)

    hard = ObjectiveTerm(
        output_key="EL",
        unit="%",
        role="hard_outcome_constraint",
        direction="at_least",
        lower=30,
    )
    constrained = _definition(primary, hard, kind="constrained_single_objective")
    constrained.validate_against(
        fixture.task_definition, fixture.runtime_capability
    )

    second_primary = hard.model_copy(
        update={"role": "primary_objective", "direction": "maximize", "lower": None}
    )
    pareto = _definition(primary, second_primary, kind="pareto_multi_objective")
    pareto.validate_against(fixture.task_definition, fixture.runtime_capability)
    assert len({single.digest, constrained.digest, pareto.digest}) == 3


def test_objective_rejects_output_unit_and_direction_drift() -> None:
    fixture = _contract()
    missing = _definition(
        ObjectiveTerm(
            output_key="unknown",
            unit="MPa",
            role="primary_objective",
            direction="maximize",
        )
    )
    with pytest.raises(ValueError, match="Taskにありません"):
        missing.validate_against(fixture.task_definition, fixture.runtime_capability)

    wrong_unit = _definition(
        ObjectiveTerm(
            output_key="TS",
            unit="%",
            role="primary_objective",
            direction="maximize",
        )
    )
    with pytest.raises(ValueError, match="単位"):
        wrong_unit.validate_against(
            fixture.task_definition, fixture.runtime_capability
        )

    wrong_direction = _definition(
        ObjectiveTerm(
            output_key="TS",
            unit="MPa",
            role="primary_objective",
            direction="minimize",
        )
    )
    with pytest.raises(ValueError, match="方向"):
        wrong_direction.validate_against(
            fixture.task_definition, fixture.runtime_capability
        )


def test_objective_incumbent_requires_an_immutable_reference() -> None:
    with pytest.raises(ValueError, match="revision"):
        ObjectiveIncumbent(source="candidate_revision", candidate_id="candidate")
    incumbent = ObjectiveIncumbent(
        source="candidate_revision",
        candidate_id="candidate",
        candidate_revision=3,
    )
    assert incumbent.candidate_revision == 3
