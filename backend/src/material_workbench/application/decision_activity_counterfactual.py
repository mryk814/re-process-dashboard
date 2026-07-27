"""Feasible target-reaching proposals measured from one saved candidate revision."""
from __future__ import annotations

import math
from dataclasses import dataclass

from material_workbench.application.decision_activity_registry import (
    ActivityComputation,
    ActivityContext,
    DecisionActivityHandler,
    DecisionActivityValidationError,
)
from material_workbench.contracts.decision_activity_contracts import (
    COUNTERFACTUAL_ACTIVITY,
    CounterfactualInfeasibility,
    CounterfactualInputChange,
    CounterfactualParameters,
    CounterfactualProposal,
    CounterfactualSummary,
    CounterfactualTargetEvaluation,
)
from material_workbench.contracts.design_space_contracts import (
    DesignSpaceDefinition,
    NumericDomain,
)
from material_workbench.contracts.objective_contracts import (
    ObjectiveDefinition,
    ObjectiveTerm,
)
from material_workbench.contracts.schemas import (
    Candidate,
    CandidateInputs,
    ModelMetadata,
    Prediction,
    Support,
)
from material_workbench.domain.candidate_inputs import (
    raw_input_value,
    with_declared_balance,
    with_input_values,
)
from material_workbench.domain.proposal_generation import generate_candidates
from material_workbench.execution.inference_work_graph import semantic_digest


STRATEGY_ID = "normalized-l1-sobol-v1"
STRATEGY_VERSION = "1.0.0"
NUMERIC_TOLERANCE = 1e-10


@dataclass(frozen=True)
class _PreparedCounterfactual:
    design_space: DesignSpaceDefinition
    objective: ObjectiveDefinition
    fields: dict[str, object]


@dataclass(frozen=True)
class _Evaluated:
    candidate: Candidate
    predictions: dict[str, Prediction]
    support: Support
    changes: tuple[CounterfactualInputChange, ...]
    distance: float
    target_evaluations: tuple[CounterfactualTargetEvaluation, ...]
    blocking_shortfall: float
    preference_penalty: float
    meets_objective: bool


def prepare(context: ActivityContext) -> _PreparedCounterfactual:
    parameters = context.parameters
    assert isinstance(parameters, CounterfactualParameters)
    space = context.project.design_space
    objective = context.project.objective_definition
    if space is None or context.project.design_space_digest is None:
        raise DecisionActivityValidationError(
            "Project-level Design Spaceが固定されていません"
        )
    if objective is None or context.project.objective_definition_digest is None:
        raise DecisionActivityValidationError("Projectの目標値を先に設定してください")
    fields = {
        field.path: field
        for group in context.task_definition.input_groups
        for field in group.fields
    }
    declared = {
        *space.fixed_values,
        *(item.path for item in space.numeric_domains),
        *(item.path for item in space.heat_pattern_domains),
        *(item.path for item in space.categorical_domains),
    }
    unknown = sorted(set(parameters.immutable_paths) - declared)
    if unknown:
        raise DecisionActivityValidationError(
            "変更不可項目がDesign Spaceにありません: " + ", ".join(unknown)
        )
    return _PreparedCounterfactual(
        design_space=space,
        objective=objective,
        fields=fields,
    )


def _value(candidate: Candidate, path: str) -> float | str:
    parts = path.split(".")
    if len(parts) == 2:
        value = raw_input_value(candidate, path)
        if value is None:
            raise DecisionActivityValidationError(f"候補に入力がありません: {path}")
        return value
    if (
        len(parts) == 3
        and parts[0] == "heat_pattern"
        and parts[1].isdigit()
        and parts[2] in {"time_s", "temperature_c"}
    ):
        points = candidate.inputs.heat_pattern
        index = int(parts[1])
        if points is None or index >= len(points):
            raise DecisionActivityValidationError(
                f"候補にヒートパターン点がありません: {path}"
            )
        return float(getattr(points[index], parts[2]))
    raise DecisionActivityValidationError(f"入力パスを解決できません: {path}")


def _with_value(
    context: ActivityContext,
    candidate: Candidate,
    path: str,
    value: float | str,
) -> Candidate:
    parts = path.split(".")
    if len(parts) == 3 and parts[0] == "heat_pattern":
        points = candidate.inputs.heat_pattern
        index = int(parts[1])
        if points is None or index >= len(points):
            raise DecisionActivityValidationError(
                f"候補にヒートパターン点がありません: {path}"
            )
        updated = candidate.model_copy(deep=True)
        assert updated.inputs.heat_pattern is not None
        setattr(updated.inputs.heat_pattern[index], parts[2], float(value))
        updated.inputs = CandidateInputs.model_validate(updated.inputs)
        return updated
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return with_declared_balance(
            candidate,
            {path: float(value)},
            context.task_definition.composition_totals,
            context.task_definition,
        )
    updated = with_input_values(
        candidate, {path: str(value)}, context.task_definition
    )
    space = context.project.design_space
    assert space is not None
    for conditional in space.conditional_constraints:
        if conditional.controller_path != path:
            continue
        if value not in conditional.active_choices:
            updated = with_input_values(
                updated,
                dict(conditional.inactive_values),
                context.task_definition,
            )
    return updated


def _domain_span(domain: NumericDomain) -> float:
    if domain.range is not None:
        return domain.range.max - domain.range.min
    return max(domain.values) - min(domain.values) if len(domain.values) > 1 else 0.0


def _changes(
    context: ActivityContext,
    prepared: _PreparedCounterfactual,
    proposed: Candidate,
) -> tuple[tuple[CounterfactualInputChange, ...], float]:
    parameters = context.parameters
    assert isinstance(parameters, CounterfactualParameters)
    numeric = {
        item.path: item
        for item in (
            *prepared.design_space.numeric_domains,
            *prepared.design_space.heat_pattern_domains,
        )
    }
    categorical = {
        item.path: item for item in prepared.design_space.categorical_domains
    }
    changes: list[CounterfactualInputChange] = []
    distance = 0.0
    for path in sorted((*numeric, *categorical)):
        base_value = _value(context.candidate, path)
        next_value = _value(proposed, path)
        if isinstance(base_value, (int, float)) and isinstance(
            next_value, (int, float)
        ):
            if math.isclose(
                float(base_value),
                float(next_value),
                rel_tol=0.0,
                abs_tol=NUMERIC_TOLERANCE,
            ):
                continue
            span = _domain_span(numeric[path])
            normalized = (
                abs(float(next_value) - float(base_value)) / span
                if span > 0
                else math.inf
            )
        else:
            if base_value == next_value:
                continue
            normalized = parameters.categorical_change_penalty
        field = prepared.fields.get(path)
        changes.append(
            CounterfactualInputChange(
                path=path,
                label=str(getattr(field, "label", path)),
                unit=getattr(field, "unit", None),
                base_value=base_value,
                proposed_value=next_value,
                normalized_distance=normalized,
            )
        )
        distance += normalized
    return tuple(changes), distance


def _scale(term: ObjectiveTerm) -> float:
    values = [
        abs(value)
        for value in (term.lower, term.upper, term.target)
        if value is not None
    ]
    return max([1.0, *values])


def _evaluate_term(
    term: ObjectiveTerm,
    value: float,
    base_value: float,
) -> tuple[bool, float, float | None]:
    scale = _scale(term)
    if term.role == "reporting_only":
        return True, 0.0, None
    if term.direction == "at_least":
        assert term.lower is not None
        gap = max(term.lower - value, 0.0)
    elif term.direction == "at_most":
        assert term.upper is not None
        gap = max(value - term.upper, 0.0)
    elif term.direction == "between":
        assert term.lower is not None and term.upper is not None
        gap = max(term.lower - value, value - term.upper, 0.0)
    elif term.direction == "target":
        assert term.target is not None
        tolerance = NUMERIC_TOLERANCE * scale
        gap = max(abs(value - term.target) - tolerance, 0.0)
    elif term.direction == "maximize":
        achieved = value > base_value + NUMERIC_TOLERANCE
        return achieved, 0.0 if achieved else NUMERIC_TOLERANCE / scale, None
    elif term.direction == "minimize":
        achieved = value < base_value - NUMERIC_TOLERANCE
        return achieved, 0.0 if achieved else NUMERIC_TOLERANCE / scale, None
    else:
        gap = 0.0
    normalized = gap / scale
    return normalized <= NUMERIC_TOLERANCE, normalized, gap


def _candidate_pool(
    context: ActivityContext,
    prepared: _PreparedCounterfactual,
) -> list[Candidate]:
    parameters = context.parameters
    assert isinstance(parameters, CounterfactualParameters)
    immutable = set(parameters.immutable_paths)
    sampling_space = prepared.design_space.model_copy(
        update={
            "numeric_domains": tuple(
                item
                for item in prepared.design_space.numeric_domains
                if item.path not in immutable
            ),
            "heat_pattern_domains": tuple(
                item
                for item in prepared.design_space.heat_pattern_domains
                if item.path not in immutable
            ),
            "categorical_domains": tuple(
                item
                for item in prepared.design_space.categorical_domains
                if item.path not in immutable
            ),
        }
    )
    generated = [
        candidate
        for candidate, _ in generate_candidates(
            "sobol",
            context.candidate,
            sampling_space,
            count=parameters.sample_count,
            seed=parameters.seed,
        )
    ]
    # Coordinate lines make a one-field minimal change observable rather than
    # leaving it to a high-dimensional random sample.
    for domain in (
        *prepared.design_space.numeric_domains,
        *prepared.design_space.heat_pattern_domains,
    ):
        if domain.path in parameters.immutable_paths:
            continue
        values = (
            tuple(
                domain.range.min
                + index * (domain.range.max - domain.range.min) / 16
                for index in range(17)
            )
            if domain.range is not None
            else domain.values
        )
        for value in values:
            try:
                generated.append(
                    _with_value(context, context.candidate, domain.path, value)
                )
            except ValueError:
                continue
    for domain in prepared.design_space.categorical_domains:
        if domain.path in parameters.immutable_paths:
            continue
        for choice in domain.choices:
            try:
                generated.append(
                    _with_value(context, context.candidate, domain.path, choice)
                )
            except ValueError:
                continue
    return generated


def _predict(
    context: ActivityContext,
    candidate: Candidate,
) -> tuple[dict[str, Prediction], Support, dict[str, object]]:
    result = context.runtime.predict_core(
        candidate,
        detailed=False,
        target_values=context.project.target_values,
    )
    predictions = {
        key: Prediction.model_validate(value)
        for key, value in result["predictions"].items()
    }
    support = Support.model_validate(context.runtime.support_summary(candidate))
    return predictions, support, result


def _evaluate_candidate(
    context: ActivityContext,
    prepared: _PreparedCounterfactual,
    candidate: Candidate,
    base_predictions: dict[str, Prediction],
) -> _Evaluated | None:
    parameters = context.parameters
    assert isinstance(parameters, CounterfactualParameters)
    changes, distance = _changes(context, prepared, candidate)
    if (
        not changes
        or not math.isfinite(distance)
        or len(changes) > parameters.max_changed_fields
        or any(item.path in parameters.immutable_paths for item in changes)
    ):
        return None
    try:
        context.validate_candidate(candidate)
    except ValueError:
        return None
    predictions, support, _ = _predict(context, candidate)
    evaluations: list[CounterfactualTargetEvaluation] = []
    blocking = 0.0
    preference = 0.0
    for term in prepared.objective.terms:
        prediction = predictions.get(term.output_key)
        base = base_predictions.get(term.output_key)
        if prediction is None or base is None:
            raise DecisionActivityValidationError(
                f"Objectiveの特性を予測できません: {term.output_key}"
            )
        achieved, normalized_shortfall, shortfall = _evaluate_term(
            term,
            prediction.value,
            base.value,
        )
        evaluations.append(
            CounterfactualTargetEvaluation(
                target=term.output_key,
                unit=term.unit,
                predicted_value=prediction.value,
                prediction=prediction,
                achieved=achieved,
                normalized_shortfall=normalized_shortfall,
                shortfall=shortfall,
                role=term.role,
            )
        )
        if term.role in {"primary_objective", "hard_outcome_constraint"}:
            blocking += normalized_shortfall
        elif term.role == "soft_preference":
            preference += normalized_shortfall * float(term.weight or 1.0)
    return _Evaluated(
        candidate=candidate,
        predictions=predictions,
        support=support,
        changes=changes,
        distance=distance,
        target_evaluations=tuple(evaluations),
        blocking_shortfall=blocking,
        preference_penalty=preference,
        meets_objective=blocking <= NUMERIC_TOLERANCE,
    )


def _refine_coordinate_boundaries(
    context: ActivityContext,
    prepared: _PreparedCounterfactual,
    base_predictions: dict[str, Prediction],
    evaluated: list[_Evaluated],
) -> None:
    """Bisect a feasible one-field line to recover its nearest boundary."""

    feasible_by_path: dict[str, _Evaluated] = {}
    for item in evaluated:
        if item.meets_objective and len(item.changes) == 1:
            path = item.changes[0].path
            current = feasible_by_path.get(path)
            if current is None or item.distance < current.distance:
                feasible_by_path[path] = item
    for path, feasible in feasible_by_path.items():
        base_value = _value(context.candidate, path)
        feasible_value = _value(feasible.candidate, path)
        if not isinstance(base_value, (int, float)) or not isinstance(
            feasible_value, (int, float)
        ):
            continue
        low = float(base_value)
        high = float(feasible_value)
        best = feasible
        for _ in range(24):
            middle = (low + high) / 2
            candidate = _with_value(context, context.candidate, path, middle)
            item = _evaluate_candidate(
                context, prepared, candidate, base_predictions
            )
            if item is not None and item.meets_objective:
                best = item
                high = middle
            else:
                low = middle
        evaluated.append(best)


def compute(
    context: ActivityContext,
    prepared: _PreparedCounterfactual,
) -> ActivityComputation:
    parameters = context.parameters
    assert isinstance(parameters, CounterfactualParameters)
    base_predictions, _, base_result = _predict(context, context.candidate)
    base_satisfies = all(
        _evaluate_term(
            term,
            base_predictions[term.output_key].value,
            base_predictions[term.output_key].value,
        )[0]
        for term in prepared.objective.terms
        if term.role in {"primary_objective", "hard_outcome_constraint"}
    )
    if base_satisfies:
        raise DecisionActivityValidationError(
            "現在候補はすでにProject目標を満たしているため、変更案は不要です"
        )

    evaluated: list[_Evaluated] = []
    rejected = 0
    seen: set[str] = set()
    for candidate in _candidate_pool(context, prepared):
        digest = semantic_digest(candidate.inputs.model_dump(mode="json"))
        if digest in seen:
            continue
        seen.add(digest)
        item = _evaluate_candidate(context, prepared, candidate, base_predictions)
        if item is None:
            rejected += 1
            continue
        evaluated.append(item)
    if not evaluated:
        raise DecisionActivityValidationError(
            "Design Space内に評価可能な変更条件を生成できませんでした"
        )
    _refine_coordinate_boundaries(
        context, prepared, base_predictions, evaluated
    )

    support_order = {"supported": 0, "caution": 1, "extrapolated": 2}
    feasible = [item for item in evaluated if item.meets_objective]
    feasible.sort(
        key=lambda item: (
            support_order[item.support.status],
            item.distance,
            item.preference_penalty,
            tuple(change.path for change in item.changes),
            tuple(str(change.proposed_value) for change in item.changes),
        )
    )
    proposals = []
    for rank, item in enumerate(feasible[: parameters.result_count], start=1):
        warnings = []
        if item.support.status != "supported":
            warnings.append(
                "学習データの支持範囲外または境界付近です。達成保証ではありません。"
            )
        proposal_id = semantic_digest(
            {
                "base": [context.candidate.id, context.candidate.revision],
                "inputs": item.candidate.inputs.model_dump(mode="json"),
                "objective": prepared.objective.digest,
            }
        ).removeprefix("sha256:")[:16]
        proposals.append(
            CounterfactualProposal(
                proposal_id=proposal_id,
                rank=rank,
                inputs=item.candidate.inputs,
                change_distance=item.distance,
                changed_field_count=len(item.changes),
                changes=item.changes,
                target_evaluations=item.target_evaluations,
                meets_objective=True,
                support=item.support,
                warnings=tuple(warnings),
            )
        )

    infeasibility: list[CounterfactualInfeasibility] = []
    if not proposals:
        for term in prepared.objective.terms:
            if term.role not in {
                "primary_objective",
                "hard_outcome_constraint",
            }:
                continue
            ranked = sorted(
                evaluated,
                key=lambda item: next(
                    value.normalized_shortfall
                    for value in item.target_evaluations
                    if value.target == term.output_key
                ),
            )
            best = ranked[0]
            target = next(
                value
                for value in best.target_evaluations
                if value.target == term.output_key
            )
            infeasibility.append(
                CounterfactualInfeasibility(
                    target=term.output_key,
                    unit=term.unit,
                    best_value=target.predicted_value,
                    normalized_shortfall=target.normalized_shortfall,
                    explanation=(
                        "現在のDesign Spaceと変更項目数では基準へ届く案を確認できませんでした。"
                        "Design Spaceまたは目標の見直し候補です。"
                    ),
                )
            )

    warnings = [
        "提案はモデル予測に基づく局所探索であり、実測での達成を保証しません。",
        "変更量はProject Design Space幅で正規化したL1距離です。",
        "条件はclipせず、Task／Design Space制約を満たす案だけを評価しています。",
    ]
    if rejected:
        warnings.append(
            f"制約または変更上限を満たさない{rejected}条件は除外しました。"
        )
    summary = CounterfactualSummary(
        status="feasible" if proposals else "infeasible",
        base_candidate_id=context.candidate.id,
        base_candidate_revision=context.candidate.revision,
        design_space_digest=prepared.design_space.digest,
        objective_definition_digest=prepared.objective.digest,
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        seed=parameters.seed,
        evaluated_count=len(evaluated),
        rejected_count=rejected,
        proposals=tuple(proposals),
        infeasibility=tuple(infeasibility),
        warnings=tuple(warnings),
    )
    return ActivityComputation(
        model=ModelMetadata.model_validate(base_result.get("model_meta", {})),
        result=summary,
    )


COUNTERFACTUAL_HANDLER = DecisionActivityHandler(
    definition=COUNTERFACTUAL_ACTIVITY,
    parameters_kind="counterfactual-parameters/v1",
    prepare=prepare,
    compute=compute,
)
