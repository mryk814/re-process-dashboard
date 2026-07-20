from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from material_workbench.model_packages import ModelPackageLoader, PackageContractError


def _artifact(path: Path, relative: str) -> dict[str, object]:
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _rbf(left: np.ndarray, right: np.ndarray, lengthscale: np.ndarray) -> np.ndarray:
    scaled = (left[:, None, :] - right[None, :, :]) / lengthscale
    return np.exp(-0.5 * np.sum(scaled * scaled, axis=2))


def _icm_arrays(task_covariance: np.ndarray) -> dict[str, np.ndarray]:
    train_x = np.array([[-1.0, 0.0], [1.0, 0.0], [0.0, -1.0], [0.0, 1.0]])
    train_task = np.array([0, 0, 1, 1], dtype=np.int64)
    train_y = np.array([1.0, 3.0, 10.0, 30.0])
    lengthscale = np.ones(2)
    mean = np.array([2.0, 20.0])
    train_noise = np.array([0.1, 0.2])
    covariance = task_covariance[np.ix_(train_task, train_task)] * _rbf(train_x, train_x, lengthscale)
    covariance[np.diag_indices_from(covariance)] += train_noise[train_task]
    precision = np.linalg.inv(covariance)
    return {
        "train_x": train_x,
        "train_task": train_task,
        "train_y": train_y,
        "feature_mean": np.zeros(2),
        "feature_scale": np.ones(2),
        "lengthscale": lengthscale,
        "task_covariance": task_covariance,
        "mean": mean,
        "train_noise": train_noise,
        "observation_noise": np.array([0.1, 0.2]),
        "precision": precision,
        "alpha": precision @ (train_y - mean[train_task]),
    }


def _write_multitask_package(
    tmp_path: Path,
    arrays: dict[str, np.ndarray],
    *,
    name: str = "mtgp",
    predictor_overrides: dict[str, object] | None = None,
    override_ids: set[str] | None = None,
) -> Path:
    root = tmp_path / name
    (root / "feature-pipeline").mkdir(parents=True)
    (root / "model-artifacts").mkdir()
    pipeline = root / "feature-pipeline" / "pipeline.json"
    pipeline.write_text(json.dumps({
        "id": "mtgp-test",
        "version": "1",
        "canonical_input_paths": ["composition.C", "composition.Mn"],
        "features": [
            {"name": "C", "unit": "mass%", "meaning": "C composition", "group": "composition"},
            {"name": "Mn", "unit": "mass%", "meaning": "Mn composition", "group": "composition"},
        ],
    }), encoding="utf-8")
    model = root / "model-artifacts" / "multitask.npz"
    np.savez(model, **arrays)
    predictors = []
    for task_index, target in enumerate(("TS", "YS")):
        predictor: dict[str, object] = {
            "id": f"{target.lower()}-mtgp", "target": target, "unit": "MPa", "target_kind": "continuous",
            "runtime_type": "builtin.multitask_gp.v1", "architecture_id": "icm_rbf_v1",
            "artifact": "model-artifacts/multitask.npz", "predictive_family": "normal",
            "feature_names": ["C", "Mn"], "config": {"task_index": task_index},
        }
        if override_ids is None or predictor["id"] in override_ids:
            predictor.update(predictor_overrides or {})
        predictors.append(predictor)
    manifest = {
        "schema_version": "model-package/v1", "package_id": name, "package_version": "1", "task_id": "test", "input_schema_version": "candidate-v1",
        "feature_pipeline": {"id": "mtgp-test", "version": "1", "spec": "feature-pipeline/pipeline.json", "canonical_input_paths": ["composition.C", "composition.Mn"], "output_features": ["C", "Mn"]},
        "predictors": predictors,
        "provenance": {"training_data_id": "x", "feature_dataset_id": "y", "training_code_revision": "z"},
        "artifacts": [_artifact(pipeline, "feature-pipeline/pipeline.json"), _artifact(model, "model-artifacts/multitask.npz")],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _write_single_task_package(tmp_path: Path, *, outputscale: float) -> Path:
    root = tmp_path / "single"
    (root / "feature-pipeline").mkdir(parents=True)
    (root / "model-artifacts").mkdir()
    pipeline = root / "feature-pipeline" / "pipeline.json"
    pipeline.write_text(json.dumps({
        "id": "single-test",
        "version": "1",
        "canonical_input_paths": ["composition.C", "composition.Mn"],
        "features": [
            {"name": "C", "unit": "mass%", "meaning": "C composition", "group": "composition"},
            {"name": "Mn", "unit": "mass%", "meaning": "Mn composition", "group": "composition"},
        ],
    }), encoding="utf-8")
    train_x = np.array([[-1.0, 0.0], [1.0, 0.0]])
    train_y = np.array([1.0, 3.0])
    lengthscale = np.ones(2)
    covariance = outputscale * _rbf(train_x, train_x, lengthscale)
    covariance[np.diag_indices_from(covariance)] += 0.1
    precision = np.linalg.inv(covariance)
    model = root / "model-artifacts" / "single.npz"
    np.savez(
        model,
        train_x=train_x, train_y=train_y,
        feature_mean=np.zeros(2), feature_scale=np.ones(2),
        lengthscale=lengthscale, outputscale=np.asarray(outputscale),
        train_noise=np.asarray(0.1), observation_noise=np.asarray(0.1),
        mean=np.asarray(2.0), precision=precision, alpha=precision @ (train_y - 2.0),
    )
    manifest = {
        "schema_version": "model-package/v1", "package_id": "single", "package_version": "1", "task_id": "test", "input_schema_version": "candidate-v1",
        "feature_pipeline": {"id": "single-test", "version": "1", "spec": "feature-pipeline/pipeline.json", "canonical_input_paths": ["composition.C", "composition.Mn"], "output_features": ["C", "Mn"]},
        "predictors": [{
            "id": "ts-gp", "target": "TS", "unit": "MPa", "target_kind": "continuous",
            "runtime_type": "builtin.exact_gp.v1", "architecture_id": "exact_rbf_grouped_v1",
            "artifact": "model-artifacts/single.npz", "predictive_family": "normal", "feature_names": ["C", "Mn"],
        }],
        "provenance": {"training_data_id": "x", "feature_dataset_id": "y", "training_code_revision": "z"},
        "artifacts": [_artifact(pipeline, "feature-pipeline/pipeline.json"), _artifact(model, "model-artifacts/single.npz")],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _correlated_task_covariance(correlation: float) -> np.ndarray:
    variances = np.array([2.0, 8.0])
    covariance = correlation * float(np.sqrt(variances.prod()))
    return np.array([[variances[0], covariance], [covariance, variances[1]]])


def test_multitask_prediction_is_deterministic_and_semantically_consistent(tmp_path: Path) -> None:
    package = ModelPackageLoader().load(_write_multitask_package(tmp_path, _icm_arrays(_correlated_task_covariance(0.9))))
    for predictor_id in ("ts-mtgp", "ys-mtgp"):
        predictor = package.load_predictor(predictor_id)
        first = predictor.predict({"C": 0.4, "Mn": -0.3}, seed=1)
        second = predictor.predict({"C": 0.4, "Mn": -0.3}, seed=2)
        assert first == second
        assert first.quantiles["0.05"] <= first.quantiles["0.50"] <= first.quantiles["0.95"]
        assert first.distribution["family"] == "normal"
        components = first.uncertainty_components
        assert components is not None
        assert components["total_predictive_variance"] == pytest.approx(
            components["latent_model_variance"] + components["observation_noise_variance"]
        )
        assert first.distribution["std"] == pytest.approx(components["total_predictive_std"])


def test_block_diagonal_task_covariance_reduces_to_single_task_exact_gp(tmp_path: Path) -> None:
    independent = np.array([[2.0, 0.0], [0.0, 8.0]])
    multitask = ModelPackageLoader().load(_write_multitask_package(tmp_path, _icm_arrays(independent)))
    single = ModelPackageLoader().load(_write_single_task_package(tmp_path, outputscale=2.0))
    point = {"C": 0.7, "Mn": 0.2}
    joint = multitask.load_predictor("ts-mtgp").predict(point)
    alone = single.load_predictor("ts-gp").predict(point)
    assert joint.point_estimate == pytest.approx(alone.point_estimate, abs=1e-10)
    assert joint.uncertainty_components is not None and alone.uncertainty_components is not None
    assert joint.uncertainty_components["latent_model_variance"] == pytest.approx(
        alone.uncertainty_components["latent_model_variance"], abs=1e-10
    )


def test_cross_task_correlation_transfers_information_between_targets(tmp_path: Path) -> None:
    independent = ModelPackageLoader().load(
        _write_multitask_package(tmp_path, _icm_arrays(_correlated_task_covariance(0.0)), name="independent")
    )
    correlated = ModelPackageLoader().load(
        _write_multitask_package(tmp_path, _icm_arrays(_correlated_task_covariance(0.9)), name="correlated")
    )
    # Only task 1 has training evidence near (0, 1); the correlated model must use it for task 0.
    point = {"C": 0.0, "Mn": 1.0}
    without_transfer = independent.load_predictor("ts-mtgp").predict(point)
    with_transfer = correlated.load_predictor("ts-mtgp").predict(point)
    assert without_transfer.uncertainty_components is not None and with_transfer.uncertainty_components is not None
    assert (
        with_transfer.uncertainty_components["latent_model_variance"]
        < without_transfer.uncertainty_components["latent_model_variance"]
    )
    assert with_transfer.point_estimate != pytest.approx(without_transfer.point_estimate)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda arrays: arrays.pop("alpha"), "unexpected array schema"),
        (lambda arrays: arrays.update(task_covariance=np.array([[2.0, 1.0], [0.5, 8.0]])), "must be symmetric"),
        (lambda arrays: arrays.update(task_covariance=np.array([[-2.0, 0.0], [0.0, 8.0]])), "diagonal must be positive"),
        (lambda arrays: arrays.update(task_covariance=np.array([[1.0, 2.0], [2.0, 1.0]])), "positive semidefinite"),
        (lambda arrays: arrays.update(train_task=np.array([0, 0, 1, 5], dtype=np.int64)), "out of range"),
        (lambda arrays: arrays.update(train_task=np.array([0.0, 0.0, 1.0, 1.0])), "1-D integer array"),
        (lambda arrays: arrays.update(train_noise=np.array([0.1, 0.0])), "invalid multi-task GP artifact schema"),
        (lambda arrays: arrays.update(precision=np.eye(3)), "invalid multi-task GP artifact schema"),
        (lambda arrays: arrays.update(alpha=np.array([0.1, np.nan, 0.2, 0.3])), "invalid multi-task GP artifact schema"),
    ],
)
def test_adapter_rejects_tampered_artifact_schemas(tmp_path: Path, mutate, message: str) -> None:
    arrays = _icm_arrays(_correlated_task_covariance(0.5))
    mutate(arrays)
    package = ModelPackageLoader().load(_write_multitask_package(tmp_path, arrays))
    with pytest.raises(PackageContractError, match=message):
        package.load_predictor("ts-mtgp")


def test_adapter_requires_in_range_integer_task_index(tmp_path: Path) -> None:
    arrays = _icm_arrays(_correlated_task_covariance(0.5))
    package = ModelPackageLoader().load(
        _write_multitask_package(tmp_path, arrays, predictor_overrides={"config": {"task_index": 7}}, override_ids={"ts-mtgp"})
    )
    with pytest.raises(PackageContractError, match="config.task_index"):
        package.load_predictor("ts-mtgp")
    package = ModelPackageLoader().load(
        _write_multitask_package(tmp_path, arrays, name="floaty", predictor_overrides={"config": {"task_index": 0.5}}, override_ids={"ts-mtgp"})
    )
    with pytest.raises(PackageContractError, match="config.task_index"):
        package.load_predictor("ts-mtgp")


def test_manifest_rejects_duplicate_task_index_for_a_shared_artifact(tmp_path: Path) -> None:
    arrays = _icm_arrays(_correlated_task_covariance(0.5))
    with pytest.raises(PackageContractError, match="unique config.task_index"):
        ModelPackageLoader().load(
            _write_multitask_package(tmp_path, arrays, predictor_overrides={"config": {"task_index": 0}})
        )


def test_manifest_rejects_wrong_architecture_and_family(tmp_path: Path) -> None:
    arrays = _icm_arrays(_correlated_task_covariance(0.5))
    with pytest.raises(PackageContractError, match="invalid model package manifest"):
        ModelPackageLoader().load(
            _write_multitask_package(tmp_path, arrays, predictor_overrides={"architecture_id": "dense_mlp_v1"})
        )
    package = ModelPackageLoader().load(
        _write_multitask_package(tmp_path, arrays, name="family", predictor_overrides={"predictive_family": "student_t"})
    )
    with pytest.raises(PackageContractError, match="requires normal / icm_rbf_v1"):
        package.load_predictor("ts-mtgp")
