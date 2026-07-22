from __future__ import annotations

import ast
import hashlib
import json
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest

from material_workbench.model_package_verify import verify_model_package_example
from material_workbench.model_packages import (
    AdapterRegistry,
    ModelPackageLoader,
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
    ordered_canonical_input_paths,
    validate_predictive_summary,
    validate_task_definition_canonical_inputs,
)
from material_workbench.task_contracts import TaskContractFixture
from material_workbench.adapters.numpyro_posterior import MAX_NPZ_COMPRESSION_RATIO
from material_workbench.adapters.sklearn_skops import _TRUSTED_TYPES_BY_FAMILY


def _artifact(path: Path, relative: str) -> dict[str, object]:
    return {"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}


def _npy_bytes(values: np.ndarray) -> bytes:
    output = BytesIO()
    np.save(output, values, allow_pickle=False)
    return output.getvalue()


def _write_package(tmp_path: Path, *, family: str = "student_t", target_kind: str = "continuous", output_width: int = 1) -> Path:
    root = tmp_path / family
    (root / "feature-pipeline").mkdir(parents=True)
    (root / "model-artifacts").mkdir()
    pipeline = root / "feature-pipeline" / "pipeline.json"
    pipeline.write_text(json.dumps({
        "id": "test-pipeline",
        "version": "1",
        "canonical_input_paths": ["composition.C", "composition.Mn"],
        "features": [
            {"name": "C", "unit": "mass%", "meaning": "C composition", "group": "composition"},
            {"name": "Mn", "unit": "mass%", "meaning": "Mn composition", "group": "composition"},
        ],
    }), encoding="utf-8")
    draws = 12
    weights = np.zeros((draws, 2, output_width), dtype=float)
    weights[:, 0, 0] = np.linspace(0.1, 0.4, draws)
    biases = np.linspace(-0.2, 0.2, draws).reshape(draws, 1)
    if output_width == 2:
        biases = np.column_stack([biases[:, 0], np.linspace(-1, 1, draws)])
    arrays: dict[str, np.ndarray] = {"w0": weights, "b0": biases}
    if family in {"normal", "student_t", "lognormal"}:
        arrays["obs_scale"] = np.full(draws, 0.3)
    if family == "student_t":
        arrays["df"] = np.full(draws, 6.0)
    if family == "negative_binomial_log":
        arrays["dispersion"] = np.full(draws, 4.0)
    model = root / "model-artifacts" / "posterior.npz"
    np.savez(model, **arrays)
    config: dict[str, object] = {"activation": "tanh"}
    if family == "ordinal_logit":
        config["thresholds"] = [-0.5, 0.7]
        config["categories"] = ["low", "middle", "high"]
    manifest = {
        "schema_version": "model-package/v1", "package_id": f"fixture-{family}", "package_version": "1", "task_id": "test", "input_schema_version": "candidate-v1",
        "feature_pipeline": {"id": "test-pipeline", "version": "1", "spec": "feature-pipeline/pipeline.json", "canonical_input_paths": ["composition.C", "composition.Mn"], "output_features": ["C", "Mn"], "artifacts": []},
        "predictors": [{"id": "target", "target": "target", "unit": "u", "target_kind": target_kind, "runtime_type": "numpyro.dense_posterior.v1", "architecture_id": "dense_mlp_v1", "artifact": "model-artifacts/posterior.npz", "predictive_family": family, "feature_names": ["C", "Mn"], "config": config}],
        "provenance": {"training_data_id": "sha256:training", "feature_dataset_id": "sha256:features", "training_code_revision": "git:test"},
        "artifacts": [_artifact(pipeline, "feature-pipeline/pipeline.json"), _artifact(model, "model-artifacts/posterior.npz")],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("family", "target_kind", "output_width"),
    [
        ("normal", "continuous", 1), ("student_t", "continuous", 1), ("lognormal", "continuous_positive", 1),
        ("bernoulli_logit", "binary", 1), ("poisson_log", "count", 1), ("negative_binomial_log", "count", 1),
        ("zero_inflated_poisson_log", "count", 2), ("ordinal_logit", "ordinal", 1),
    ],
)
def test_numpyro_dense_posterior_likelihoods_are_deterministic_and_semantic(tmp_path: Path, family: str, target_kind: str, output_width: int) -> None:
    package = ModelPackageLoader().load(_write_package(tmp_path, family=family, target_kind=target_kind, output_width=output_width))
    predictor = package.load_predictor("target")
    first = predictor.predict({"C": 0.1, "Mn": 1.4}, seed=19)
    second = predictor.predict({"C": 0.1, "Mn": 1.4}, seed=19)
    assert first == second
    assert first.target_kind == target_kind
    assert first.quantiles["0.05"] <= first.quantiles["0.50"] <= first.quantiles["0.95"]
    if family == "bernoulli_logit":
        assert 0 <= first.point_estimate <= 1 and first.event_probability == first.point_estimate
    if family in {"lognormal", "poisson_log", "negative_binomial_log", "zero_inflated_poisson_log"}:
        assert first.point_estimate >= 0 and first.quantiles["0.05"] >= 0
    if target_kind in {"count", "ordinal"}:
        assert all(float(value).is_integer() for value in first.quantiles.values())
    if family == "ordinal_logit":
        assert 0 <= first.point_estimate <= 2


@pytest.mark.parametrize(
    ("spec", "summary", "message"),
    [
        (
            PredictorSpec(id="p", target="y", unit="1", target_kind="binary", runtime_type="numpyro.dense_posterior.v1", architecture_id="dense_mlp_v1", artifact="m.npz", predictive_family="bernoulli_logit", feature_names=("x",)),
            PredictiveSummary(target="y", target_kind="binary", unit="1", point_statistic="probability", point_estimate=0.4, event_probability=0.6, quantiles={"0.05": 0.2, "0.95": 0.8}, distribution={"family": "bernoulli_logit", "support": "{0,1}"}),
            "binary probability semantics",
        ),
        (
            PredictorSpec(id="p", target="y", unit="1", target_kind="count", runtime_type="numpyro.dense_posterior.v1", architecture_id="dense_mlp_v1", artifact="m.npz", predictive_family="poisson_log", feature_names=("x",)),
            PredictiveSummary(target="y", target_kind="count", unit="1", point_statistic="rate", point_estimate=-0.1, quantiles={"0.05": 0.0, "0.95": 2.0}, distribution={"family": "poisson_log", "support": "nonnegative_integers"}),
            "nonnegative support",
        ),
        (
            PredictorSpec(id="p", target="y", unit="1", target_kind="count", runtime_type="numpyro.dense_posterior.v1", architecture_id="dense_mlp_v1", artifact="m.npz", predictive_family="poisson_log", feature_names=("x",)),
            PredictiveSummary(target="y", target_kind="count", unit="1", point_statistic="rate", point_estimate=1.2, quantiles={"0.05": 0.0, "0.95": 2.5}, distribution={"family": "poisson_log", "support": "nonnegative_integers"}),
            "non-discrete quantiles",
        ),
        (
            PredictorSpec(id="p", target="y", unit="MPa", target_kind="ordinal", runtime_type="numpyro.dense_posterior.v1", architecture_id="dense_mlp_v1", artifact="m.npz", predictive_family="ordinal_logit", feature_names=("x",)),
            PredictiveSummary(target="y", target_kind="ordinal", unit="MPa", point_statistic="expected_category", point_estimate=1.2, quantiles={"0.05": 0.0, "0.95": 2.0}, distribution={"family": "ordinal_logit", "support": "ordered_categories", "categories": ["a", "b", "c"]}),
            "dimensionless unit",
        ),
    ],
)
def test_predictive_summary_rejects_invalid_noncontinuous_semantics(spec: PredictorSpec, summary: PredictiveSummary, message: str) -> None:
    with pytest.raises(PackageContractError, match=message):
        validate_predictive_summary(summary, spec)


def test_numpyro_adapter_rejects_invalid_scale_and_ordinal_metadata_at_load(tmp_path: Path) -> None:
    normal_root = _write_package(tmp_path / "normal", family="normal")
    artifact = normal_root / "model-artifacts" / "posterior.npz"
    with np.load(artifact, allow_pickle=False) as current:
        arrays = {name: current[name] for name in current.files}
    arrays["obs_scale"] = np.full(12, -0.1)
    np.savez(artifact, **arrays)
    manifest_path = normal_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][1] = _artifact(artifact, "model-artifacts/posterior.npz")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="obs_scale must be positive"):
        ModelPackageLoader().load(normal_root).load_predictor("target")

    ordinal_root = _write_package(tmp_path / "ordinal", family="ordinal_logit", target_kind="ordinal")
    ordinal_manifest_path = ordinal_root / "manifest.json"
    ordinal_manifest = json.loads(ordinal_manifest_path.read_text(encoding="utf-8"))
    ordinal_manifest["predictors"][0]["config"]["categories"] = ["too", "short"]
    ordinal_manifest_path.write_text(json.dumps(ordinal_manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="category metadata"):
        ModelPackageLoader().load(ordinal_root).load_predictor("target")


def test_builtin_linear_package_and_registry_are_dependency_free(tmp_path: Path) -> None:
    root = tmp_path / "linear"
    (root / "feature-pipeline").mkdir(parents=True)
    (root / "model-artifacts").mkdir()
    pipeline = root / "feature-pipeline" / "pipeline.json"
    pipeline.write_text(json.dumps({
        "id": "p",
        "version": "1",
        "canonical_input_paths": ["composition.C", "composition.Mn"],
        "features": [
            {"name": "C", "unit": "mass%", "meaning": "C composition", "group": "composition"},
            {"name": "Mn", "unit": "mass%", "meaning": "Mn composition", "group": "composition"},
        ],
    }), encoding="utf-8")
    artifact = root / "model-artifacts" / "linear.npz"
    np.savez(artifact, weights=np.array([2.0, 3.0]), bias=np.array(1.0), lower_offset=np.array(-2.0), upper_offset=np.array(4.0))
    manifest = {
        "schema_version": "model-package/v1", "package_id": "linear", "package_version": "1", "task_id": "test", "input_schema_version": "candidate-v1",
        "feature_pipeline": {"id": "p", "version": "1", "spec": "feature-pipeline/pipeline.json", "canonical_input_paths": ["composition.C", "composition.Mn"], "output_features": ["C", "Mn"]},
        "predictors": [{"id": "linear", "target": "TS", "unit": "MPa", "target_kind": "continuous", "runtime_type": "builtin.linear.v1", "artifact": "model-artifacts/linear.npz", "predictive_family": "empirical_quantiles", "feature_names": ["C", "Mn"]}],
        "provenance": {"training_data_id": "x", "feature_dataset_id": "y", "training_code_revision": "z"},
        "artifacts": [_artifact(pipeline, "feature-pipeline/pipeline.json"), _artifact(artifact, "model-artifacts/linear.npz")],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    package = ModelPackageLoader().load(root)
    result = package.load_predictor("linear").predict({"C": 0.1, "Mn": 1.5})
    assert result.point_estimate == pytest.approx(5.7)
    assert set(AdapterRegistry()._adapters) == {"builtin.linear.v1", "builtin.exact_gp.v1", "builtin.additive_terms.v1", "builtin.quantile_linear.v1", "builtin.posterior_linear.v1", "sklearn.skops.v1", "lightgbm.booster.v1", "gpytorch.static_exact_rbf.v1", "numpyro.dense_posterior.v1"}


def test_loader_rejects_hash_tampering_traversal_and_unknown_manifest_fields(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["unexpected"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="invalid model package manifest"):
        ModelPackageLoader().load(root)
    manifest.pop("unexpected")
    manifest["artifacts"][0]["path"] = "../escape.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="invalid model package manifest"):
        ModelPackageLoader().load(root)
    manifest["artifacts"][0]["path"] = "feature-pipeline/pipeline.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pipeline = root / "feature-pipeline" / "pipeline.json"
    pipeline.write_text('{"changed": true}', encoding="utf-8")
    with pytest.raises(PackageContractError, match="artifact size mismatch|artifact hash mismatch"):
        ModelPackageLoader().load(root)


def test_loader_requires_unique_canonical_input_paths_in_manifest(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["feature_pipeline"].pop("canonical_input_paths")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="canonical_input_paths"):
        ModelPackageLoader().load(root)

    manifest["feature_pipeline"]["canonical_input_paths"] = ["composition.C", "composition.C"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="canonical input paths must be unique"):
        ModelPackageLoader().load(root)


def test_loader_rejects_pipeline_and_manifest_canonical_input_mismatch(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    pipeline_path = root / "feature-pipeline" / "pipeline.json"
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    pipeline["canonical_input_paths"] = ["composition.Mn", "composition.C"]
    pipeline_path.write_text(json.dumps(pipeline), encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0] = _artifact(pipeline_path, "feature-pipeline/pipeline.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PackageContractError, match="canonical input paths differ"):
        ModelPackageLoader().load(root)


def test_loader_validates_pipeline_outputs_separately_from_canonical_inputs(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    pipeline_path = root / "feature-pipeline" / "pipeline.json"
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    pipeline["features"] = list(reversed(pipeline["features"]))
    pipeline_path.write_text(json.dumps(pipeline), encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0] = _artifact(pipeline_path, "feature-pipeline/pipeline.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PackageContractError, match="pipeline output feature order differs"):
        ModelPackageLoader().load(root)


def test_loader_validates_predictor_feature_order_separately_from_pipeline_inputs(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["predictors"][0]["feature_names"] = ["Mn", "C"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PackageContractError, match="predictor feature order"):
        ModelPackageLoader().load(root)


def test_loader_rejects_unknown_pipeline_document_fields(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    pipeline_path = root / "feature-pipeline" / "pipeline.json"
    pipeline = json.loads(pipeline_path.read_text(encoding="utf-8"))
    pipeline["arbitrary_code"] = "do-not-ignore"
    pipeline_path.write_text(json.dumps(pipeline), encoding="utf-8")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"][0] = _artifact(pipeline_path, "feature-pipeline/pipeline.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PackageContractError, match="invalid feature pipeline specification"):
        ModelPackageLoader().load(root)


@pytest.mark.parametrize(
    ("task_id", "package_id", "package_version", "pipeline_version"),
    [
        ("annealed-properties-v1", "annealed-gp-2026-07", "0.9.0-input-contract-v2", "2.0.0"),
        ("hot-rolled-properties-v1", "hot-rolled-horseshoe-2026-07", "1.0.0", "2.0.0"),
    ],
)
def test_checked_in_packages_match_task_definition_canonical_input_order(
    task_id: str,
    package_id: str,
    package_version: str,
    pipeline_version: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = TaskContractFixture.model_validate_json(
        (root / "backend" / "src" / "material_workbench" / "task_definitions" / f"{task_id}.json").read_text(
            encoding="utf-8"
        )
    )
    package = ModelPackageLoader().load(root / "models" / "packages" / package_id)

    validate_task_definition_canonical_inputs(fixture.task_definition, package.manifest)
    assert package.manifest.package_version == package_version
    assert package.manifest.feature_pipeline.version == pipeline_version
    assert package.manifest.feature_pipeline.canonical_input_paths == (
        ordered_canonical_input_paths(fixture.task_definition)
    )


def test_canonical_input_order_includes_optional_declared_fields() -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = TaskContractFixture.model_validate_json(
        (root / "backend" / "src" / "material_workbench" / "task_definitions" / "annealed-properties-v1.json").read_text(
            encoding="utf-8"
        )
    )
    document = fixture.model_dump(mode="json")
    optional_path = document["task_definition"]["input_groups"][0]["fields"][0]["path"]
    document["task_definition"]["input_groups"][0]["fields"][0]["required"] = False
    optional_fixture = TaskContractFixture.model_validate(document)

    assert optional_path in ordered_canonical_input_paths(optional_fixture.task_definition)


def test_numpyro_posterior_rejects_archive_bombs_and_excessive_draws(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    artifact = root / "model-artifacts" / "posterior.npz"
    with ZipFile(artifact, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("w0.npy", _npy_bytes(np.zeros((12, 2, 1))))
        archive.writestr("b0.npy", _npy_bytes(np.zeros((12, 1))))
        archive.writestr("padding.npy", b"0" * (MAX_NPZ_COMPRESSION_RATIO + 1) * 1024)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"][1] = _artifact(artifact, "model-artifacts/posterior.npz")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    package = ModelPackageLoader().load(root)
    with pytest.raises(PackageContractError, match="compression ratio"):
        package.load_predictor("target")


def test_numpyro_posterior_rejects_excessive_draws(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    artifact = root / "model-artifacts" / "posterior.npz"
    np.savez(artifact, w0=np.zeros((4097, 2, 1)), b0=np.zeros((4097, 1)), obs_scale=np.ones(4097), df=np.full(4097, 6.0))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["artifacts"][1] = _artifact(artifact, "model-artifacts/posterior.npz")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    package = ModelPackageLoader().load(root)
    with pytest.raises(PackageContractError, match="draw count"):
        package.load_predictor("target")


def test_model_package_runtime_has_no_dynamic_execution_or_unsafe_deserialization() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "material_workbench"
    files = [source_root / "model_packages.py", *(source_root / "adapters").glob("*.py")]
    banned_modules = {"pickle", "joblib", "importlib"}
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not {alias.name.split(".")[0] for alias in node.names} & banned_modules
            if isinstance(node, ast.ImportFrom):
                assert node.module is None or node.module.split(".")[0] not in banned_modules
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in {"exec", "eval", "__import__"}


def test_sklearn_trusted_types_are_owned_by_the_application() -> None:
    assert set(_TRUSTED_TYPES_BY_FAMILY) == {"linear_regression_v1", "ridge_regression_v1"}
    assert all(all(item.startswith(("sklearn.", "numpy.")) for item in types) for types in _TRUSTED_TYPES_BY_FAMILY.values())


def test_checked_in_numpyro_examples_are_all_loadable() -> None:
    examples = Path(__file__).resolve().parents[2] / "examples" / "model-packages" / "numpyro"
    package_roots = sorted(path.parent for path in examples.glob("*/manifest.json"))
    assert len(package_roots) == 8
    for root in package_roots:
        package = ModelPackageLoader().load(root)
        result = package.load_predictor("target").predict({"C": 0.08, "Mn": 1.5}, seed=7)
        report = verify_model_package_example(root)
        assert np.isfinite(result.point_estimate)
        assert result.quantiles["0.05"] <= result.quantiles["0.50"] <= result.quantiles["0.95"]
        assert report.quality_metrics
