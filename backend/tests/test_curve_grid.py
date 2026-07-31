import pytest

from decision_workbench.modeling.curve_grid import anchored_curve_grid


def test_curve_grid_keeps_size_and_includes_current_candidate_value() -> None:
    values = anchored_curve_grid(0.0, 10.0, 5, current=6.2)

    assert len(values) == 5
    assert values == sorted(values)
    assert values[0] == 0.0
    assert values[-1] == 10.0
    assert 6.2 in values


@pytest.mark.parametrize("current", [None, -1.0, 0.0, 10.0, 11.0])
def test_curve_grid_preserves_endpoints_for_current_value_outside_the_interior(current) -> None:
    assert anchored_curve_grid(0.0, 10.0, 3, current=current) == [0.0, 5.0, 10.0]
