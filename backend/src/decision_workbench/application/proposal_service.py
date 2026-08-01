from __future__ import annotations

from time import perf_counter
from collections import defaultdict
from typing import Any, Callable

from decision_workbench.contracts.batch_proposal_contracts import (
    BatchProposalDefinition,
)
from decision_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from decision_workbench.contracts.proposal_contracts import ProposalStrategyDefinition
from decision_workbench.contracts.prediction_catalog_contracts import (
    DEFAULT_SCREENING_SEED,
    ScreeningGoal,
    ScreeningRequest,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
)
from decision_workbench.domain.batch_selector import (
    candidate_design_values,
    select_experiment_batch,
)
from decision_workbench.domain.proposal_acquisition import acquisition_value
from decision_workbench.domain.proposal_generation import generate_candidates
from decision_workbench.domain.proposal_selection import select_proposal_shortlist
from decision_workbench.domain.screening_score import (
    evaluate_screening_goal,
    score_contract,
    screening_goal_runtime_value,
)
from decision_workbench.task_composition.ports import (
    BatchPredictionRuntime,
    PredictionRuntime,
)

SCREENING_SEED = DEFAULT_SCREENING_SEED


def generate_from_design_space(
    base: Candidate,
    design_space: DesignSpaceDefinition,
    *,
    count: int,
    seed: int = SCREENING_SEED,
) -> list[tuple[Candidate, dict[str, float | str]]]:
    """Generate candidates from the immutable design-space contract.

    Sampling is deliberately kept as the first allow-listed strategy.  The
    important boundary is that it consumes the DesignSpace, rather than a UI
    request, so later simplex, Bayesian, or program decoders share the same
    fixed values, domains and conditional rules.
    """
    return generate_candidates(
        "latin_hypercube",
        base,
        design_space,
        count=count,
        seed=seed,
    )


def _validate_screening_pool(
    generated: list[tuple[Candidate, dict[str, float | str]]],
    candidate_validator: Callable[[CandidateInput], None],
) -> tuple[
    list[tuple[int, Candidate, CandidateInput, dict[str, float | str]]],
    dict[str, int],
    list[dict[str, Any]],
]:
    """Validate the complete proposal pool so rejection counts have a fixed denominator."""
    valid_candidates: list[
        tuple[int, Candidate, CandidateInput, dict[str, float | str]]
    ] = []
    rejected_by_reason: defaultdict[str, int] = defaultdict(int)
    rejections: list[dict[str, Any]] = []
    for pool_index, (candidate, applied) in enumerate(generated):
        try:
            candidate_input = CandidateInput.model_validate(candidate.model_dump())
            candidate_validator(candidate_input)
        except ValueError as exc:
            reason = str(exc) or "candidate_constraint"
            rejected_by_reason[reason] += 1
            rejections.append(
                {
                    "pool_index": pool_index,
                    "inputs": applied,
                    "reason": reason,
                }
            )
            continue
        candidate = Candidate.model_validate({**candidate.model_dump(), **candidate_input.model_dump()})
        valid_candidates.append((pool_index, candidate, candidate_input, applied))
    return valid_candidates, dict(sorted(rejected_by_reason.items())), rejections


def _apply_screening_goal_metadata(
    prediction: dict[str, Any],
    goal: ScreeningGoal | None,
) -> dict[str, Any]:
    prediction["goal_direction"] = goal.direction if goal else None
    prediction["goal_value"] = (
        goal.lower if goal and goal.direction == "at_least"
        else goal.upper if goal and goal.direction == "at_most"
        else None
    )
    prediction["goal_lower"] = goal.lower if goal and goal.direction == "between" else None
    prediction["goal_upper"] = goal.upper if goal and goal.direction == "between" else None
    return prediction


def _proposal_coverage(
    generated: list[tuple[Candidate, dict[str, float | str]]],
    design_space: DesignSpaceDefinition,
) -> dict[str, dict[str, float]]:
    evidence: dict[str, dict[str, float]] = {}
    domains = (*design_space.numeric_domains, *design_space.heat_pattern_domains)
    for domain in domains:
        values = [
            float(applied[domain.path])
            for _, applied in generated
            if domain.path in applied
        ]
        if not values:
            continue
        if domain.range is not None:
            span = domain.range.max - domain.range.min
        else:
            span = max(domain.values) - min(domain.values)
        observed_min = min(values)
        observed_max = max(values)
        evidence[domain.path] = {
            "observed_min": observed_min,
            "observed_max": observed_max,
            "observed_mean": sum(values) / len(values),
            "normalized_span": min(
                1.0,
                max(0.0, (observed_max - observed_min) / max(span, 1e-12)),
            ),
        }
    return evidence


def _evaluate_proposal_pool(
    runtime: PredictionRuntime,
    candidates: list[Candidate],
    *,
    target_values: dict[str, Any],
) -> list[dict[str, Any]]:
    """Evaluate a generated pool without building discarded similarity rows."""

    evaluation_candidates = [
        candidate.model_copy(
            update={
                "id": f"{candidate.id}:proposal-pool:{index}",
            }
        )
        for index, candidate in enumerate(candidates)
    ]
    predictions = (
        runtime.predict_batch(
            evaluation_candidates,
            detailed=False,
            target_values=target_values,
        )
        if (
            isinstance(runtime, BatchPredictionRuntime)
            and runtime.supports_batch_prediction
        )
        else [
            runtime.predict_core(
                candidate,
                detailed=False,
                target_values=target_values,
            )
            for candidate in evaluation_candidates
        ]
    )
    if len(predictions) != len(candidates):
        raise ValueError("batch prediction did not preserve candidate cardinality")
    for candidate, evaluation_candidate, prediction in zip(
        candidates,
        evaluation_candidates,
        predictions,
        strict=True,
    ):
        if prediction.get("candidate_id") != evaluation_candidate.id:
            raise ValueError("batch prediction did not preserve candidate order")
        prediction["candidate_id"] = candidate.id
        if "support" in prediction:
            continue
        support = runtime.support_summary(evaluation_candidate)
        prediction["support"] = support
        prediction["similar"] = []
        if (
            support.status != "supported"
            and support.message not in prediction["warnings"]
        ):
            prediction["warnings"].append(support.message)
    return predictions


def run_proposal(
    runtime: PredictionRuntime,
    base: Candidate,
    request: ScreeningRequest,
    *,
    probability_available: dict[str, bool],
    candidate_validator: Callable[[CandidateInput], None],
    design_space: DesignSpaceDefinition | None = None,
    strategy: ProposalStrategyDefinition,
    batch_reference_candidates: dict[str, Candidate] | None = None,
) -> dict[str, Any]:
    pool_size = request.samples * request.proposal.pool_multiplier
    if design_space is None:
        raise ValueError("Design Spaceなしでは候補を生成できません")
    generated = generate_candidates(
        strategy.generator_id,
        base,
        design_space,
        count=pool_size,
        seed=request.seed,
        generator_version=strategy.generator_version,
        parameters=strategy.generator_parameters,
    )
    points: list[dict[str, Any]] = []
    base_prediction = runtime.predict(base, detailed=False)
    valid_candidates, rejected_by_reason, proposal_rejections = (
        _validate_screening_pool(generated, candidate_validator)
    )

    if len(valid_candidates) < request.samples:
        raise ValueError(
            f"生成{pool_size}件中、制約を満たす点は{len(valid_candidates)}件でした。"
            f"{request.samples}件を作れるよう範囲を見直してください"
        )

    configured_goals: dict[str, ScreeningGoal] = dict(request.secondary_goals)
    if request.target_goal is not None:
        configured_goals[request.target] = request.target_goal
    target_values = {
        key: screening_goal_runtime_value(goal)
        for key, goal in configured_goals.items()
        if probability_available.get(key, False)
    }
    proposal_candidates = [
        candidate for _, candidate, _, _ in valid_candidates
    ]
    batch_prediction = (
        isinstance(runtime, BatchPredictionRuntime)
        and runtime.supports_batch_prediction
    )
    evaluation_started = perf_counter()
    prediction_rows = _evaluate_proposal_pool(
        runtime,
        proposal_candidates,
        target_values=target_values,
    )
    evaluation_runtime_ms = (perf_counter() - evaluation_started) * 1000
    for (
        pool_index,
        candidate,
        candidate_input,
        applied,
    ), prediction in zip(valid_candidates, prediction_rows, strict=True):
        selected = prediction["predictions"][request.target]
        support = prediction["support"]
        evaluation = evaluate_screening_goal(
            selected.value,
            goal=request.target_goal,
            achievement_probability=selected.goal_probability if probability_available.get(request.target, False) else None,
            support_distance=support.distance,
        )
        secondary_evaluations = {
            key: evaluate_screening_goal(
                prediction["predictions"][key].value,
                goal=goal,
                achievement_probability=prediction["predictions"][key].goal_probability if probability_available.get(key, False) else None,
                support_distance=support.distance,
            )
            for key, goal in request.secondary_goals.items()
        }
        acquisition_score, acquisition_components = acquisition_value(
            strategy.acquisition_id,
            prediction=selected,
            goal=request.target_goal,
            support_distance=support.distance,
            exploration_parameter=request.proposal.exploration_parameter,
            incumbent_value=request.proposal.incumbent_value,
        )
        prediction_payload = _apply_screening_goal_metadata(
            selected.model_dump(),
            request.target_goal,
        )
        predictions_payload = {}
        for key, item in prediction["predictions"].items():
            predictions_payload[key] = _apply_screening_goal_metadata(
                item.model_dump(),
                configured_goals.get(key),
            )
        points.append({
            "index": pool_index,
            "pool_index": pool_index,
            "inputs": applied,
            "candidate": candidate_input.model_dump(mode="json"),
            "prediction": prediction_payload,
            "predictions": predictions_payload,
            "color_value": selected.value,
            "support": support.model_dump(),
            "warnings": prediction.get("warnings", []),
            "similar": [item.model_dump() if hasattr(item, "model_dump") else item for item in prediction.get("similar", [])],
            "score": round(float(acquisition_score), 6),
            "acquisition_components": acquisition_components,
            "goal_evaluation": evaluation.model_dump(),
            "secondary_goal_evaluations": {key: item.model_dump() for key, item in secondary_evaluations.items()},
        })
    support_rank = {"supported": 0, "caution": 1, "extrapolated": 2}
    eligible = [
        point
        for point in points
        if not (
            request.proposal.support_policy == "exclude_extrapolated"
            and point["support"]["status"] == "extrapolated"
        )
    ]
    if len(eligible) < request.samples:
        raise ValueError(
            f"support方針を満たす点は{len(eligible)}件でした。"
            f"{request.samples}件を選べるよう範囲または方針を見直してください"
        )
    ranked = (
        sorted(eligible, key=lambda point: point["pool_index"])
        if request.purpose == "design_space_map"
        else sorted(
            eligible,
            key=lambda point: (
                support_rank[point["support"]["status"]]
                if request.proposal.support_policy == "supported_first"
                else 0,
                sum(item["achieved"] is False for item in point["secondary_goal_evaluations"].values()),
                point["score"],
                point["pool_index"],
            ),
        )
    )
    proposal_selection = (
        select_proposal_shortlist(
            ranked,
            request.proposal,
            design_space,
            strategy,
            seed=request.seed,
        )
        if request.purpose == "goal_search"
        else None
    )
    selected = ranked[:request.samples]
    if proposal_selection is not None:
        proposed_pool_indices = {
            item["pool_index"] for item in proposal_selection["selected"]
        }
        selected_pool_indices = {
            point["pool_index"] for point in selected
        }
        missing_proposals = [
            point
            for point in ranked
            if point["pool_index"] in proposed_pool_indices
            and point["pool_index"] not in selected_pool_indices
        ]
        if missing_proposals:
            retained = [
                point
                for point in selected
                if point["pool_index"] in proposed_pool_indices
            ]
            fill = [
                point
                for point in selected
                if point["pool_index"] not in proposed_pool_indices
            ][
                : request.samples - len(retained) - len(missing_proposals)
            ]
            selected = [*retained, *fill, *missing_proposals]
    batch_definition = request.batch_definition
    if (
        batch_definition is not None
        and "candidate_pool_size" not in batch_definition.model_fields_set
        and batch_definition.candidate_pool_size > request.samples
    ):
        batch_definition = BatchProposalDefinition.model_validate(
            {
                **batch_definition.model_dump(mode="json"),
                "candidate_pool_size": request.samples,
            }
        )
    exact_control_points: list[dict[str, Any]] = []
    if batch_definition is not None:
        references = batch_reference_candidates or {}
        for control_index, requirement in enumerate(
            batch_definition.controls
        ):
            candidate = references[requirement.candidate_id]
            exact_control_points.append(
                {
                    "pool_index": pool_size + control_index,
                    "inputs": candidate_design_values(candidate, design_space),
                    "candidate": CandidateInput.model_validate(
                        candidate.model_dump()
                    ).model_dump(mode="json"),
                    "score": 0.0,
                    "secondary_goal_evaluations": {},
                    "_batch_source": "exact_control",
                    "_candidate_id": candidate.id,
                    "_candidate_revision": candidate.revision,
                }
            )
    batch_proposal = (
        select_experiment_batch(
            [
                *ranked[: batch_definition.candidate_pool_size],
                *exact_control_points,
            ],
            batch_definition,
            design_space,
            seed=request.seed,
            reference_candidates=batch_reference_candidates or {},
            distance_id=strategy.distance_id,
            distance_version=strategy.distance_version,
            distance_parameters=strategy.distance_parameters,
        )
        if batch_definition is not None
        else None
    )
    candidate_by_pool_index = {
        pool_index: candidate
        for pool_index, candidate, _, _ in valid_candidates
    }
    for point in selected:
        point["similar"] = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in runtime.similarity(
                candidate_by_pool_index[point["pool_index"]]
            )
        ]
    selected_pool_indices = {point["pool_index"] for point in selected}
    selected_points = []
    selected_rank = {
        point["pool_index"]: rank for rank, point in enumerate(selected, start=1)
    }
    for index, point in enumerate(selected):
        selected_points.append({**point, "index": index})
    if proposal_selection is not None:
        point_index_by_pool = {
            point["pool_index"]: point["index"] for point in selected_points
        }
        proposal_selection["selected"] = [
            {
                **item,
                "point_index": point_index_by_pool[item["pool_index"]],
            }
            for item in proposal_selection["selected"]
        ]
    if batch_proposal is not None:
        point_index_by_pool = {
            point["pool_index"]: point["index"] for point in selected_points
        }
        batch_proposal["selected"] = [
            {
                "point_index": point_index_by_pool.get(
                    item["point"]["pool_index"]
                ),
                "pool_index": item["point"]["pool_index"],
                "order": order,
                "role": item["role"],
                "reason": item["reason"],
                "acquisition_component": item["acquisition_component"],
                "diversity_component": item["diversity_component"],
                "pending_penalty": item["pending_penalty"],
                "resource_penalty": item["resource_penalty"],
                "combined_score": item["combined_score"],
                "estimated_cost": item["estimated_cost"],
                "setup_group": item["setup_group"],
                "source": item["source"],
                "candidate_id": item["candidate_id"],
                "candidate_revision": item["candidate_revision"],
                "canonical_identity_digest": item[
                    "canonical_identity_digest"
                ],
            }
            for order, item in enumerate(batch_proposal["selected"], start=1)
        ]
    proposal_pool = [
        {
            "pool_index": point["pool_index"],
            "inputs": point["inputs"],
            "acquisition_score": point["score"],
            "acquisition_components": point["acquisition_components"],
            "support_status": point["support"]["status"],
            "selected_rank": selected_rank.get(point["pool_index"]),
            "exclusion_reason": (
                None
                if point["pool_index"] in selected_pool_indices
                else "support_policy_extrapolated"
                if request.proposal.support_policy == "exclude_extrapolated"
                and point["support"]["status"] == "extrapolated"
                else "ranked_below_selection_cutoff"
            ),
        }
        for point in points
    ]
    return {
        "schema_version": "screening-run/v4",
        "purpose": request.purpose,
        "source_run_id": request.source_run_id,
        "seed": request.seed,
        "base_candidate_id": base.id,
        "base_inputs": base.inputs.model_dump(mode="json"),
        "base_canonical_input": base_prediction["canonical_input"],
        "model_provenance": base_prediction["model_meta"],
        "target": request.target,
        "target_goal": request.target_goal.model_dump(mode="json") if request.target_goal else None,
        "secondary_goals": {
            key: goal.model_dump(mode="json")
            for key, goal in request.secondary_goals.items()
        },
        "score_contract": score_contract(
            request.target_goal,
            probability_available=probability_available.get(request.target, False),
        ),
        "samples": request.samples,
        "variables": {name: spec.model_dump() for name, spec in request.variables.items()},
        "points": selected_points,
        "representative_points": (
            [
                selected_points[item["point_index"]]
                for item in proposal_selection["selected"]
            ]
            if proposal_selection is not None
            else selected_points[:10]
        ),
        "proposal_pool": proposal_pool,
        "proposal_rejections": proposal_rejections,
        "proposal_selection": proposal_selection,
        "batch_proposal": batch_proposal,
        "_proposal_diagnostics": {
            "generated_count": pool_size,
            "valid_count": len(valid_candidates),
            "evaluated_count": len(points),
            "selected_count": len(selected_points),
            "displayed_count": len(selected_points),
            "proposed_count": (
                proposal_selection["actual_count"]
                if proposal_selection is not None
                else 0
            ),
            "model_call_count": (
                1 if batch_prediction and proposal_candidates
                else len(proposal_candidates)
            ),
            "runtime_ms": evaluation_runtime_ms,
            "memory_peak_bytes": None,
            "rejected_count": sum(rejected_by_reason.values()),
            "rejection_rate": sum(rejected_by_reason.values()) / pool_size,
            "rejected_by_reason": dict(sorted(rejected_by_reason.items())),
            "coverage_by_path": _proposal_coverage(generated, design_space),
        },
    }
