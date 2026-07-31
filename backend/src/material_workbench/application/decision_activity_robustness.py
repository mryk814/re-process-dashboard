"""Robustness / tolerance analysis, owned entirely by this activity."""
from __future__ import annotations

import math
import random
import statistics
from collections.abc import Callable

from material_workbench.application.decision_activity_registry import (
    ActivityComputation,
    ActivityContext,
    DecisionActivityHandler,
    DecisionActivityValidationError,
)
from material_workbench.contracts.decision_activity_contracts import (
    AbsoluteTolerance,
    CriticalInput,
    InputVariationInterval,
    ModelUncertaintyInterval,
    RelativeTolerance,
    ROBUSTNESS_ACTIVITY,
    RobustnessFailureExample,
    RobustnessParameters,
    RobustnessSummary,
    RobustnessTargetSummary,
    ToleranceSpec,
    TruncatedNormalTolerance,
)
from material_workbench.contracts.schemas import (
    ModelMetadata,
    Prediction,
    Project,
    Support,
    TargetRange,
)
from material_workbench.domain.goal_targets import empirical_goal_probability


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires values")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _bounds(base: float, spec: ToleranceSpec) -> tuple[float, float]:
    if isinstance(spec, AbsoluteTolerance):
        return base - spec.amount, base + spec.amount
    if isinstance(spec, RelativeTolerance):
        lower, upper = sorted((base * (1.0 - spec.fraction), base * (1.0 + spec.fraction)))
        return lower, upper
    return spec.lower, spec.upper


def _sampler(base: float, spec: ToleranceSpec, rng: random.Random) -> Callable[[], float]:
    lower, upper = _bounds(base, spec)
    if isinstance(spec, TruncatedNormalTolerance):
        def sample_normal() -> float:
            for _ in range(10_000):
                value = rng.gauss(base, spec.standard_deviation)
                if lower <= value <= upper:
                    return value
            raise DecisionActivityValidationError("打切り正規分布から値を生成できません")
        return sample_normal
    return lambda: rng.uniform(lower, upper)


def _goal_failed(project: Project, direction: str, target: str, value: float) -> bool:
    goal = project.target_values.get(target)
    if goal is None:
        return False
    if isinstance(goal, TargetRange):
        return not goal.lower <= value <= goal.upper
    if direction == "at_most":
        return value > float(goal)
    return value < float(goal)


def _worst(
    values: list[float], project: Project, direction: str, target: str, base: float
) -> float:
    goal = project.target_values.get(target)
    if isinstance(goal, TargetRange):
        return max(values, key=lambda value: max(goal.lower - value, value - goal.upper, 0.0))
    if goal is not None and direction == "at_most":
        return max(values)
    if goal is not None and direction == "at_least":
        return min(values)
    return max(values, key=lambda value: abs(value - base))


def _correlation(xs: list[float], ys: list[float]) -> tuple[float, str]:
    try:
        correlation = statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return 0.0, "unclear"
    if not math.isfinite(correlation) or abs(correlation) < 0.05:
        return 0.0, "unclear"
    return abs(correlation), "increases_output" if correlation > 0 else "decreases_output"


def prepare(context: ActivityContext) -> dict[str, Callable[[], float]]:
    """Validate the tolerance profile against the task contract and build samplers."""

    parameters = context.parameters
    assert isinstance(parameters, RobustnessParameters)
    definition = context.task_definition
    fields = {
        field.path: field
        for group in definition.input_groups
        for field in group.fields
    }
    balance_paths = context.candidate_family.balance_paths(definition)
    rng = random.Random(parameters.seed)
    samplers: dict[str, Callable[[], float]] = {}
    project_space = context.project.design_space
    project_numeric = {
        item.path: item
        for item in (
            *((project_space.numeric_domains if project_space else ())),
            *((project_space.heat_pattern_domains if project_space else ())),
        )
    }
    project_fixed = project_space.fixed_values if project_space is not None else {}
    for path, spec in parameters.tolerance_profile.fields.items():
        field = fields.get(path)
        if field is None or field.kind != "number" or not field.editable:
            raise DecisionActivityValidationError(f"公差解析に使えない入力です: {path}")
        if path in balance_paths:
            raise DecisionActivityValidationError(
                f"{field.label}は組成合計のbalance項目なので直接は変動させられません"
            )
        try:
            base = context.candidate_family.numeric_value(
                context.candidate,
                path,
            )
        except ValueError as exc:
            raise DecisionActivityValidationError(str(exc)) from exc
        lower, upper = _bounds(base, spec)
        if path in project_fixed:
            raise DecisionActivityValidationError(
                f"{field.label}はProject Design Spaceで固定されています"
            )
        project_domain = project_numeric.get(path)
        if project_space is not None and project_domain is None:
            raise DecisionActivityValidationError(
                f"{field.label}はProject Design Spaceで変更できません"
            )
        if project_domain is not None:
            if project_domain.range is not None and (
                lower < project_domain.range.min or upper > project_domain.range.max
            ):
                raise DecisionActivityValidationError(
                    f"{field.label}の公差範囲がProject Design Spaceを超えています"
                )
            if project_domain.values and (
                lower not in project_domain.values or upper not in project_domain.values
            ):
                raise DecisionActivityValidationError(
                    f"{field.label}の公差範囲がProject Design Spaceの候補値にありません"
                )
        assert field.allowed_range is not None
        if lower < field.allowed_range.min or upper > field.allowed_range.max:
            raise DecisionActivityValidationError(
                f"{field.label}の公差範囲がTaskの許容範囲を超えています"
            )
        if isinstance(spec, TruncatedNormalTolerance) and not lower <= base <= upper:
            raise DecisionActivityValidationError(
                f"{field.label}の打切り範囲に現在値が含まれていません"
            )
        samplers[path] = _sampler(base, spec, rng)
    return samplers


def compute(
    context: ActivityContext, samplers: dict[str, Callable[[], float]]
) -> ActivityComputation:
    parameters = context.parameters
    assert isinstance(parameters, RobustnessParameters)
    project = context.project
    candidate = context.candidate
    runtime = context.runtime
    definition = context.task_definition
    outputs = {item.key: item for item in definition.outputs}
    base_result = runtime.predict_core(
        candidate, detailed=False, target_values=project.target_values
    )
    base_predictions = {
        key: Prediction.model_validate(value)
        for key, value in base_result["predictions"].items()
    }
    accepted_inputs: list[dict[str, float]] = []
    accepted_outputs: dict[str, list[float]] = {key: [] for key in outputs}
    supports: list[Support] = []
    failures: list[RobustnessFailureExample] = []
    rejected = 0
    max_attempts = parameters.sample_count * 20
    for attempt in range(max_attempts):
        if len(accepted_inputs) >= parameters.sample_count:
            break
        varied = {path: sample() for path, sample in samplers.items()}
        sample_candidate = context.candidate_family.update(
            candidate,
            varied,
            definition,
            balance=True,
        )
        try:
            context.validate_candidate(sample_candidate)
        except ValueError:
            rejected += 1
            continue
        result = runtime.predict_core(
            sample_candidate, detailed=False, target_values=project.target_values
        )
        point_outputs = {
            key: Prediction.model_validate(value).value
            for key, value in result["predictions"].items()
        }
        support = Support.model_validate(runtime.support_summary(sample_candidate))
        accepted_inputs.append(varied)
        supports.append(support)
        for key, value in point_outputs.items():
            accepted_outputs[key].append(value)
        failed_targets = tuple(
            key for key, value in point_outputs.items()
            if _goal_failed(project, outputs[key].goal_direction, key, value)
        )
        if (failed_targets or support.status == "extrapolated") and len(failures) < 5:
            failures.append(RobustnessFailureExample(
                sample_index=attempt,
                varied_inputs=varied,
                outputs=point_outputs,
                failed_targets=failed_targets,
                support=support,
            ))
    if not accepted_inputs:
        raise DecisionActivityValidationError(
            "Task制約を満たす公差サンプルを生成できませんでした"
        )
    target_summaries = []
    for key, output in outputs.items():
        values = accepted_outputs[key]
        base_prediction = base_predictions[key]
        goal = project.target_values.get(key)
        goal_rate = (
            empirical_goal_probability(values, goal, output.goal_direction)
            if goal is not None
            else None
        )
        target_summaries.append(RobustnessTargetSummary(
            target=key,
            unit=output.unit,
            base_prediction=base_prediction,
            input_variation=InputVariationInterval(
                median=_quantile(values, 0.5),
                lower=_quantile(values, 0.05),
                upper=_quantile(values, 0.95),
            ),
            model_uncertainty=ModelUncertaintyInterval(
                lower=base_prediction.lower,
                upper=base_prediction.upper,
            ),
            goal_achievement_rate=goal_rate,
            worst_observed=_worst(
                values, project, output.goal_direction, key, base_prediction.value
            ),
        ))
    critical: list[CriticalInput] = []
    for path in samplers:
        xs = [item[path] for item in accepted_inputs]
        for target, ys in accepted_outputs.items():
            score, direction = _correlation(xs, ys)
            critical.append(CriticalInput(
                path=path,
                target=target,
                absolute_correlation=score,
                direction=direction,
            ))
    critical.sort(key=lambda item: (-item.absolute_correlation, item.target, item.path))
    warnings = [
        "入力ばらつきによる出力分布とモデルの予測不確実性は別々に表示しています。",
        "入力との相関は局所的な結び付きであり、因果効果ではありません。",
        "Projectへ固定されたDesign Spaceはないため、TaskDefinitionの制約で検証しています。",
    ]
    if rejected:
        warnings.append(
            f"Task制約を満たさない{rejected}サンプルは結果へ含めていません。値はclipしていません。"
        )
    if len(accepted_inputs) < parameters.sample_count:
        warnings.append(
            f"要求{parameters.sample_count}件のうち{len(accepted_inputs)}件だけを評価できました。"
        )
    summary = RobustnessSummary(
        requested_samples=parameters.sample_count,
        accepted_samples=len(accepted_inputs),
        rejected_samples=rejected,
        target_summaries=tuple(target_summaries),
        critical_inputs=tuple(critical[:8]),
        failure_examples=tuple(failures),
        extrapolated_rate=sum(item.status == "extrapolated" for item in supports) / len(supports),
        caution_rate=sum(item.status == "caution" for item in supports) / len(supports),
        warnings=tuple(warnings),
    )
    return ActivityComputation(
        model=ModelMetadata.model_validate(base_result.get("model_meta", {})),
        result=summary,
    )


ROBUSTNESS_HANDLER = DecisionActivityHandler(
    definition=ROBUSTNESS_ACTIVITY,
    parameters_kind="robustness-parameters/v1",
    prepare=prepare,
    compute=compute,
)
