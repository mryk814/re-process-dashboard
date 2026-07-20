from __future__ import annotations

from pathlib import Path

import pytest

from material_workbench.hot_rolling_feature_pipeline import CANONICAL_INPUT_PATHS, FEATURE_NAMES, PIPELINE_ID, PIPELINE_VERSION, build_hot_rolling_features, build_hot_rolling_features_from_observation, candidate_from_observation
from material_workbench.importer import load_workbook_data
from material_workbench.schemas import CandidateInput


DEFAULTS = {name: 0.0 for name in ("C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca")}
EXPECTED_CANONICAL_INPUT_PATHS = (
    *(f"composition.{name}" for name in DEFAULTS),
    "process.reheat_temperature_c",
    "process.hold_time_min",
    "process.finish_temperature_c",
    "process.coiling_temperature_c",
    "process.cooling_rate_c_s",
    "process.entry_thickness_mm",
    "process.exit_thickness_mm",
    "categorical.route",
)


def test_hot_rolling_features_are_fixed_order_and_non_redundant() -> None:
    candidate = CandidateInput(
        inputs={
            "composition": {"C": 0.1, "Mn": 1.5},
            "process": {"reheat_temperature_c": 1180, "hold_time_min": 30,
                "finish_temperature_c": 900, "coiling_temperature_c": 620, "cooling_rate_c_s": 35,
                "entry_thickness_mm": 35, "exit_thickness_mm": 3.5},
            "categorical": {"route": "B"}, "heat_pattern": None,
        },
    )
    bundle = build_hot_rolling_features(candidate, DEFAULTS)
    values = bundle.as_dict()
    assert (bundle.pipeline_id, bundle.pipeline_version) == (PIPELINE_ID, PIPELINE_VERSION)
    assert tuple(values) == FEATURE_NAMES
    assert bundle.indices_by_group() == {
        "composition": tuple(range(14)),
        "metallurgy": (14, 15, 16, 17, 18),
        "process": (19, 20, 21, 22, 23, 24, 25),
        "categorical": (26, 27, 28),
    }
    assert CANONICAL_INPUT_PATHS == EXPECTED_CANONICAL_INPUT_PATHS
    assert "reduction_percent" not in values
    assert (values["route_A"], values["route_B"], values["route_C"]) == (0.0, 1.0, 0.0)
    assert values["ce_iiw"] == pytest.approx(0.35)
    assert values["pcm"] == pytest.approx(0.175)
    assert values["c_times_mn"] == pytest.approx(0.15)
    assert values["finish_temperature_c"] == 900


def test_hot_rolling_task_contract_rejects_non_reduction(client) -> None:
    with pytest.raises(ValueError, match="出側板厚"):
        client.app.state.task_registry.validate_candidate("hot-rolled-properties-v1", CandidateInput(inputs={
            "composition": DEFAULTS,
            "process": {"reheat_temperature_c": 1180, "hold_time_min": 30, "finish_temperature_c": 900,
                "coiling_temperature_c": 620, "cooling_rate_c_s": 35, "entry_thickness_mm": 3, "exit_thickness_mm": 3},
            "categorical": {"route": "A"}, "heat_pattern": None,
        }))


def test_hot_rolling_training_row_uses_the_candidate_pipeline() -> None:
    source = Path(__file__).parents[2] / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"
    data = load_workbook_data(source)
    row = next(item for item in data.observations if item["source"] == "熱延引張" and item["eligible"])
    candidate = candidate_from_observation(row)
    assert candidate is not None
    training_bundle = build_hot_rolling_features_from_observation(row, data.medians)
    assert training_bundle is not None
    prediction_bundle = build_hot_rolling_features(candidate, data.medians)
    assert training_bundle.features == prediction_bundle.features
