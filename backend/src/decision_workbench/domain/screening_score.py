from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from decision_workbench.contracts.prediction_catalog_contracts import ScreeningGoal
from decision_workbench.contracts.candidate_project_contracts import (
    TargetRange,
    TargetValue,
)

ScoreMethod = Literal["achievement_probability", "directional_shortfall", "range_shortfall", "absolute_distance", "support_distance"]


@dataclass(frozen=True)
class ScreeningScore:
    """A lower-is-better score used only to order screening points."""

    score: float | None
    method: ScoreMethod
    achieved: bool | None
    achievement_probability: float | None

    def model_dump(self) -> dict[str, float | str | bool | None]:
        return asdict(self)


def evaluate_screening_goal(
    prediction: float,
    *,
    goal: ScreeningGoal | None,
    achievement_probability: float | None = None,
    support_distance: float = 0.0,
) -> ScreeningScore:
    if goal is None:
        return ScreeningScore(max(0.0, support_distance), "support_distance", None, None)

    if goal.direction == "between":
        assert goal.lower is not None and goal.upper is not None
        achieved = goal.lower <= prediction <= goal.upper
    else:
        threshold = goal.lower if goal.direction == "at_least" else goal.upper
        assert threshold is not None
        achieved = prediction >= threshold if goal.direction == "at_least" else prediction <= threshold
    if achievement_probability is not None:
        probability = min(1.0, max(0.0, achievement_probability))
        return ScreeningScore(1.0 - probability, "achievement_probability", achieved, probability)

    if goal.direction == "between":
        assert goal.lower is not None and goal.upper is not None
        return ScreeningScore(
            max(goal.lower - prediction, prediction - goal.upper, 0.0),
            "range_shortfall",
            achieved,
            None,
        )
    threshold = goal.lower if goal.direction == "at_least" else goal.upper
    assert threshold is not None
    shortfall = max(threshold - prediction, 0.0) if goal.direction == "at_least" else max(prediction - threshold, 0.0)
    return ScreeningScore(shortfall, "directional_shortfall", achieved, None)


def score_contract(
    goal: ScreeningGoal | None,
    *,
    probability_available: bool = False,
) -> dict[str, object]:
    if goal is None:
        label = "目標未設定（学習範囲への近さで表示）"
    elif goal.direction == "between":
        label = "下限から上限の範囲内ほど有望"
    elif goal.direction == "at_least":
        label = "目標以上ほど有望"
    else:
        label = "目標以下ほど有望"
    return {
        "version": "screening-score/v3",
        "preference": "lower_is_better",
        "direction": goal.direction if goal else None,
        "target_value": (
            goal.lower if goal and goal.direction == "at_least"
            else goal.upper if goal and goal.direction == "at_most"
            else None
        ),
        "lower": goal.lower if goal else None,
        "upper": goal.upper if goal else None,
        "probability_available": probability_available,
        "probability_semantics": "probability_of_achieving_goal",
        "ranking_policy": "support_tier_then_secondary_goals_then_score",
        "fallback": (
            "range_shortfall"
            if goal and goal.direction == "between"
            else "support_distance"
            if goal is None
            else "directional_shortfall"
        ),
        "display_label": label,
    }


def screening_goal_runtime_value(goal: ScreeningGoal) -> TargetValue:
    if goal.direction == "between":
        assert goal.lower is not None and goal.upper is not None
        return TargetRange(lower=goal.lower, upper=goal.upper)
    threshold = goal.lower if goal.direction == "at_least" else goal.upper
    assert threshold is not None
    return threshold
