"""Translate an immutable Objective Definition into the production proposal engine."""
from __future__ import annotations

from dataclasses import dataclass

from material_workbench.contracts.objective_contracts import (
    ObjectiveDefinition,
    ObjectiveTerm,
)
from material_workbench.contracts.proposal_contracts import ProposalObjectiveExecution
from material_workbench.contracts.schemas import ScreeningGoal


@dataclass(frozen=True)
class ObjectiveExecutionPlan:
    target: str
    target_goal: ScreeningGoal | None
    secondary_goals: dict[str, ScreeningGoal]
    evidence: ProposalObjectiveExecution
    target_term: ObjectiveTerm


def _goal(term: ObjectiveTerm) -> ScreeningGoal:
    if term.direction not in {"at_least", "at_most", "between"}:
        raise ValueError(
            f"Objective方向「{term.direction}」はProposal engineでまだ実行できません: "
            f"{term.output_key}"
        )
    return ScreeningGoal(
        direction=term.direction,
        lower=term.lower,
        upper=term.upper,
    )


def build_objective_execution_plan(
    objective: ObjectiveDefinition,
) -> ObjectiveExecutionPlan:
    """Return only meanings the current deterministic proposal engine implements."""

    if objective.optimization_kind == "pareto_multi_objective":
        raise ValueError(
            "Pareto multi-objectiveはProposal engineでまだ実行できません"
        )
    soft = [term.output_key for term in objective.terms if term.role == "soft_preference"]
    if soft:
        raise ValueError(
            "soft preferenceはProposal engineでまだ実行できません: "
            + ", ".join(soft)
        )

    primary = [
        term for term in objective.terms if term.role == "primary_objective"
    ]
    reporting = [
        term for term in objective.terms if term.role == "reporting_only"
    ]
    hard = [
        term for term in objective.terms if term.role == "hard_outcome_constraint"
    ]
    if primary:
        if len(primary) != 1:
            raise ValueError("Proposal engineで実行できる主目的は1件です")
        target_term = primary[0]
        target_goal = _goal(target_term)
    else:
        if objective.optimization_kind != "legacy_screening" or not reporting:
            raise ValueError("Proposal engineへ渡す主対象outputを決定できません")
        target_term = reporting[0]
        target_goal = None

    secondary_goals = {term.output_key: _goal(term) for term in hard}
    reporting_outputs = tuple(
        term.output_key for term in reporting if term.output_key != target_term.output_key
    )
    return ObjectiveExecutionPlan(
        target=target_term.output_key,
        target_goal=target_goal,
        secondary_goals=secondary_goals,
        target_term=target_term,
        evidence=ProposalObjectiveExecution(
            objective_digest=objective.digest,
            target=target_term.output_key,
            direction=target_goal.direction if target_goal is not None else None,
            hard_constraint_outputs=tuple(secondary_goals),
            reporting_outputs=reporting_outputs,
        ),
    )
