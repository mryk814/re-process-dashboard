from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


GoalDirection = Literal["at_least", "at_most", "target"]
ScoreMethod = Literal["achievement_probability", "directional_shortfall", "absolute_distance", "support_distance"]


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
    target_value: float | None,
    direction: GoalDirection | None,
    at_least_probability: float | None = None,
    support_distance: float = 0.0,
) -> ScreeningScore:
    if target_value is None or direction is None:
        return ScreeningScore(max(0.0, support_distance), "support_distance", None, None)

    if direction == "target":
        return ScreeningScore(abs(prediction - target_value), "absolute_distance", prediction == target_value, None)

    achieved = prediction >= target_value if direction == "at_least" else prediction <= target_value
    if at_least_probability is not None:
        probability = at_least_probability if direction == "at_least" else 1.0 - at_least_probability
        probability = min(1.0, max(0.0, probability))
        return ScreeningScore(1.0 - probability, "achievement_probability", achieved, probability)

    shortfall = max(target_value - prediction, 0.0) if direction == "at_least" else max(prediction - target_value, 0.0)
    return ScreeningScore(shortfall, "directional_shortfall", achieved, None)


def score_contract(
    direction: GoalDirection | None,
    target_value: float | None,
    *,
    probability_available: bool = False,
) -> dict[str, object]:
    if target_value is None or direction is None:
        label = "目標未設定（学習範囲への近さで表示）"
    elif direction == "at_least":
        label = "目標以上ほど有望"
    elif direction == "at_most":
        label = "目標以下ほど有望"
    else:
        label = "目標値に近いほど有望"
    return {
        "version": "screening-score/v1",
        "preference": "lower_is_better",
        "direction": direction,
        "target_value": target_value,
        "probability_available": probability_available,
        "fallback": "support_distance" if target_value is None else "directional_shortfall" if direction in {"at_least", "at_most"} else "absolute_distance",
        "display_label": label,
    }
