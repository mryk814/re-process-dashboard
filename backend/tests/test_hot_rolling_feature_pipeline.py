from __future__ import annotations

import pytest

from material_workbench.hot_rolling_feature_pipeline import FEATURE_NAMES, build_hot_rolling_features
from material_workbench.schemas import HotRollingCandidateInput


DEFAULTS = {name: 0.0 for name in ("C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca")}


def test_hot_rolling_features_are_fixed_order_and_non_redundant() -> None:
    candidate = HotRollingCandidateInput(
        composition={"C": 0.1, "Mn": 1.5}, reheat_temperature_c=1180, hold_time_min=30,
        finish_temperature_c=900, coiling_temperature_c=620, cooling_rate_c_s=35,
        entry_thickness_mm=35, exit_thickness_mm=3.5, route="B",
    )
    bundle = build_hot_rolling_features(candidate, DEFAULTS)
    values = bundle.as_dict()
    assert tuple(values) == FEATURE_NAMES
    assert "reduction_percent" not in values
    assert (values["route_A"], values["route_B"], values["route_C"]) == (0.0, 1.0, 0.0)
    assert values["ce_iiw"] == pytest.approx(0.35)


def test_hot_rolling_candidate_rejects_non_reduction() -> None:
    with pytest.raises(ValueError, match="出側板厚"):
        HotRollingCandidateInput(entry_thickness_mm=3.0, exit_thickness_mm=3.0)
