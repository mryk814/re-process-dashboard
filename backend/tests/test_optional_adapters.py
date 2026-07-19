"""Real artifact smoke tests for optional model-runtime profiles.

Run all three with:
uv run --extra runtime-sklearn --extra runtime-lightgbm --extra runtime-gpytorch pytest backend/tests/test_optional_adapters.py
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from material_workbench.model_packages import ModelPackageLoader


def _artifact(path: Path, relative: str) -> dict[str, object]:
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _package_root(tmp_path: Path, name: str, predictor: dict[str, object], model_path: Path) -> Path:
    root = tmp_path / name
    (root / "feature-pipeline").mkdir(parents=True)
    pipeline = root / "feature-pipeline" / "pipeline.json"
    pipeline.write_text('{"operations": []}', encoding="utf-8")
    manifest = {
        "schema_version": "model-package/v1", "package_id": name, "package_version": "1", "task_id": "test", "input_schema_version": "candidate-v1",
        "feature_pipeline": {"id": "optional-test", "version": "1", "spec": "feature-pipeline/pipeline.json", "output_features": ["C", "Mn"]},
        "predictors": [predictor],
        "provenance": {"training_data_id": "fixture", "feature_dataset_id": "fixture", "training_code_revision": "test"},
        "artifacts": [_artifact(pipeline, "feature-pipeline/pipeline.json"), _artifact(model_path, f"model-artifacts/{model_path.name}")],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_skops_adapter_loads_and_predicts_a_real_sklearn_artifact(tmp_path: Path) -> None:
    sklearn_linear = pytest.importorskip("sklearn.linear_model")
    skops_io = pytest.importorskip("skops.io")
    root = tmp_path / "skops"
    artifacts = root / "model-artifacts"
    artifacts.mkdir(parents=True)
    model = sklearn_linear.LinearRegression().fit(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]), np.array([1.0, 3.0, 4.0, 6.0]))
    model_path = artifacts / "linear.skops"
    skops_io.dump(model, model_path)
    predictor = {"id": "ts", "target": "TS", "unit": "MPa", "target_kind": "continuous", "runtime_type": "sklearn.skops.v1", "artifact": "model-artifacts/linear.skops", "predictive_family": "empirical_quantiles", "feature_names": ["C", "Mn"], "config": {"estimator_family": "linear_regression_v1"}}
    package = ModelPackageLoader().load(_package_root(tmp_path, "skops", predictor, model_path))
    result = package.load_predictor("ts").predict({"C": 0.5, "Mn": 0.5})
    assert result.point_estimate == pytest.approx(3.5, abs=1e-7)


def test_lightgbm_adapter_loads_and_predicts_a_native_text_booster(tmp_path: Path) -> None:
    lightgbm = pytest.importorskip("lightgbm")
    root = tmp_path / "lightgbm"
    artifacts = root / "model-artifacts"
    artifacts.mkdir(parents=True)
    features = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.5, 0.2], [0.2, 0.8]])
    target = np.array([1.0, 3.0, 4.0, 6.0, 2.4, 3.8])
    booster = lightgbm.train({"objective": "regression", "verbosity": -1, "min_data_in_leaf": 1, "num_leaves": 3, "seed": 1}, lightgbm.Dataset(features, label=target), num_boost_round=4)
    model_path = artifacts / "booster.txt"
    booster.save_model(str(model_path))
    predictor = {"id": "ts", "target": "TS", "unit": "MPa", "target_kind": "continuous", "runtime_type": "lightgbm.booster.v1", "artifact": "model-artifacts/booster.txt", "predictive_family": "empirical_quantiles", "feature_names": ["C", "Mn"], "config": {}}
    package = ModelPackageLoader().load(_package_root(tmp_path, "lightgbm", predictor, model_path))
    result = package.load_predictor("ts").predict({"C": 0.5, "Mn": 0.5})
    assert np.isfinite(result.point_estimate)
    assert result.point_estimate == pytest.approx(float(booster.predict(np.array([[0.5, 0.5]]))[0]))


def test_gpytorch_static_adapter_loads_and_predicts_safetensors(tmp_path: Path) -> None:
    torch = pytest.importorskip("torch")
    pytest.importorskip("gpytorch")
    safetensors = pytest.importorskip("safetensors.torch")
    root = tmp_path / "gpytorch"
    artifacts = root / "model-artifacts"
    artifacts.mkdir(parents=True)
    model_path = artifacts / "exact-rbf.safetensors"
    safetensors.save_file({
        "train_x": torch.tensor([[0.0, 0.0], [1.0, 1.0]], dtype=torch.float64),
        "train_y": torch.tensor([1.0, 5.0], dtype=torch.float64),
        "lengthscale": torch.tensor([1.0, 1.0], dtype=torch.float64),
        "outputscale": torch.tensor(2.0, dtype=torch.float64),
        "noise": torch.tensor(0.1, dtype=torch.float64),
        "mean": torch.tensor(0.0, dtype=torch.float64),
    }, str(model_path))
    predictor = {"id": "ts", "target": "TS", "unit": "MPa", "target_kind": "continuous", "runtime_type": "gpytorch.static_exact_rbf.v1", "architecture_id": "exact_rbf_v1", "artifact": "model-artifacts/exact-rbf.safetensors", "predictive_family": "normal", "feature_names": ["C", "Mn"], "config": {}}
    package = ModelPackageLoader().load(_package_root(tmp_path, "gpytorch", predictor, model_path))
    result = package.load_predictor("ts").predict({"C": 0.5, "Mn": 0.5})
    assert np.isfinite(result.point_estimate)
    assert result.quantiles["0.05"] < result.point_estimate < result.quantiles["0.95"]
