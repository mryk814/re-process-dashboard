"""Typed Proposal Strategy registry and Runtime Capability checks."""
from __future__ import annotations

from material_workbench.contracts.objective_contracts import ObjectiveDefinition
from material_workbench.contracts.proposal_contracts import (
    ProposalStrategyAvailability,
    ProposalStrategyDefinition,
    ProposalStrategyRequest,
)
from material_workbench.contracts.task_contracts import RuntimeCapability


STRATEGIES = (
    ProposalStrategyDefinition(
        strategy_id="latin_hypercube_v1",
        version="1.0.0",
        label="Latin hypercube・目標基準",
        generator_id="latin_hypercube",
        generator_version="1.0.0",
        acquisition_id="goal_achievement",
        acquisition_version="1.0.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
    ),
    ProposalStrategyDefinition(
        strategy_id="sobol_ucb_v1",
        version="1.0.0",
        label="Sobol・UCB/LCB",
        generator_id="sobol",
        generator_version="1.0.0",
        acquisition_id="upper_confidence_bound",
        acquisition_version="1.0.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
        requires_standard_deviation=True,
    ),
    ProposalStrategyDefinition(
        strategy_id="sobol_ei_v1",
        version="1.0.0",
        label="Sobol・Expected Improvement",
        generator_id="sobol",
        generator_version="1.0.0",
        acquisition_id="expected_improvement",
        acquisition_version="1.0.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
        requires_standard_deviation=True,
        requires_incumbent=True,
    ),
    ProposalStrategyDefinition(
        strategy_id="sobol_thompson_v1",
        version="0.1.0",
        label="Sobol・Thompson Sampling",
        generator_id="sobol",
        generator_version="1.0.0",
        acquisition_id="thompson_sampling",
        acquisition_version="0.1.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
        requires_samples=True,
        production_enabled=False,
    ),
    ProposalStrategyDefinition(
        strategy_id="sobol_uncertainty_v1",
        version="0.1.0",
        label="Sobol・不確かさ探索",
        generator_id="sobol",
        generator_version="1.0.0",
        acquisition_id="uncertainty_sampling",
        acquisition_version="0.1.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
        requires_standard_deviation=True,
        production_enabled=False,
    ),
    ProposalStrategyDefinition(
        strategy_id="sobol_support_boundary_v1",
        version="0.1.0",
        label="Sobol・学習支持境界",
        generator_id="sobol",
        generator_version="1.0.0",
        acquisition_id="support_boundary_sampling",
        acquisition_version="0.1.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
        production_enabled=False,
    ),
)


def strategy_availability(
    capability: RuntimeCapability,
    *,
    target: str,
    objective: ObjectiveDefinition | None,
    incumbent_value: float | None = None,
) -> list[ProposalStrategyAvailability]:
    target_capability = next(
        (item for item in capability.targets if item.target == target),
        None,
    )
    results = []
    for definition in STRATEGIES:
        reasons = []
        if not definition.production_enabled:
            reasons.append("この戦略はRuntime契約を確認中のため、まだ利用できません")
        if target_capability is None:
            reasons.append("選択したoutputをRuntimeが予測できません")
        else:
            if (
                definition.requires_standard_deviation
                and not target_capability.standard_deviation
            ):
                reasons.append("予測標準偏差に対応するRuntimeが必要です")
            if definition.requires_samples and not target_capability.samples:
                reasons.append("予測sampleに対応するRuntimeが必要です")
        if definition.requires_joint_samples and not capability.joint_samples:
            reasons.append("joint sampleに対応するRuntimeが必要です")
        has_incumbent = incumbent_value is not None
        if definition.requires_incumbent and not has_incumbent:
            reasons.append("incumbentのsourceと値が必要です")
        if (
            definition.acquisition_id in {"upper_confidence_bound", "expected_improvement"}
            and objective is not None
        ):
            primary = [
                term
                for term in objective.terms
                if term.role == "primary_objective"
            ]
            if (
                len(primary) != 1
                or primary[0].direction not in {"at_least", "at_most", "maximize", "minimize"}
            ):
                reasons.append("UCB/EIには方向を持つ単一主目的が必要です")
        results.append(
            ProposalStrategyAvailability(
                definition=definition,
                available=not reasons,
                reasons=tuple(reasons),
            )
        )
    return results


def resolve_strategy(
    request: ProposalStrategyRequest,
    capability: RuntimeCapability,
    *,
    target: str,
    objective: ObjectiveDefinition,
) -> tuple[ProposalStrategyDefinition, str | None]:
    availability = {
        item.definition.strategy_id: item
        for item in strategy_availability(
            capability,
            target=target,
            objective=objective,
            incumbent_value=request.incumbent_value,
        )
    }
    selected = availability.get(request.strategy_id)
    if selected is None:
        raise ValueError(f"未登録のProposal Strategyです: {request.strategy_id}")
    if selected.available:
        return selected.definition, None
    if request.fallback_policy == "deterministic_goal":
        baseline = availability["latin_hypercube_v1"]
        return baseline.definition, request.strategy_id
    raise ValueError(" / ".join(selected.reasons))
