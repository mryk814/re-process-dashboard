import pytest
from pydantic import ValidationError

from decision_workbench.contracts.prediction_catalog_contracts import ScreeningGoal
from decision_workbench.domain.screening_score import evaluate_screening_goal, score_contract


def test_at_least_achievement_is_never_ranked_behind_failure_without_probability() -> None:
    goal = ScreeningGoal(direction="at_least", lower=500)
    achieved = evaluate_screening_goal(510, goal=goal)
    failed = evaluate_screening_goal(490, goal=goal)

    assert achieved.score == 0
    assert failed.score == 10
    assert achieved.achieved is True
    assert failed.achieved is False


def test_at_most_achievement_is_never_ranked_behind_failure_without_probability() -> None:
    goal = ScreeningGoal(direction="at_most", upper=500)
    achieved = evaluate_screening_goal(490, goal=goal)
    failed = evaluate_screening_goal(510, goal=goal)

    assert achieved.score == 0
    assert failed.score == 10


def test_goal_achievement_probability_has_the_same_semantics_for_both_directions() -> None:
    at_least = evaluate_screening_goal(
        510,
        goal=ScreeningGoal(direction="at_least", lower=500),
        achievement_probability=0.8,
    )
    at_most = evaluate_screening_goal(
        490,
        goal=ScreeningGoal(direction="at_most", upper=500),
        achievement_probability=0.8,
    )
    fallback = evaluate_screening_goal(510, goal=ScreeningGoal(direction="at_least", lower=500))

    assert at_least.score == pytest.approx(0.2)
    assert at_most.score == pytest.approx(0.2)
    assert at_least.method == at_most.method == "achievement_probability"
    assert at_least.achievement_probability == at_most.achievement_probability == 0.8
    assert fallback.method == "directional_shortfall"


def test_unset_target_has_explicit_support_distance_contract() -> None:
    evaluation = evaluate_screening_goal(510, goal=None, support_distance=0.42)

    assert evaluation.score == 0.42
    assert evaluation.method == "support_distance"
    assert score_contract(None)["fallback"] == "support_distance"


@pytest.mark.parametrize("prediction", [450, 500, 550])
def test_between_goal_includes_both_boundaries(prediction: float) -> None:
    goal = ScreeningGoal(direction="between", lower=450, upper=550)
    evaluation = evaluate_screening_goal(prediction, goal=goal)

    assert evaluation.score == 0
    assert evaluation.method == "range_shortfall"
    assert evaluation.achieved is True


@pytest.mark.parametrize(
    ("prediction", "expected_shortfall"),
    [(449.5, 0.5), (550.25, 0.25)],
)
def test_between_goal_ranks_outside_values_by_nearest_boundary(
    prediction: float,
    expected_shortfall: float,
) -> None:
    goal = ScreeningGoal(direction="between", lower=450, upper=550)
    evaluation = evaluate_screening_goal(prediction, goal=goal)

    assert evaluation.score == pytest.approx(expected_shortfall)
    assert evaluation.achieved is False
    assert score_contract(goal)["fallback"] == "range_shortfall"


@pytest.mark.parametrize(
    "payload",
    [
        {"direction": "at_least"},
        {"direction": "at_least", "lower": 450, "upper": 550},
        {"direction": "at_most", "lower": 450},
        {"direction": "between", "lower": 550, "upper": 450},
        {"direction": "between", "lower": 450},
    ],
)
def test_screening_goal_rejects_incomplete_or_ambiguous_bounds(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ScreeningGoal.model_validate(payload)
