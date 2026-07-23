import json
import hashlib
import shutil

import numpy as np
import pytest

from material_workbench.feature_pipeline import COMPOSITION_NAMES, build_feature_bundle
from material_workbench.heat_time import line_speed_scaled_times
from material_workbench.runtime import FEATURE_NAMES, HEAT_STAGE_TEMPERATURE_VARIABLE, ModelRuntime
from material_workbench.schemas import CandidateInput


def _heat_candidate(*, stage_names: tuple[str | None, ...] = (None, None, None)) -> CandidateInput:
    return CandidateInput.model_validate({
        "name": "curve",
        "inputs": {
            "composition": {"C": 0.1},
            "process": {"ls_mpm": 60.0},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 20, "stage_name": stage_names[0]},
                {"time_s": 60, "temperature_c": 620, "stage_name": stage_names[1]},
                {"time_s": 120, "temperature_c": 820, "stage_name": stage_names[2]},
            ],
        },
    })


def test_line_speed_response_scales_every_heat_time_from_the_entry() -> None:
    candidate = _heat_candidate()

    ModelRuntime._set_curve_variable(candidate, "process.ls_mpm", 120.0)  # type: ignore[arg-type]

    assert candidate.inputs.process["ls_mpm"] == 120.0
    assert [point.time_s for point in candidate.inputs.heat_pattern or []] == [0.0, 30.0, 60.0]


def test_line_speed_time_scaling_helper_is_pure() -> None:
    candidate = _heat_candidate()
    points = candidate.inputs.heat_pattern or []

    scaled = line_speed_scaled_times(points, 60.0, 120.0)

    assert scaled == (0.0, 30.0, 60.0)
    assert [point.time_s for point in points] == [0.0, 60.0, 120.0]


def test_elapsed_time_response_keeps_heat_times_when_line_speed_changes() -> None:
    candidate = _heat_candidate()
    candidate.inputs.heat_time_basis = "elapsed_time"

    ModelRuntime._set_curve_variable(candidate, "process.ls_mpm", 120.0)  # type: ignore[arg-type]

    assert candidate.inputs.process["ls_mpm"] == 120.0
    assert [point.time_s for point in candidate.inputs.heat_pattern or []] == [0.0, 60.0, 120.0]


def test_named_stage_response_shifts_all_matching_points_without_flattening_shape() -> None:
    candidate = CandidateInput.model_validate({
        "name": "curve",
        "inputs": {
            "composition": {"C": 0.1},
            "process": {"ls_mpm": 60.0},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 20},
                {"time_s": 40, "temperature_c": 600, "stage_name": "加熱1"},
                {"time_s": 60, "temperature_c": 640, "stage_name": "加熱1"},
                {"time_s": 120, "temperature_c": 400},
            ],
        },
    })

    ModelRuntime._set_curve_variable(candidate, HEAT_STAGE_TEMPERATURE_VARIABLE, 670.0, "加熱1", 50.0)  # type: ignore[arg-type]

    matching = [point.temperature_c for point in candidate.inputs.heat_pattern or [] if point.stage_name == "加熱1"]
    assert matching == [650.0, 690.0]


def test_missing_named_stage_is_inserted_on_the_existing_curve_without_changing_baseline_features() -> None:
    candidate = _heat_candidate()
    defaults = {name: 0.0 for name in COMPOSITION_NAMES}
    before = build_feature_bundle(candidate, defaults).as_dict()
    current = ModelRuntime._heat_stage_temperature(candidate.inputs.heat_pattern or [], 60.0, "加熱1", 30.0)

    ModelRuntime._set_curve_variable(candidate, HEAT_STAGE_TEMPERATURE_VARIABLE, current, "加熱1", 30.0)  # type: ignore[arg-type]

    after = build_feature_bundle(candidate, defaults).as_dict()
    assert len(candidate.inputs.heat_pattern or []) == 4
    assert next(point for point in candidate.inputs.heat_pattern or [] if point.stage_name == "加熱1").time_s == 30.0
    assert after == pytest.approx(before)


def test_missing_named_stage_is_not_interpolated_across_a_segment_boundary() -> None:
    candidate = CandidateInput.model_validate({
        "name": "segmented",
        "inputs": {
            "composition": {"C": 0.1},
            "process": {"ls_mpm": 60.0},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 20},
                {"time_s": 60, "temperature_c": 600},
                {"time_s": 120, "temperature_c": 800, "segment_start": True},
            ],
        },
    })

    with pytest.raises(ValueError, match="非連続な工程境界"):
        ModelRuntime._heat_stage_temperature(candidate.inputs.heat_pattern or [], 60.0, "加熱1", 90.0)


def test_named_stage_shift_rejects_any_point_outside_the_physical_temperature_range() -> None:
    candidate = CandidateInput.model_validate({
        "name": "too-hot",
        "inputs": {
            "composition": {"C": 0.1},
            "process": {"ls_mpm": 60.0},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 20},
                {"time_s": 60, "temperature_c": 1700, "stage_name": "加熱1"},
                {"time_s": 120, "temperature_c": 1800, "stage_name": "加熱1"},
            ],
        },
    })

    with pytest.raises(ValueError, match="物理温度範囲"):
        ModelRuntime._set_curve_variable(candidate, HEAT_STAGE_TEMPERATURE_VARIABLE, 1800, "加熱1", 60.0)  # type: ignore[arg-type]


def test_grouped_oof_calibration_and_parent_condition_support(client) -> None:
    runtime: ModelRuntime = client.app.state.task_registry.runtime_for("annealed-properties-v1")
    assert runtime.support_reference is not None
    assert runtime.support_reference.parent_vectors.shape[1] == len(FEATURE_NAMES)
    assert len(runtime.support_reference.parent_rows) == len(runtime.support_reference.loo_nearest_distances)
    assert len(runtime.support_reference.parent_observation_rows) == len(runtime.support_reference.parent_rows)
    for model in runtime.models.values():
        assert model.calibration_folds == 5
        assert len(model.oof_residuals) == len(model.rows)
        # Multiple measurement repeats exist, so calibration must split by parent condition rather than row.
        assert len({row["parent_key"] for row in model.rows}) < len(model.rows)
        lower, upper = model.interval_offsets()
        assert lower < upper


def test_similarity_summarizes_repeats_and_keeps_layers_distinct(client) -> None:
    candidate = client.get("/api/projects/default/candidates").json()[0]
    preview = client.post(f"/api/projects/default/candidates/{candidate['id']}/preview", params={"expected_revision": candidate["revision"]}).json()
    similar = preview["similar"]
    assert preview["support"]["distance"] >= 0
    assert set(preview["support"]["components"]) == {"composition", "process", "metallurgy", "heat_pattern"}
    assert all(value >= 0 for value in preview["support"]["components"].values())
    training_parents = {item["parent_key"] for item in similar if item["layer"] == "training"}
    historical_parents = {item["parent_key"] for item in similar if item["layer"] == "historical"}
    assert training_parents.isdisjoint(historical_parents)
    assert all(item["repeat_summary"] for item in similar)
    for item in similar:
        assert len(item["observation_ids"]) >= 1
        assert all(summary["n"] >= 1 for summary in item["repeat_summary"].values())
        assert item["outputs"] == {name: summary["mean"] for name, summary in item["repeat_summary"].items()}
        assert item["melt_key"].startswith("ME-")
        assert item["process_key"].startswith("AN-")
        assert item["process_label"] == "焼鈍履歴"


def test_default_model_package_loads_and_matches_its_smoke_contract(client) -> None:
    runtime: ModelRuntime = client.app.state.task_registry.runtime_for("annealed-properties-v1")
    assert runtime.model_package is not None
    assert runtime.model_package.manifest.package_id == "annealed-gp-2026-07"
    smoke = runtime.model_package.manifest.smoke_test
    assert smoke is not None
    candidate = CandidateInput.model_validate(json.loads(runtime.model_package.artifact_path(smoke.input).read_text(encoding="utf-8")))
    values = build_feature_bundle(candidate, runtime.composition_defaults).as_dict()
    expected = json.loads(runtime.model_package.artifact_path(smoke.expected).read_text(encoding="utf-8"))
    actual = {target: predictor.predict(values, seed=0).point_estimate for target, predictor in runtime.package_predictors.items()}
    assert set(actual) == set(expected)
    assert all(np.isclose(actual[target], expected[target], rtol=1e-7, atol=1e-7) for target in actual)


def test_canonical_input_uses_package_defaults_and_one_heat_definition(client) -> None:
    runtime: ModelRuntime = client.app.state.task_registry.runtime_for("annealed-properties-v1")
    candidate = CandidateInput(
        name="partial",
        inputs={
            "composition": {"C": 0.1},
            "process": {"ls_mpm": 100},
            "categorical": {},
            "heat_pattern": [
                {"time_s": 0, "temperature_c": 20},
                {"time_s": 100, "temperature_c": 820},
                {"time_s": 140, "temperature_c": 820},
                {"time_s": 200, "temperature_c": 400},
            ],
        },
    )
    canonical = runtime.canonical_input(candidate)  # type: ignore[arg-type]
    features = canonical["feature_vector"]
    heat = canonical["heat_summary"]
    assert canonical["composition_imputation"]["imputed_elements"] == [name for name in runtime.composition_defaults if name != "C"]
    assert canonical["composition_imputation"]["reference_stats_sha256"] == runtime.composition_defaults_sha256
    assert heat["peak_temperature_c"] == features["peak_temperature_c"]
    np.testing.assert_allclose(
        [heat["time_at_or_above_95pct_peak_s"]],
        [features["time_at_or_above_95pct_peak_s"]],
    )
    assert heat["reheat_count"] == features["reheat_count"]
    assert heat["cooling_800_to_500_observed"] == bool(features["cooling_800_to_500_observed"])


def test_runtime_rejects_pipeline_version_that_only_preserves_feature_names(client, tmp_path) -> None:
    runtime: ModelRuntime = client.app.state.task_registry.runtime_for("annealed-properties-v1")
    assert runtime.model_package is not None
    root = tmp_path / "stale-package"
    shutil.copytree(runtime.model_package.root, root)
    pipeline_path = root / "feature-pipeline" / "pipeline.json"
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    pipeline["version"] = "0.0.0-stale"
    pipeline_path.write_text(json.dumps(pipeline), encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifact = next(item for item in manifest["artifacts"] if item["path"] == "feature-pipeline/pipeline.json")
    artifact["bytes"] = pipeline_path.stat().st_size
    artifact["sha256"] = hashlib.sha256(pipeline_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="feature pipeline id/version differs"):
        ModelRuntime(runtime.data, package_root=root)


def test_runtime_rejects_package_canonical_input_order_that_differs_from_task_definition(client, tmp_path) -> None:
    runtime: ModelRuntime = client.app.state.task_registry.runtime_for("annealed-properties-v1")
    assert runtime.model_package is not None
    root = tmp_path / "wrong-canonical-order"
    shutil.copytree(runtime.model_package.root, root)
    manifest_path = root / "manifest.json"
    pipeline_path = root / "feature-pipeline" / "pipeline.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    wrong_order = list(reversed(manifest["feature_pipeline"]["canonical_input_paths"]))
    manifest["feature_pipeline"]["canonical_input_paths"] = wrong_order
    pipeline["canonical_input_paths"] = wrong_order
    pipeline_path.write_text(json.dumps(pipeline), encoding="utf-8")
    artifact = next(item for item in manifest["artifacts"] if item["path"] == "feature-pipeline/pipeline.json")
    artifact["bytes"] = pipeline_path.stat().st_size
    artifact["sha256"] = hashlib.sha256(pipeline_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="canonical input order does not match TaskDefinition"):
        ModelRuntime(runtime.data, package_root=root)
