"""Candidate-difference attribution, owned entirely by this activity.

The attribution substitutes one input at a time into the comparison candidate,
so each contribution is a local substitution effect, not a causal effect. The
part of the gap that substitutions do not add up to is reported as an explicit
residual instead of being distributed across inputs.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from decision_workbench.application.decision_activity_registry import (
    ActivityComputation,
    ActivityContext,
    DecisionActivityHandler,
    DecisionActivityValidationError,
)
from decision_workbench.contracts.decision_activity_contracts import (
    CANDIDATE_DIFFERENCE_ACTIVITY,
    CandidateDifferenceParameters,
    CandidateDifferenceSummary,
    DifferenceContribution,
    DifferenceInputChange,
    DifferenceTargetSummary,
)
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.prediction_catalog_contracts import (
    ModelMetadata,
    Prediction,
    Support,
)
from decision_workbench.contracts.task_contracts import InputFieldDefinition


# 1入力あたり1回の追加予測が必要なので、置換対象は明示的に上限を設ける。
# 超えた場合は黙って切り捨てず、比較候補を選び直してもらう。
MAX_SUBSTITUTED_INPUTS = 24
NUMERIC_TOLERANCE = 1e-12


@dataclass(frozen=True)
class _PreparedDifference:
    comparison: Candidate
    changes: tuple[DifferenceInputChange, ...]
    substituted_paths: tuple[str, ...]


def _fields(context: ActivityContext) -> dict[str, InputFieldDefinition]:
    return {
        field.path: field
        for group in context.task_definition.input_groups
        for field in group.fields
        if field.kind in {"number", "categorical"}
    }


def _changed(
    base_value: float | str | None, comparison_value: float | str | None
) -> bool:
    if isinstance(base_value, (int, float)) and isinstance(comparison_value, (int, float)):
        return abs(float(base_value) - float(comparison_value)) > NUMERIC_TOLERANCE
    return base_value != comparison_value


def prepare(context: ActivityContext) -> _PreparedDifference:
    parameters = context.parameters
    assert isinstance(parameters, CandidateDifferenceParameters)
    base = context.candidate
    if (
        parameters.comparison_candidate_id == base.id
        and parameters.comparison_revision == base.revision
    ):
        raise DecisionActivityValidationError(
            "比較候補には別の候補または別のrevisionを指定してください"
        )
    try:
        comparison = context.resolve_candidate(
            parameters.comparison_candidate_id, parameters.comparison_revision
        )
    except (LookupError, ValueError) as exc:
        raise DecisionActivityValidationError(
            f"比較候補のrevisionを解決できません: {exc}"
        ) from exc
    if comparison.blend_validation.status == "invalid":
        reasons = " / ".join(issue.message for issue in comparison.blend_validation.issues)
        raise DecisionActivityValidationError(
            f"比較候補がDesign Spaceを満たしていないため比較できません: {reasons}"
        )
    context.validate_candidate(comparison)

    changes: list[DifferenceInputChange] = []
    substituted: list[str] = []
    for path, field in sorted(_fields(context).items()):
        base_value = context.candidate_family.value(
            base,
            path,
            required=False,
        )
        comparison_value = context.candidate_family.value(
            comparison,
            path,
            required=False,
        )
        if base_value is None and comparison_value is None:
            continue
        if not _changed(base_value, comparison_value):
            continue
        difference = (
            float(base_value) - float(comparison_value)
            if isinstance(base_value, (int, float))
            and isinstance(comparison_value, (int, float))
            else None
        )
        changes.append(DifferenceInputChange(
            path=path,
            label=field.label,
            unit=field.unit,
            base_value=base_value,
            comparison_value=comparison_value,
            difference=difference,
        ))
        if base_value is not None:
            substituted.append(path)
    if not changes:
        raise DecisionActivityValidationError(
            "2つの候補の入力が同じため、差分の要因を説明できません"
        )
    if len(substituted) > MAX_SUBSTITUTED_INPUTS:
        raise DecisionActivityValidationError(
            f"入力の相違が{len(substituted)}件あり、置換上限{MAX_SUBSTITUTED_INPUTS}件を超えています。"
            "より近い候補を比較対象に選んでください"
        )
    return _PreparedDifference(
        comparison=comparison,
        changes=tuple(changes),
        substituted_paths=tuple(substituted),
    )


def compute(
    context: ActivityContext, prepared: _PreparedDifference
) -> ActivityComputation:
    parameters = context.parameters
    assert isinstance(parameters, CandidateDifferenceParameters)
    runtime = context.runtime
    project = context.project
    definition = context.task_definition
    outputs = {item.key: item for item in definition.outputs}

    base_result = runtime.predict_core(
        context.candidate,
        detailed=False,
        target_values=project.target_values,
        **context.prediction_sampling_kwargs(),
    )
    comparison_result = runtime.predict_core(
        prepared.comparison,
        detailed=False,
        target_values=project.target_values,
        **context.prediction_sampling_kwargs(),
    )
    base_predictions = {
        key: Prediction.model_validate(value)
        for key, value in base_result["predictions"].items()
    }
    comparison_predictions = {
        key: Prediction.model_validate(value)
        for key, value in comparison_result["predictions"].items()
    }
    missing = sorted(set(outputs) - (set(base_predictions) & set(comparison_predictions)))
    if missing:
        raise DecisionActivityValidationError(
            "両候補で同じ目標特性を予測できません: " + ", ".join(missing)
        )

    contributions: list[DifferenceContribution] = []
    attributed: dict[str, float] = {key: 0.0 for key in outputs}
    for path in prepared.substituted_paths:
        substituted_value = context.candidate_family.value(
            context.candidate,
            path,
            required=False,
        )
        assert substituted_value is not None
        substituted_candidate = context.candidate_family.update(
            prepared.comparison,
            {path: substituted_value},
            definition,
        )
        try:
            context.validate_candidate(substituted_candidate)
        except ValueError as exc:
            raise DecisionActivityValidationError(
                f"{path}だけを置き換えた条件がTask制約を満たしません: {exc}"
            ) from exc
        substituted_result = runtime.predict_core(
            substituted_candidate,
            detailed=False,
            target_values=project.target_values,
            **context.prediction_sampling_kwargs(),
        )
        for key in outputs:
            value = Prediction.model_validate(substituted_result["predictions"][key]).value
            contribution = value - comparison_predictions[key].value
            attributed[key] += contribution
            contributions.append(DifferenceContribution(
                path=path,
                target=key,
                contribution=contribution,
                direction=(
                    "no_change"
                    if abs(contribution) <= NUMERIC_TOLERANCE
                    else "increases_output" if contribution > 0
                    else "decreases_output"
                ),
            ))
    contributions.sort(key=lambda item: (item.target, -abs(item.contribution), item.path))

    target_summaries: list[DifferenceTargetSummary] = []
    for key, output in outputs.items():
        base_prediction = base_predictions[key]
        comparison_prediction = comparison_predictions[key]
        difference = base_prediction.value - comparison_prediction.value
        if not math.isfinite(difference):
            raise DecisionActivityValidationError(f"予測差が有限値ではありません: {key}")
        target_summaries.append(DifferenceTargetSummary(
            target=key,
            unit=output.unit,
            base_prediction=base_prediction,
            comparison_prediction=comparison_prediction,
            difference=difference,
            attributed_difference=attributed[key],
            unexplained_difference=difference - attributed[key],
        ))

    warnings = [
        "各入力の寄与は、比較候補にその入力だけを置き換えた局所的な差です。因果効果ではありません。",
        "寄与の合計と実際の差の残りは、入力どうしの交互作用として残差へ明示しています。",
        "予測値の差とモデルの予測不確実性は別々に表示しています。合算していません。",
    ]
    unsubstituted = [
        change.path for change in prepared.changes
        if change.path not in prepared.substituted_paths
    ]
    if unsubstituted:
        warnings.append(
            "基準候補に値のない入力は置換できないため、寄与を計算していません: "
            + ", ".join(unsubstituted)
        )

    summary = CandidateDifferenceSummary(
        comparison_candidate_id=prepared.comparison.id,
        comparison_candidate_revision=prepared.comparison.revision,
        changed_input_count=len(prepared.changes),
        target_summaries=tuple(target_summaries),
        input_changes=prepared.changes,
        contributions=tuple(contributions),
        base_support=Support.model_validate(runtime.support_summary(context.candidate)),
        comparison_support=Support.model_validate(
            runtime.support_summary(prepared.comparison)
        ),
        warnings=tuple(warnings),
    )
    return ActivityComputation(
        model=ModelMetadata.model_validate(base_result.get("model_meta", {})),
        result=summary,
    )


CANDIDATE_DIFFERENCE_HANDLER = DecisionActivityHandler(
    definition=CANDIDATE_DIFFERENCE_ACTIVITY,
    parameters_kind="candidate-difference-parameters/v1",
    prepare=prepare,
    compute=compute,
)
