from __future__ import annotations

import math
from pathlib import Path

import pytest

from material_workbench.hot_rolling_feature_pipeline import CANONICAL_INPUT_PATHS, FEATURE_NAMES, PIPELINE_ID, PIPELINE_VERSION, build_hot_rolling_features, build_hot_rolling_features_from_observation, candidate_from_observation
from material_workbench.importer import load_workbook_data
from material_workbench.schemas import CandidateInput


DEFAULTS = {name: 0.0 for name in ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")}
EXPECTED_CANONICAL_INPUT_PATHS = (
    *(f"composition.{name}" for name in DEFAULTS),
    "process.soaking_temperature_c",
    "process.finish_temperature_c",
    "process.entry_thickness_mm",
    "process.exit_thickness_mm",
    "process.hold_temperature_c",
    "process.hold_time_min",
)


def test_hot_rolling_features_are_fixed_order_and_non_redundant() -> None:
    candidate = CandidateInput(
        inputs={
        "composition": {"C": 0.1, "Mn": 1.5},
            "process": {"soaking_temperature_c": 1180, "finish_temperature_c": 900,
                "entry_thickness_mm": 35, "exit_thickness_mm": 3.5,
                "hold_temperature_c": 1160, "hold_time_min": 30},
            "categorical": {}, "heat_pattern": None,
        },
    )
    bundle = build_hot_rolling_features(candidate, DEFAULTS)
    values = bundle.as_dict()
    assert (bundle.pipeline_id, bundle.pipeline_version) == (PIPELINE_ID, PIPELINE_VERSION)
    assert tuple(values) == FEATURE_NAMES
    assert bundle.indices_by_group() == {
        "composition": tuple(range(14)),
        "metallurgy": (14, 15, 16, 17, 18, 19, 20, 21, 22, 31),
        "process": (23, 24, 25, 26, 27, 28, 29, 30, 32, 33, 34, 35, 36),
    }
    assert CANONICAL_INPUT_PATHS == EXPECTED_CANONICAL_INPUT_PATHS
    assert "reduction_percent" not in values
    assert values["ce_iiw"] == pytest.approx(0.35)
    assert values["pcm"] == pytest.approx(0.175)
    assert values["c_times_mn"] == pytest.approx(0.15)
    assert values["finish_temperature_c"] == 900
    assert values["ac1_proxy_c"] == pytest.approx(706.95)
    assert values["ac3_proxy_c"] == pytest.approx(845.8057635)
    assert values["ms_proxy_c"] == pytest.approx(451.1)
    assert values["total_reduction_percent"] == pytest.approx(90)
    assert values["log_thickness_strain"] == pytest.approx(math.log(10))
    assert values["ar3_proxy_c"] == pytest.approx(757.425)
    assert values["finish_minus_ar3_c"] == pytest.approx(142.575)
    assert values["hold_exposure_above_ac1_c_min"] == pytest.approx(13_591.5)


def test_hot_rolling_task_contract_rejects_invalid_thickness_order() -> None:
    from material_workbench.task_contracts import CanonicalCandidate, TaskContractFixture
    from material_workbench.task_registry import load_task_contracts

    contract = load_task_contracts()["hot-rolled-properties-v1"]
    candidate = CandidateInput(inputs={
        "composition": DEFAULTS,
        "process": {"soaking_temperature_c": 1180, "finish_temperature_c": 900,
                    "entry_thickness_mm": 3, "exit_thickness_mm": 3,
                    "hold_temperature_c": 1160, "hold_time_min": 30},
        "categorical": {}, "heat_pattern": None,
    })
    canonical = CanonicalCandidate(
        schema_version=contract.task_definition.canonical_candidate_schema_version,
        task_id=contract.task_definition.id,
        composition=candidate.inputs.composition,
        process=candidate.inputs.process,
        heat_pattern=None,
        categorical={},
        provenance=candidate.provenance,
    )
    with pytest.raises(ValueError, match="出側板厚"):
        TaskContractFixture(
            task_definition=contract.task_definition,
            canonical_candidate=canonical,
            runtime_capability=contract.runtime_capability,
        )


def test_hot_rolling_training_row_uses_the_candidate_pipeline() -> None:
    source = Path(__file__).parents[2] / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"
    data = load_workbook_data(source)
    row = next(item for item in data.observations if item["task_id"] == "hot-rolled-properties-v1" and item["eligible"])
    candidate = candidate_from_observation(row)
    assert candidate is not None
    training_bundle = build_hot_rolling_features_from_observation(row, data.medians)
    assert training_bundle is not None
    prediction_bundle = build_hot_rolling_features(candidate, data.medians)
    assert training_bundle.features == prediction_bundle.features
