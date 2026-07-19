import json
import hashlib
import shutil

import numpy as np
import pytest

from material_workbench.feature_pipeline import build_feature_bundle
from material_workbench.runtime import FEATURE_NAMES, ModelRuntime
from material_workbench.schemas import CandidateInput


def test_grouped_oof_calibration_and_parent_condition_support(client) -> None:
    runtime: ModelRuntime = client.app.state.runtime
    assert runtime.support_reference is not None
    assert runtime.support_reference.parent_vectors.shape[1] == len(FEATURE_NAMES)
    assert len(runtime.support_reference.parent_rows) == len(runtime.support_reference.loo_nearest_distances)
    for model in runtime.models.values():
        assert model.calibration_folds == 5
        assert len(model.oof_residuals) == len(model.rows)
        # Multiple measurement repeats exist, so calibration must split by parent condition rather than row.
        assert len({row["parent_key"] for row in model.rows}) < len(model.rows)
        lower, upper = model.interval_offsets()
        assert lower < upper


def test_default_model_package_loads_and_matches_its_smoke_contract(client) -> None:
    runtime: ModelRuntime = client.app.state.runtime
    assert runtime.model_package is not None
    assert runtime.model_package.manifest.package_id == "annealed-ridge-2026-07"
    smoke = runtime.model_package.manifest.smoke_test
    assert smoke is not None
    candidate = CandidateInput.model_validate(json.loads(runtime.model_package.artifact_path(smoke["input"]).read_text(encoding="utf-8")))
    values = build_feature_bundle(candidate, runtime.composition_defaults).as_dict()
    expected = json.loads(runtime.model_package.artifact_path(smoke["expected"]).read_text(encoding="utf-8"))
    actual = {target: predictor.predict(values, seed=0).point_estimate for target, predictor in runtime.package_predictors.items()}
    assert set(actual) == set(expected)
    assert all(np.isclose(actual[target], expected[target], rtol=1e-7, atol=1e-7) for target in actual)


def test_canonical_input_uses_package_defaults_and_one_heat_definition(client) -> None:
    runtime: ModelRuntime = client.app.state.runtime
    candidate = CandidateInput(
        name="partial",
        composition={"C": 0.1},
        thickness_mm=1.4,
        line_speed_m_min=100,
        coating="GI",
        heat_pattern=[
            {"time_s": 0, "temperature_c": 20},
            {"time_s": 100, "temperature_c": 820},
            {"time_s": 140, "temperature_c": 820},
            {"time_s": 200, "temperature_c": 400},
        ],
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
    runtime: ModelRuntime = client.app.state.runtime
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
    with pytest.raises(ValueError, match="pipeline specification id/version"):
        ModelRuntime(runtime.data, package_root=root)
