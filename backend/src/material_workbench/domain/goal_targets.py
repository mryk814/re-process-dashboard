from __future__ import annotations

import math
from collections.abc import Callable, Iterable

from material_workbench.contracts.candidate_project_contracts import (
    TargetRange,
    TargetValue,
)


def serialize_target_values(values: dict[str, TargetValue]) -> dict[str, float | dict[str, float]]:
    return {
        key: value.model_dump() if isinstance(value, TargetRange) else float(value)
        for key, value in values.items()
    }


def goal_fields(
    goal: TargetValue | None,
    default_direction: str,
) -> tuple[float | None, float | None, float | None, str | None]:
    if goal is None:
        return None, None, None, None
    if isinstance(goal, TargetRange):
        return None, goal.lower, goal.upper, "between"
    if default_direction == "target":
        raise ValueError("方向のない目標特性には下限・上限が必要です")
    return float(goal), None, None, default_direction


def probability_from_cdf(goal: TargetValue, cdf: Callable[[float], float], default_direction: str) -> float:
    if isinstance(goal, TargetRange):
        return max(0.0, min(1.0, cdf(goal.upper) - cdf(goal.lower)))
    if default_direction == "target":
        raise ValueError("方向のない目標特性には下限・上限が必要です")
    probability_below = cdf(float(goal))
    return probability_below if default_direction == "at_most" else 1.0 - probability_below


def normal_goal_probability(mean: float, standard_deviation: float, goal: TargetValue, default_direction: str) -> float:
    def cdf(value: float) -> float:
        z = (value - mean) / standard_deviation
        return 0.5 * math.erfc(-z / math.sqrt(2.0))

    return probability_from_cdf(goal, cdf, default_direction)


def empirical_goal_probability(samples: Iterable[float], goal: TargetValue, default_direction: str) -> float:
    values = list(samples)
    if not values:
        raise ValueError("samples must not be empty")
    if isinstance(goal, TargetRange):
        matches = sum(goal.lower <= value <= goal.upper for value in values)
    elif default_direction == "at_most":
        matches = sum(value <= float(goal) for value in values)
    else:
        matches = sum(value >= float(goal) for value in values)
    return matches / len(values)
