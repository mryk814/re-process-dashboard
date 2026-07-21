"""Flank-wear task: loader, feature golden, lognormal adapter, and API contract."""
from pathlib import Path

import math

import numpy as np
import pytest

from material_workbench.adapters.builtin_exact_gp import BuiltinExactGPAdapter
from material_workbench.flank_wear import TASK_ID, load_flank_wear_data
from material_workbench.flank_wear_feature_pipeline import FEATURE_NAMES, build_flank_wear_features
from material_workbench.model_lifecycle import canonical_training_dataset
from material_workbench.model_packages import PackageContractError, PredictorSpec, validate_predictive_summary
from material_workbench.schemas import CandidateInput
from material_workbench.task_registry import load_task_contracts


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "cutting_tool_flank_wear_synthetic_dataset.xlsx"

GOLDEN_FEATURES = {
    "C": 0.1, "Si": 0.3, "Mn": 0.75, "Cr": 0.2, "Ni": 0.1, "Mo": 0.05,
    "V": 0.0, "W": 0.0, "Co": 0.0, "Ti": 0.0, "Al": 0.03, "Cu": 0.0,
    "hardness_hv": 180.0, "tensile_mpa": 520.0,
    "wear_resistance_index": 1.2, "fracture_resistance_index": 1.0,
    "nose_radius_mm": 0.8, "rake_angle_deg": 6.0,
    "coating_cvd": 1.0, "coating_pvd": 0.0,
    "cutting_speed_mpm": 200.0, "log_cutting_speed": 5.2983173665,
    "feed_mm_rev": 0.2, "depth_of_cut_mm": 1.5,
    "coolant_dry": 0.0, "coolant_air_blow": 0.0, "coolant_mql": 0.0,
    "coolant_flood": 1.0, "coolant_high_pressure": 0.0,
    "interrupted_cut": 0.0, "holder_rigidity_ord": 1.0,
    "log_cutting_distance": 6.9087547793,
    "speed_distance": 6.1007959047,
    "hardness_distance": 2.4871517206,
    "wear_index_distance": 5.7572956494,
}


@pytest.fixture(scope="module")
def flank_data():
    return load_flank_wear_data(SOURCE)


def _canonical_candidate() -> CandidateInput:
    canonical = load_task_contracts()[TASK_ID].canonical_candidate
    return CandidateInput(name="golden", inputs={
        "composition": dict(canonical.composition),
        "process": dict(canonical.process),
        "categorical": dict(canonical.categorical),
        "heat_pattern": None,
    })


def test_loader_produces_run_grouped_wear_observations(flank_data):
    assert flank_data.run_count == 180
    assert len(flank_data.observations) == 2169
    eligible = [row for row in flank_data.observations if row["eligible"]]
    assert len(eligible) == 2036
    reasons = {reason for row in flank_data.observations for reason in row["eligibility_reasons"]}
    assert reasons == {"測定状態が有効ではありません", "摩耗量の測定値がありません"}
    sample = eligible[0]
    assert set(sample["outputs"]) <= {"VB平均[µm]", "VB最大[µm]"}
    assert "cutting_distance_m" in sample["features"]
    assert sample["run_context"]["material_key"].startswith("MAT-")


def test_feature_pipeline_golden_vector():
    bundle = build_flank_wear_features(_canonical_candidate(), {})
    assert bundle.names == FEATURE_NAMES
    actual = bundle.as_dict()
    assert set(actual) == set(GOLDEN_FEATURES)
    for name, expected in GOLDEN_FEATURES.items():
        assert actual[name] == pytest.approx(expected, abs=1e-9), name


def test_feature_pipeline_rejects_unknown_choice():
    candidate = _canonical_candidate()
    candidate.inputs.categorical["coolant_method"] = "潤滑たっぷり"
    with pytest.raises(ValueError):
        build_flank_wear_features(candidate, {})


def test_canonical_training_dataset_covers_eligible_rows(flank_data):
    payload = canonical_training_dataset(TASK_ID, flank_data, load_task_contracts()[TASK_ID])
    assert payload["task_id"] == TASK_ID
    assert len(payload["rows"]) == 2036
    first = payload["rows"][0]
    assert set(first["outputs"]) <= {"VB_mean", "VB_max"}
    assert tuple(item["name"] for item in payload["feature_pipeline"]["features"]) == FEATURE_NAMES


def _lognormal_predictor(tmp_path: Path, config: dict):
    rng = np.random.default_rng(20260721)
    train_x = rng.normal(size=(6, 2))
    arrays = {
        "train_x": train_x,
        "train_y": rng.normal(size=6),
        "feature_mean": np.zeros(2),
        "feature_scale": np.ones(2),
        "lengthscale": np.ones(2),
        "outputscale": np.asarray(1.0),
        "train_noise": np.asarray(0.1),
        "observation_noise": np.asarray(0.1),
        "mean": np.asarray(0.2),
    }
    covariance = np.exp(-0.5 * np.sum((train_x[:, None, :] - train_x[None, :, :]) ** 2, axis=2))
    covariance.flat[:: 7] += 0.1
    arrays["precision"] = np.linalg.inv(covariance)
    arrays["alpha"] = arrays["precision"] @ (arrays["train_y"] - 0.2)
    artifact = tmp_path / "target.npz"
    np.savez(artifact, **arrays)
    spec = PredictorSpec(
        id="vb", target="VB_mean", unit="µm", target_kind="continuous_positive",
        runtime_type="builtin.exact_gp.v1", architecture_id="exact_rbf_grouped_v1",
        artifact="target.npz", predictive_family="lognormal",
        feature_names=("a", "b"), config=config,
    )

    class _FakePackage:
        def artifact_path(self, path: str) -> Path:
            return tmp_path / path

    return BuiltinExactGPAdapter(), _FakePackage(), spec


def test_builtin_exact_gp_lognormal_summary_semantics(tmp_path):
    adapter, package, spec = _lognormal_predictor(tmp_path, {"latent_transform": "log1p"})
    predictor = adapter.load(package, spec)
    summary = predictor.predict({"a": 0.3, "b": -0.2})
    validate_predictive_summary(summary, spec)
    assert summary.point_statistic == "median"
    assert summary.distribution["family"] == "lognormal"
    assert summary.distribution["support"] == "nonnegative"
    assert summary.quantiles["0.05"] >= 0.0
    assert summary.quantiles["0.05"] <= summary.quantiles["0.50"] <= summary.quantiles["0.95"]
    assert summary.point_estimate == pytest.approx(summary.quantiles["0.50"])
    assert summary.point_estimate == pytest.approx(
        max(math.expm1(summary.distribution["log_mean"]), 0.0)
    )
    assert summary.distribution["std"] > 0


def test_builtin_exact_gp_lognormal_requires_log1p_config(tmp_path):
    adapter, package, spec = _lognormal_predictor(tmp_path, {})
    with pytest.raises(PackageContractError):
        adapter.load(package, spec)


def _create_flank_project(client):
    catalog = client.get("/api/task-definitions").json()
    starter = next(
        item for item in catalog
        if item["definition"]["task_definition"]["id"] == TASK_ID
    )["starter_candidate"]
    project = client.post(
        "/api/projects",
        json={"name": "切削摩耗の候補検討", "task_id": TASK_ID, "target_values": {"VB_max": 200}},
    ).json()
    candidate = client.post(f"/api/projects/{project['id']}/candidates", json=starter).json()
    return project, candidate


def test_flank_wear_api_preview_and_goal_probability(client):
    project, candidate = _create_flank_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/preview",
        params={"expected_revision": candidate["revision"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert set(payload["predictions"]) == {"VB_mean", "VB_max"}
    prediction = payload["predictions"]["VB_max"]
    assert 0 <= prediction["lower"] <= prediction["value"] <= prediction["upper"]
    assert prediction["goal_direction"] == "at_most"
    assert 0 <= prediction["goal_probability"] <= 1
    assert payload["support"]["status"] in {"supported", "caution", "extrapolated"}
    assert set(payload["support"]["components"]) == {"composition", "material", "tool", "process"}


def test_flank_wear_wear_curve_over_cutting_distance(client):
    project, candidate = _create_flank_project(client)
    response = client.get(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/response-curve",
        params={
            "expected_revision": candidate["revision"],
            "target": "VB_mean",
            "variable": "cutting_distance_m",
            "points": 9,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["variable"]["id"] == "process.cutting_distance_m"
    assert payload["variable"]["unit"] == "m"
    points = payload["points"]
    assert len(points) == 9
    assert points[0]["x"] == 0.0
    assert all(point["lower"] <= point["value"] <= point["upper"] for point in points)
    # 摩耗曲線: 切削距離に対して単調非減少であること
    values = [point["value"] for point in points]
    assert all(later >= earlier for earlier, later in zip(values, values[1:]))
    assert values[0] < values[-1]


def test_flank_wear_model_package_status_uses_task_profile(client):
    project, _ = _create_flank_project(client)
    response = client.get(f"/api/projects/{project['id']}/model-package")
    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == TASK_ID
    assert {item["target"] for item in payload["predictors"]} == {"VB_mean", "VB_max"}
    assert all(item["predictive_family"] == "lognormal" for item in payload["predictors"])
    assert payload["quality_report"]["split"] == "leave-one-parent-condition-out"


def test_flank_wear_actual_measurement_accepts_micrometer(client):
    project, candidate = _create_flank_project(client)
    response = client.post(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/actuals",
        params={"expected_revision": candidate["revision"]},
        json={"property": "VB_max", "mean": 88.0, "std": 4.0, "replicates": 3, "unit": "µm"},
    )
    assert response.status_code == 201
    mismatch = client.post(
        f"/api/projects/{project['id']}/candidates/{candidate['id']}/actuals",
        params={"expected_revision": candidate["revision"] + 0},
        json={"property": "VB_max", "mean": 88.0, "unit": "%"},
    )
    assert mismatch.status_code == 422
