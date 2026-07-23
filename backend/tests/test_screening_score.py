import pytest

from material_workbench.domain.screening_score import evaluate_screening_goal, score_contract


def test_at_least_achievement_is_never_ranked_behind_failure_without_probability() -> None:
    achieved = evaluate_screening_goal(510, target_value=500, direction="at_least")
    failed = evaluate_screening_goal(490, target_value=500, direction="at_least")

    assert achieved.score == 0
    assert failed.score == 10
    assert achieved.achieved is True
    assert failed.achieved is False


def test_at_most_achievement_is_never_ranked_behind_failure_without_probability() -> None:
    achieved = evaluate_screening_goal(490, target_value=500, direction="at_most")
    failed = evaluate_screening_goal(510, target_value=500, direction="at_most")

    assert achieved.score == 0
    assert failed.score == 10


def test_probability_is_direction_aware_and_only_used_when_supplied() -> None:
    at_least = evaluate_screening_goal(510, target_value=500, direction="at_least", at_least_probability=0.8)
    at_most = evaluate_screening_goal(490, target_value=500, direction="at_most", at_least_probability=0.2)
    fallback = evaluate_screening_goal(510, target_value=500, direction="at_least")

    assert at_least.score == pytest.approx(0.2)
    assert at_most.score == pytest.approx(0.2)
    assert at_least.method == at_most.method == "achievement_probability"
    assert fallback.method == "directional_shortfall"


def test_unset_target_has_explicit_support_distance_contract() -> None:
    evaluation = evaluate_screening_goal(510, target_value=None, direction="at_least", support_distance=0.42)

    assert evaluation.score == 0.42
    assert evaluation.method == "support_distance"
    assert score_contract("at_least", None)["fallback"] == "support_distance"
