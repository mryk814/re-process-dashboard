"""Typed Proposal Strategy registry and Runtime Capability checks."""
from __future__ import annotations

from typing import Literal

from decision_workbench.contracts.objective_contracts import ObjectiveDefinition
from decision_workbench.contracts.proposal_contracts import (
    AcquisitionRepresentation,
    ProposalStrategyAvailability,
    ProposalStrategyDefinition,
    ProposalStrategyRequest,
)
from decision_workbench.contracts.task_contracts import (
    RuntimeCapability,
    TargetRuntimeCapability,
)
from decision_workbench.contracts.model_capability_contracts import (
    CapabilityRequirement,
    ModelPackageCapabilityMatrix,
)
from decision_workbench.modeling.package_capabilities import resolve_capabilities
from decision_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from decision_workbench.domain.proposal_generation import (
    bounded_simplex_compatibility,
)
from decision_workbench.domain.proposal_geometry import (
    COMPOSITION_DISTANCE_ID,
    COMPOSITION_DISTANCE_VERSION,
    DEFAULT_COMPOSITION_DISTANCE_PARAMETERS,
)


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
        strategy_id="bounded_simplex_goal_v1",
        version="1.0.0",
        label="Bounded simplex・目標基準（組成向け）",
        generator_id="bounded_simplex_hit_and_run",
        generator_version="1.0.0",
        generator_parameters={
            "simplex_sampler": "hit_and_run",
            "other_axes_sampler": "latin_hypercube",
            "minimum_balance": 0.0,
            "burn_in_steps": 256,
            "thinning_steps": 16,
        },
        distance_id=COMPOSITION_DISTANCE_ID,
        distance_version=COMPOSITION_DISTANCE_VERSION,
        distance_parameters=DEFAULT_COMPOSITION_DISTANCE_PARAMETERS,
        acquisition_id="goal_achievement",
        acquisition_version="1.0.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
    ),
    ProposalStrategyDefinition(
        strategy_id="design_prior_empirical_v1",
        version="1.0.0",
        label="Design Prior・経験分布",
        generator_id="design_prior",
        generator_version="1.0.0",
        acquisition_id="goal_achievement",
        acquisition_version="1.0.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
    ),
    ProposalStrategyDefinition(
        strategy_id="sobol_ucb_v1",
        version="1.1.0",
        label="Sobol・UCB/LCB",
        generator_id="sobol",
        generator_version="1.0.0",
        acquisition_id="upper_confidence_bound",
        acquisition_version="1.0.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
        requires_standard_deviation=True,
        requires_acquisition_representation="normal_mean_std",
        required_capabilities=(CapabilityRequirement(capability="normal_mean_std"),),
    ),
    ProposalStrategyDefinition(
        strategy_id="sobol_ei_v1",
        version="1.1.0",
        label="Sobol・Expected Improvement",
        generator_id="sobol",
        generator_version="1.0.0",
        acquisition_id="expected_improvement",
        acquisition_version="1.1.0",
        selector_id="ranked_top_k",
        selector_version="1.0.0",
        requires_standard_deviation=True,
        requires_incumbent=True,
        requires_acquisition_representation="normal_mean_std",
        required_capabilities=(CapabilityRequirement(capability="normal_mean_std"),),
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
        lifecycle_status="experimental",
        required_capabilities=(CapabilityRequirement(capability="predictive_samples"),),
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
        lifecycle_status="experimental",
        required_capabilities=(CapabilityRequirement(capability="standard_deviation"),),
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
        lifecycle_status="experimental",
    ),
)


def target_acquisition_representations(
    capability: TargetRuntimeCapability | None,
    *,
    target_kind: Literal[
        "continuous", "continuous_positive", "binary", "count", "ordinal"
    ] | None,
) -> tuple[AcquisitionRepresentation, ...]:
    """Translate low-level Runtime output fields into acquisition-safe meanings."""

    if capability is None:
        return ("unsupported",)
    representations: list[AcquisitionRepresentation] = []
    if (
        target_kind == "continuous"
        and "mean" in capability.point_statistics
        and capability.standard_deviation
    ):
        representations.append("normal_mean_std")
    if capability.samples:
        representations.append("posterior_samples")
    if capability.parametric_distribution:
        representations.append("parametric_distribution")
    return tuple(representations) or ("unsupported",)


def strategy_availability(
    capability: RuntimeCapability,
    *,
    target: str,
    target_kind: Literal[
        "continuous", "continuous_positive", "binary", "count", "ordinal"
    ],
    objective: ObjectiveDefinition | None,
    incumbent_value: float | None = None,
    design_space: DesignSpaceDefinition | None = None,
    capability_matrix: ModelPackageCapabilityMatrix | None = None,
) -> list[ProposalStrategyAvailability]:
    target_capability = next(
        (item for item in capability.targets if item.target == target),
        None,
    )
    representations = target_acquisition_representations(
        target_capability,
        target_kind=target_kind,
    )
    results = []
    for definition in STRATEGIES:
        reasons = []
        if definition.generator_id == "bounded_simplex_hit_and_run":
            if design_space is None:
                reasons.append("Project Design Spaceを読み込めません")
            else:
                compatible, geometry_reasons = bounded_simplex_compatibility(
                    design_space,
                    minimum_balance=float(
                        definition.generator_parameters.get("minimum_balance", 0.0)
                    ),
                )
                if not compatible:
                    reasons.extend(geometry_reasons)
        if not definition.production_enabled:
            reasons.append("この戦略はRuntime契約を確認中のため、まだ利用できません")
        if target_capability is None:
            reasons.append("選択したoutputをRuntimeが予測できません")
        elif capability_matrix is None:
            # Direct callers that only have the legacy Task contract retain the
            # old diagnostic surface. Production always supplies the Package
            # matrix below, where family semantics are also checked.
            if (
                definition.requires_standard_deviation
                and definition.requires_acquisition_representation is None
                and not target_capability.standard_deviation
            ):
                reasons.append("予測標準偏差に対応するRuntimeが必要です")
            if definition.requires_samples and not target_capability.samples:
                reasons.append("予測sampleに対応するRuntimeが必要です")
            required_representation = definition.requires_acquisition_representation
            if (
                required_representation is not None
                and required_representation not in representations
            ):
                reasons.append(
                    "この戦略には平均と予測標準偏差をnormal mean/stdとして"
                    "解釈できる連続outputが必要です"
                )
        else:
            resolution = resolve_capabilities(
                capability_matrix, target=target,
                requirements=definition.required_capabilities,
            )
            reasons.extend(reason for reason in resolution.reasons if reason not in reasons)
        if capability_matrix is None and definition.requires_joint_samples and not capability.joint_samples:
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
        effective_definition = _distance_aware_definition(
            definition,
            design_space,
        )
        results.append(
            ProposalStrategyAvailability(
                definition=effective_definition,
                target_acquisition_representations=representations,
                available=not reasons,
                reasons=tuple(reasons),
            )
        )
    return results


def _distance_aware_definition(
    definition: ProposalStrategyDefinition,
    design_space: DesignSpaceDefinition | None,
) -> ProposalStrategyDefinition:
    """Bind diversity geometry to the actual variables of this Design Space.

    Candidate generation and acquisition remain strategy-specific.  Distance
    is a separate selector contract, so a generic generator can use the
    simplex-aware composition geometry when composition axes vary.
    """

    if design_space is None or definition.distance_id != "scalar_axis_rms":
        return definition
    varying_paths = {
        *(item.path for item in design_space.numeric_domains),
        *(item.path for item in design_space.heat_pattern_domains),
        *(item.path for item in design_space.categorical_domains),
    }
    varying_composition_paths = {
        path for path in varying_paths if path.startswith("composition.")
    }
    if len(varying_composition_paths) < 2 and not design_space.composition_constraints:
        return definition
    return definition.model_copy(
        update={
            "distance_id": COMPOSITION_DISTANCE_ID,
            "distance_version": COMPOSITION_DISTANCE_VERSION,
            "distance_parameters": dict(DEFAULT_COMPOSITION_DISTANCE_PARAMETERS),
        }
    )


def resolve_strategy(
    request: ProposalStrategyRequest,
    capability: RuntimeCapability,
    *,
    target: str,
    target_kind: Literal[
        "continuous", "continuous_positive", "binary", "count", "ordinal"
    ],
    objective: ObjectiveDefinition,
    design_space: DesignSpaceDefinition | None = None,
    capability_matrix: ModelPackageCapabilityMatrix | None = None,
) -> tuple[ProposalStrategyDefinition, str | None]:
    availability = {
        item.definition.strategy_id: item
        for item in strategy_availability(
            capability,
            target=target,
            target_kind=target_kind,
            objective=objective,
            incumbent_value=request.incumbent_value,
            design_space=design_space,
            capability_matrix=capability_matrix,
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
