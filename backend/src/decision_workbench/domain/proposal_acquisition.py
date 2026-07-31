"""Deterministic acquisition evaluators with explicit component reporting."""
from __future__ import annotations

import math
from statistics import NormalDist
from typing import Any

from decision_workbench.contracts.prediction_catalog_contracts import ScreeningGoal
from decision_workbench.domain.screening_score import evaluate_screening_goal


def predictive_standard_deviation(prediction: Any) -> tuple[float, str]:
    components = prediction.uncertainty_components or {}
    for key in ("total", "predictive", "model"):
        value = components.get(key)
        if value is not None and math.isfinite(float(value)) and float(value) > 0:
            return float(value), f"uncertainty_component:{key}"
    width = float(prediction.upper) - float(prediction.lower)
    if width <= 0:
        raise ValueError("予測不確かさが正の幅を持ちません")
    return (
        width / (2 * 1.6448536269514722),
        "central_90_interval_normal_approximation",
    )


def acquisition_value(
    acquisition_id: str,
    *,
    prediction: Any,
    goal: ScreeningGoal | None,
    support_distance: float,
    exploration_parameter: float,
    incumbent_value: float | None,
) -> tuple[float, dict[str, float | str | bool | None]]:
    mean = float(prediction.value)
    if acquisition_id == "goal_achievement":
        evaluation = evaluate_screening_goal(
            mean,
            goal=goal,
            achievement_probability=prediction.goal_probability,
            support_distance=support_distance,
        )
        score = (
            float(evaluation.score)
            if evaluation.score is not None
            else float(support_distance)
        )
        return score, {
            "method": evaluation.method,
            "mean": mean,
            "achievement_probability": evaluation.achievement_probability,
            "support_distance": support_distance,
        }

    if goal is None or goal.direction == "between":
        raise ValueError("UCB/EIには上限または下限方向の主目的が必要です")
    sigma, sigma_method = predictive_standard_deviation(prediction)
    maximize = goal.direction == "at_least"
    if acquisition_id == "upper_confidence_bound":
        bound = (
            mean + exploration_parameter * sigma
            if maximize
            else mean - exploration_parameter * sigma
        )
        return (-bound if maximize else bound), {
            "method": "ucb" if maximize else "lcb",
            "mean": mean,
            "standard_deviation": sigma,
            "standard_deviation_method": sigma_method,
            "acquisition_representation": "normal_mean_std",
            "exploration_parameter": exploration_parameter,
            "parameter_role": "confidence_multiplier",
            "confidence_bound": bound,
        }
    if acquisition_id == "expected_improvement":
        if incumbent_value is None:
            raise ValueError("Expected Improvementにはincumbent値が必要です")
        raw_improvement = (
            mean - incumbent_value if maximize else incumbent_value - mean
        )
        improvement = raw_improvement - exploration_parameter
        z = improvement / sigma
        normal = NormalDist()
        expected = improvement * normal.cdf(z) + sigma * normal.pdf(z)
        expected = max(0.0, expected)
        return -expected, {
            "method": "expected_improvement",
            "mean": mean,
            "standard_deviation": sigma,
            "standard_deviation_method": sigma_method,
            "acquisition_representation": "normal_mean_std",
            "incumbent_value": incumbent_value,
            "raw_improvement": raw_improvement,
            "improvement_margin": exploration_parameter,
            "parameter_role": "improvement_margin",
            "expected_improvement": expected,
        }
    raise ValueError(f"未登録のAcquisition Evaluatorです: {acquisition_id}")
