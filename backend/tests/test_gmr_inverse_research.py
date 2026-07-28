import numpy as np
import pytest

from material_workbench.research.gmr_inverse import (
    ConstraintSet,
    JointGaussianMixture,
    run_historical_replay,
    synthetic_process_data,
)


def test_gmr_returns_both_high_density_modes_for_one_target() -> None:
    x, y, _ = synthetic_process_data(rows=320)
    model = JointGaussianMixture(components=2).fit(x, y)

    modes = model.conditional_modes(92.0)

    assert len(modes) == 2
    assert {point.point[0] < 0 for point in modes} == {False, True}
    assert sum(point.conditional_weight for point in modes) == pytest.approx(1.0)


def test_constraint_set_filters_bounds_and_cross_field_rule() -> None:
    constraints = ConstraintSet(
        lower=np.asarray([-2.3, -1.9]),
        upper=np.asarray([2.3, 1.9]),
    )
    points = np.asarray([
        [-1.5, 1.2],
        [1.5, 1.2],
        [3.0, -1.2],
    ])

    accepted, rejected = constraints.filter(points)

    assert accepted.tolist() == [[-1.5, 1.2]]
    assert rejected == 2


def test_historical_replay_records_forward_evidence_and_hold_decision() -> None:
    report = run_historical_replay()
    gmr = report["summary"]["gmr_modes"]

    assert gmr["constraint_pass_rate"] == 1.0
    assert gmr["mean_modes_presented"] == 2.0
    assert gmr["goal_hit_rate"] >= report["summary"]["historical_neighbor"]["goal_hit_rate"]
    assert report["decision"]["status"] == "hold"
    assert report["example_target"]["candidates"]
    assert all(
        "forward_prediction" in candidate
        for candidate in report["example_target"]["candidates"]
    )
