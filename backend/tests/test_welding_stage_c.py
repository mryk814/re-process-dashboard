from pathlib import Path
import sys

from fastapi.testclient import TestClient

from backend.scripts.generators import build_welding_stage_c_model_package as stage_c_builder_cli
from decision_workbench.data.observation_profile import (
    build_observation_training_dataset,
    load_observation_profile,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.observation_model_builder import build
from decision_workbench.modeling.observation_regression import resolve_spec
from decision_workbench.task_composition.builtin.welding import observation_declaration


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "welding_consumable_multistage_synthetic_dataset.xlsx"
PACKAGE = ROOT / "models" / "packages" / "welding-stage-c-ridge-v1"
TASK_ID = "welding-stage-c-properties-v1"
DECLARATION = observation_declaration(TASK_ID)
PROFILE_PATH = DECLARATION.profile_path
SPEC = resolve_spec(DECLARATION)
TENSILE_FEATURES = SPEC.target_features["TS"]
CHARPY_FEATURES = SPEC.target_features["CHARPY_ENERGY"]
CORROSION_FEATURES = SPEC.target_features["CORROSION_RATE"]
TARGET_FAMILY = SPEC.target_family


def test_stage_c_builder_rebuilds_a_verified_package(tmp_path: Path) -> None:
    destination = tmp_path / "welding-stage-c-ridge-v1"

    build(SOURCE, destination, declaration=DECLARATION)

    package = ModelPackageLoader().load(destination)
    assert package.manifest.task_id == TASK_ID
    assert {item.target for item in package.manifest.predictors} == set(
        SPEC.target_features
    )


def test_stage_c_builder_cli_passes_the_runtime_declaration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_build(
        source: Path,
        destination: Path,
        *,
        declaration,
        replace: bool,
    ) -> None:
        captured.update(
            source=source,
            destination=destination,
            declaration=declaration,
            replace=replace,
        )

    destination = tmp_path / "package"
    monkeypatch.setattr(stage_c_builder_cli, "build", fake_build)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_welding_stage_c_model_package.py",
            "--source",
            str(SOURCE),
            "--output",
            str(destination),
            "--replace",
        ],
    )

    assert stage_c_builder_cli.main() == 0
    assert captured == {
        "source": SOURCE,
        "destination": destination,
        "declaration": DECLARATION,
        "replace": True,
    }


def _create_stage_c_candidate(client: TestClient) -> tuple[str, dict]:
    created = client.post("/api/projects", json={"name": "Stage C API確認", "task_id": TASK_ID})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    task = next(
        item for item in client.get("/api/task-definitions").json()
        if item["definition"]["task_definition"]["id"] == TASK_ID
    )
    candidate = client.post(
        f"/api/projects/{project_id}/candidates",
        json=task["starter_candidate"],
    )
    assert candidate.status_code == 201, candidate.text
    return project_id, candidate.json()


def test_stage_c_profile_and_package_keep_target_specific_cohorts() -> None:
    dataset = build_observation_training_dataset(SOURCE, load_observation_profile(PROFILE_PATH))
    package = ModelPackageLoader().load(PACKAGE)
    predictors = {item.target: item for item in package.manifest.predictors}

    assert {name: len(view.rows) for name, view in dataset.views.items()} == {
        "tensile": 600,
        "charpy": 2700,
        "corrosion": 103,
    }
    assert {item.runtime_type for item in predictors.values()} == {"builtin.linear.v1"}
    assert predictors["TS"].feature_names == TENSILE_FEATURES
    assert predictors["CHARPY_ENERGY"].feature_names == CHARPY_FEATURES
    assert predictors["CORROSION_RATE"].feature_names == CORROSION_FEATURES
    for target, predictor in predictors.items():
        config = predictor.config
        assert config["observation_family"] == TARGET_FAMILY[target]
        assert config["training_cohort"] == f"{TARGET_FAMILY[target]}:target-usable"
        assert config["training_rows"] in {600, 2700, 103}
        assert config["evaluation_groups"] in {300, 103}
        assert config["profile_digest"] == dataset.profile_digest
    assert not any(path.suffix.lower() in {".pkl", ".pickle", ".joblib"} for path in PACKAGE.rglob("*"))


def test_stage_c_predicts_curves_restores_snapshot_and_inspects_training_views(
    client: TestClient,
) -> None:
    project_id, candidate = _create_stage_c_candidate(client)
    base = f"/api/projects/{project_id}/candidates/{candidate['id']}"

    detailed = client.post(
        f"{base}/predict",
        params={"expected_revision": candidate["revision"]},
    )
    assert detailed.status_code == 200, detailed.text
    prediction = detailed.json()["prediction"]
    assert set(prediction["predictions"]) == {
        "TS", "YS", "EL", "RA", "CHARPY_ENERGY", "BRITTLE_FRACTURE", "CORROSION_RATE",
    }
    predictor_meta = prediction["model_meta"]["package"]["predictors"]
    assert predictor_meta["TS"]["feature_names"] == list(TENSILE_FEATURES)
    assert predictor_meta["CHARPY_ENERGY"]["training_rows"] == 2700
    assert predictor_meta["CORROSION_RATE"]["evaluation_groups"] == 103
    snapshot = detailed.json()["snapshot"]
    restored = client.post(f"/api/projects/{project_id}/snapshots/{snapshot['id']}/restore")
    assert restored.status_code == 201, restored.text
    assert restored.json()["inputs"] == candidate["inputs"]

    curve = client.get(
        f"{base}/response-curve",
        params={
            "expected_revision": candidate["revision"],
            "target": "CHARPY_ENERGY",
            "variable": "process.test_temperature_c",
            "points": 11,
        },
    )
    assert curve.status_code == 200, curve.text
    assert len(curve.json()["points"]) == 11
    assert curve.json()["variable"]["id"] == "process.test_temperature_c"
    assert {point["x"] for point in curve.json()["points"]} >= {-60.0, 0.0}

    tensile_curve = client.get(
        f"{base}/response-curve",
        params={
            "expected_revision": candidate["revision"],
            "target": "TS",
            "variable": "process.test_temperature_c",
            "points": 11,
        },
    )
    assert tensile_curve.status_code == 422
    assert tensile_curve.json()["code"] == "response_curve_not_applicable"
    assert "入力に使わないため応答曲線を作成できません" in tensile_curve.json()["message"]

    selected = client.get(
        f"/api/projects/{project_id}/model-package/training-data",
        params={"stage": "selected", "target": "CHARPY_ENERGY", "limit": 2},
    )
    assert selected.status_code == 200, selected.text
    assert selected.json()["total"] == 2700
    assert selected.json()["parent_conditions"] == 300
    assert all(row["parent_key"].startswith("WR-") for row in selected.json()["rows"])

    tensile_features = client.get(
        f"/api/projects/{project_id}/model-package/training-data",
        params={"stage": "features", "target": "TS", "limit": 1},
    ).json()
    tensile_columns = {item["key"] for item in tensile_features["columns"]}
    assert "feature.process.heat_input_kj_per_mm" in tensile_columns
    assert "feature.process.test_temperature_c" not in tensile_columns

    corrosion_features = client.get(
        f"/api/projects/{project_id}/model-package/training-data",
        params={"stage": "features", "target": "CORROSION_RATE", "limit": 1},
    ).json()
    corrosion_columns = {item["key"] for item in corrosion_features["columns"]}
    assert "feature.categorical.test_solution::3.5%NaCl" in corrosion_columns
    assert "feature.process.heat_input_kj_per_mm" not in corrosion_columns
    assert corrosion_features["total"] == 103
