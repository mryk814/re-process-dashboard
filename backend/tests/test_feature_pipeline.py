import math
from pathlib import Path

import numpy as np
import pytest

from material_workbench.feature_pipeline import CANONICAL_INPUT_PATHS, FEATURE_NAMES, FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION, build_feature_bundle, build_feature_bundle_from_observation, candidate_from_observation
from material_workbench.importer import _normalize_stage_local_times, load_workbook_data
from material_workbench.schemas import CandidateInput


COMPOSITION = {
    "C": 0.10, "Si": 0.30, "Mn": 1.50, "P": 0.01, "S": 0.005,
    "Al": 0.04, "Cu": 0.20, "Ni": 0.15, "Cr": 0.20, "Mo": 0.10,
    "Ti": 0.02, "B": 0.002, "O": 0.003, "N": 0.004,
}
EXPECTED_FEATURE_NAMES = (
    "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N",
    "ls_mpm",
    "ce_iiw", "pcm", "c_times_mn", "si_plus_al", "cr_plus_mo", "microalloy_sum",
    "peak_temperature_c", "max_heating_rate_c_s", "time_at_or_above_95pct_peak_s",
    "time_at_or_above_700c_s", "thermal_exposure_above_600c_c_s",
    "cooling_rate_800_to_500_c_s", "cooling_800_to_500_observed", "reheat_count", "has_reheat",
)
EXPECTED_CANONICAL_INPUT_PATHS = (
    *(f"composition.{name}" for name in COMPOSITION),
    "process.ls_mpm",
    "heat_pattern",
)


def _candidate(heat_pattern: list[dict[str, float]], **changes: object) -> CandidateInput:
    payload = {
        "name": "feature-golden",
        "inputs": {
            "composition": COMPOSITION,
            "process": {"ls_mpm": 100.0},
            "heat_pattern": heat_pattern,
            **changes,
        },
    }
    return CandidateInput.model_validate(payload)


def test_feature_bundle_golden_for_piecewise_linear_route() -> None:
    candidate = _candidate([
        {"time_s": 0, "temperature_c": 20},
        {"time_s": 100, "temperature_c": 820},
        {"time_s": 140, "temperature_c": 820},
        {"time_s": 200, "temperature_c": 400},
    ])
    bundle = build_feature_bundle(candidate)
    actual = bundle.as_dict()

    assert (bundle.pipeline_id, bundle.pipeline_version) == (FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION)
    assert bundle.names == FEATURE_NAMES == EXPECTED_FEATURE_NAMES
    assert bundle.indices_by_group() == {
        "composition": tuple(range(14)),
        "process": (14,),
        "metallurgy": (15, 16, 17, 18, 19, 20),
        "heat_pattern": tuple(range(21, 30)),
    }
    assert tuple(item.group for item in bundle.features) == tuple(
        group for group, count in (("composition", 14), ("process", 1), ("metallurgy", 6), ("heat_pattern", 9))
        for _ in range(count)
    )
    assert CANONICAL_INPUT_PATHS == EXPECTED_CANONICAL_INPUT_PATHS
    assert len(bundle.names) == 30
    assert len(set(bundle.names)) == len(bundle.names)
    assert bundle.values.dtype == np.float64
    assert not bundle.values.flags.writeable
    assert actual["ce_iiw"] == pytest.approx(0.42)
    assert actual["pcm"] == pytest.approx(0.22416666666666667)
    assert actual["c_times_mn"] == pytest.approx(0.15)
    assert actual["si_plus_al"] == pytest.approx(0.34)
    assert actual["cr_plus_mo"] == pytest.approx(0.30)
    assert actual["microalloy_sum"] == pytest.approx(0.022)
    assert actual["peak_temperature_c"] == pytest.approx(820.0)
    assert actual["max_heating_rate_c_s"] == pytest.approx(8.0)
    assert actual["time_at_or_above_95pct_peak_s"] == pytest.approx(50.98214285714286)
    assert actual["time_at_or_above_700c_s"] == pytest.approx(72.14285714285714)
    assert actual["thermal_exposure_above_600c_c_s"] == pytest.approx(15_282.142857142859)
    assert actual["cooling_rate_800_to_500_c_s"] == pytest.approx(7.0)
    assert actual["cooling_800_to_500_observed"] == 1
    assert actual["reheat_count"] == 0
    assert actual["has_reheat"] == 0


def test_reheat_count_uses_25_degree_excursion_and_ignores_small_noise() -> None:
    candidate = _candidate([
        {"time_s": 0, "temperature_c": 20},
        {"time_s": 100, "temperature_c": 800},
        {"time_s": 120, "temperature_c": 700},
        {"time_s": 140, "temperature_c": 735},
        {"time_s": 160, "temperature_c": 650},
        {"time_s": 180, "temperature_c": 670},
        {"time_s": 220, "temperature_c": 400},
    ])
    actual = build_feature_bundle(candidate).as_dict()
    assert actual["reheat_count"] == 1
    assert actual["has_reheat"] == 1


@pytest.mark.parametrize("factor", [0.5, 2.0, 10.0])
def test_thermal_time_features_scale_with_time_but_rates_scale_inversely(factor: float) -> None:
    base = [
        {"time_s": 0, "temperature_c": 20},
        {"time_s": 100, "temperature_c": 820},
        {"time_s": 140, "temperature_c": 820},
        {"time_s": 200, "temperature_c": 400},
    ]
    scaled = [{"time_s": point["time_s"] * factor, "temperature_c": point["temperature_c"]} for point in base]
    original = build_feature_bundle(_candidate(base)).as_dict()
    transformed = build_feature_bundle(_candidate(scaled)).as_dict()

    for name in ("time_at_or_above_95pct_peak_s", "time_at_or_above_700c_s", "thermal_exposure_above_600c_c_s"):
        assert transformed[name] == pytest.approx(original[name] * factor)
    for name in ("max_heating_rate_c_s", "cooling_rate_800_to_500_c_s"):
        assert transformed[name] == pytest.approx(original[name] / factor)
    assert transformed["peak_temperature_c"] == original["peak_temperature_c"]


def test_threshold_integrals_are_non_negative_and_increase_with_temperature() -> None:
    low = _candidate([{"time_s": 0, "temperature_c": 20}, {"time_s": 100, "temperature_c": 650}, {"time_s": 200, "temperature_c": 20}])
    high = _candidate([{"time_s": 0, "temperature_c": 20}, {"time_s": 100, "temperature_c": 750}, {"time_s": 200, "temperature_c": 20}])
    low_features = build_feature_bundle(low).as_dict()
    high_features = build_feature_bundle(high).as_dict()
    assert low_features["time_at_or_above_700c_s"] == 0
    assert high_features["time_at_or_above_700c_s"] > 0
    assert high_features["thermal_exposure_above_600c_c_s"] > low_features["thermal_exposure_above_600c_c_s"] >= 0


def test_piecewise_linear_features_ignore_time_origin_and_redundant_points() -> None:
    route = [
        {"time_s": 10, "temperature_c": 20},
        {"time_s": 110, "temperature_c": 820},
        {"time_s": 150, "temperature_c": 820},
        {"time_s": 210, "temperature_c": 400},
    ]
    shifted = [{**point, "time_s": point["time_s"] + 50} for point in route]
    inserted = [route[0], {"time_s": 60, "temperature_c": 420}, *route[1:]]
    assert np.allclose(build_feature_bundle(_candidate(route)).values, build_feature_bundle(_candidate(shifted)).values)
    assert np.allclose(build_feature_bundle(_candidate(route)).values, build_feature_bundle(_candidate(inserted)).values)


def test_unobserved_cooling_rate_has_an_explicit_missingness_flag() -> None:
    route = [{"time_s": 0, "temperature_c": 20}, {"time_s": 100, "temperature_c": 750}, {"time_s": 200, "temperature_c": 600}]
    values = build_feature_bundle(_candidate(route)).as_dict()
    assert values["cooling_rate_800_to_500_c_s"] == 0
    assert values["cooling_800_to_500_observed"] == 0


def test_stage_clock_reset_is_rebased_without_artificial_stage_duration() -> None:
    points = [
        {"time_s": 0.0, "temperature_c": 20.0},
        {"time_s": 45.0, "temperature_c": 800.0},
        {"time_s": 34.0, "temperature_c": 790.0},
        {"time_s": 40.0, "temperature_c": 600.0},
    ]
    _normalize_stage_local_times(points)
    assert [point["time_s"] for point in points] == pytest.approx([0.0, 45.0, 45.000001, 51.000001])
    assert points[2]["segment_start"] is True


def test_stage_boundary_is_excluded_from_rates_and_threshold_crossings() -> None:
    route = [
        {"time_s": 0, "temperature_c": 20},
        {"time_s": 45, "temperature_c": 900},
        {"time_s": 45.000001, "temperature_c": 400, "segment_start": True},
        {"time_s": 51, "temperature_c": 350},
    ]
    values = build_feature_bundle(_candidate(route)).as_dict()
    assert values["max_heating_rate_c_s"] == pytest.approx(880 / 45)
    assert values["cooling_rate_800_to_500_c_s"] == 0
    assert values["cooling_800_to_500_observed"] == 0


def test_cooling_crossings_and_reheat_state_never_chain_across_stages() -> None:
    crossing_route = [
        {"time_s": 0, "temperature_c": 900},
        {"time_s": 10, "temperature_c": 750},
        {"time_s": 10.000001, "temperature_c": 700, "segment_start": True},
        {"time_s": 20, "temperature_c": 400},
    ]
    crossing = build_feature_bundle(_candidate(crossing_route)).as_dict()
    assert crossing["cooling_rate_800_to_500_c_s"] == 0
    assert crossing["cooling_800_to_500_observed"] == 0

    reheat_route = [
        {"time_s": 0, "temperature_c": 800},
        {"time_s": 10, "temperature_c": 700},
        {"time_s": 10.000001, "temperature_c": 400, "segment_start": True},
        {"time_s": 15, "temperature_c": 450},
        {"time_s": 20, "temperature_c": 300},
    ]
    reheat = build_feature_bundle(_candidate(reheat_route)).as_dict()
    assert reheat["reheat_count"] == 0
    assert reheat["has_reheat"] == 0


def test_missing_composition_requires_explicit_defaults() -> None:
    partial = _candidate(
        [{"time_s": 0, "temperature_c": 20}, {"time_s": 100, "temperature_c": 800}],
        composition={"C": 0.1},
    )
    with pytest.raises(ValueError, match="Missing composition"):
        build_feature_bundle(partial)
    bundle = build_feature_bundle(partial, {name: value for name, value in COMPOSITION.items() if name != "C"})
    assert bundle.as_dict()["C"] == 0.1
    assert np.isfinite(bundle.values).all()


def test_unknown_or_non_finite_composition_is_rejected() -> None:
    route = [{"time_s": 0, "temperature_c": 20}, {"time_s": 100, "temperature_c": 800}]
    with pytest.raises(ValueError, match="未対応の組成元素"):
        build_feature_bundle(_candidate(route, composition={**COMPOSITION, "Nb": 0.03}))
    with pytest.raises(ValueError, match="有限値"):
        build_feature_bundle(_candidate(route, composition={**COMPOSITION, "C": math.nan}))


def test_annealed_training_row_uses_the_candidate_pipeline() -> None:
    source = Path(__file__).parents[2] / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"
    data = load_workbook_data(source)
    row = next(item for item in data.observations if item["source"] == "焼鈍引張" and item["eligible"])
    candidate = candidate_from_observation(row)
    assert candidate is not None
    training_bundle = build_feature_bundle_from_observation(row, data.medians)
    assert training_bundle is not None
    prediction_bundle = build_feature_bundle(candidate, data.medians)
    assert training_bundle.features == prediction_bundle.features
