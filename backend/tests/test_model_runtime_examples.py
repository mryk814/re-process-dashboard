from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pytest

from material_workbench.model_package_verify import verify_model_package_example
from material_workbench.model_example_contracts import PredictiveMixtureDesignFixture, validate_mixture_component_digests
from material_workbench.model_packages import ModelPackageLoader, PackageContractError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import build_quantile_model_example as quantile_builder  # noqa: E402
import build_additive_model_examples as additive_builder  # noqa: E402


def _replace_model_artifact(root: Path, **arrays: np.ndarray) -> None:
    path = root / "model-artifacts" / "quantiles.npz"
    np.savez(path, **arrays)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["artifacts"] if item["path"] == "model-artifacts/quantiles.npz")
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    entry["bytes"] = path.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_quantile_example_builds_and_verifies_without_activation(tmp_path: Path) -> None:
    destination = tmp_path / "quantile"
    quantile_builder.build(destination)

    report = verify_model_package_example(destination)
    summary = ModelPackageLoader().load(destination).load_predictor("target").predict({"x": 0.4, "scale": 1.2}, seed=7)

    assert report.package_id == "quantile-linear-example"
    assert summary.point_statistic == "median"
    assert summary.point_estimate == pytest.approx(1.84)
    assert summary.quantiles == pytest.approx({"0.05": 0.64, "0.5": 1.84, "0.95": 3.04})
    assert summary.distribution == {"family": "empirical_quantiles", "support": "runtime_defined"}
    assert "quantile-linear-example" not in (ROOT / "models" / "active-packages.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("arrays", "message"),
    [
        ({"quantile_levels": np.array([0.05, 0.5, 0.95]), "coefficients": np.zeros((3, 2))}, "unexpected tensor schema"),
        ({"quantile_levels": np.array([0.05, 0.5, 1.2]), "coefficients": np.zeros((3, 2)), "intercepts": np.zeros(3)}, r"inside \(0, 1\)"),
        ({"quantile_levels": np.array([0.05, 0.5, 0.95]), "coefficients": np.zeros((3, 3)), "intercepts": np.zeros(3)}, "incompatible shapes"),
        ({"quantile_levels": np.array([0.05, 0.5, 0.95]), "coefficients": np.array([[0.0, 0.0], [np.nan, 0.0], [0.0, 0.0]]), "intercepts": np.zeros(3)}, "finite"),
    ],
)
def test_quantile_adapter_rejects_malformed_artifacts(tmp_path: Path, arrays: dict[str, np.ndarray], message: str) -> None:
    destination = tmp_path / "quantile"
    quantile_builder.build(destination)
    _replace_model_artifact(destination, **arrays)
    package = ModelPackageLoader().load(destination)

    with pytest.raises(PackageContractError, match=message):
        package.load_predictor("target")


def test_quantile_adapter_rejects_crossing_instead_of_sorting_it(tmp_path: Path) -> None:
    destination = tmp_path / "quantile"
    quantile_builder.build(destination)
    _replace_model_artifact(
        destination,
        quantile_levels=np.array([0.05, 0.5, 0.95]),
        coefficients=np.array([[3.0, 0.0], [2.0, 0.0], [1.0, 0.0]]),
        intercepts=np.array([0.0, 1.0, 2.0]),
    )
    predictor = ModelPackageLoader().load(destination).load_predictor("target")

    with pytest.raises(PackageContractError, match="cross"):
        predictor.predict({"x": 2.0, "scale": 0.0})


def test_additive_examples_verify_explain_and_keep_capability_difference(tmp_path: Path) -> None:
    destination = tmp_path / "additive"
    additive_builder.build(destination)
    point_package = ModelPackageLoader().load(destination / "point")
    normal_package = ModelPackageLoader().load(destination / "normal")
    features = {"x": 0.35, "route_code": 1.0, "z": 0.2}
    point_predictor = point_package.load_predictor("target")
    normal_predictor = normal_package.load_predictor("target")

    point = point_predictor.predict(features)
    normal = normal_predictor.predict(features)
    explanation = point_predictor.explain(features)

    assert verify_model_package_example(destination / "point").quality_metrics["explanation_reconstruction_error"] == pytest.approx(0)
    assert verify_model_package_example(destination / "normal").quality_metrics["explanation_reconstruction_error"] == pytest.approx(0)
    assert point.quantiles == {} and point.distribution["family"] == "empirical_quantiles"
    assert normal.quantiles["0.05"] < normal.point_estimate < normal.quantiles["0.95"]
    assert normal.distribution["std"] > 0
    assert explanation.intercept + sum(term.contribution for term in explanation.terms) == pytest.approx(explanation.link_score)
    assert explanation.prediction == pytest.approx(point.point_estimate)
    assert {term.kind for term in explanation.terms} == {"linear", "bspline_univariate", "categorical_lookup"}


def test_additive_response_curve_runs_from_canonical_input_through_declared_feature_order(tmp_path: Path) -> None:
    destination = tmp_path / "additive"
    additive_builder.build(destination)
    package = ModelPackageLoader().load(destination / "point")
    predictor = package.load_predictor("target")
    canonical = {"composition.x": 0.0, "categorical.route_code": 1.0, "process.z": 0.2}
    paths = package.manifest.feature_pipeline.canonical_input_paths
    names = package.manifest.feature_pipeline.output_features
    curve = []
    for value in np.linspace(0, 1, 9):
        canonical["composition.x"] = float(value)
        feature_bundle = {name: canonical[path] for path, name in zip(paths, names)}
        curve.append(predictor.predict(feature_bundle).point_estimate)

    assert max(curve) - min(curve) > 0.5
    assert all(np.isfinite(curve))


def test_additive_adapter_rejects_unknown_terms_shapes_nonfinite_and_categories(tmp_path: Path) -> None:
    destination = tmp_path / "additive"
    additive_builder.build(destination)
    root = destination / "point"
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["predictors"][0]["config"]["terms"][0]["kind"] = "python_callback"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="identity, kind, or feature index"):
        ModelPackageLoader().load(root).load_predictor("target")

    additive_builder.build(destination, replace=True)
    root = destination / "point"
    artifact = root / "model-artifacts" / "additive.npz"
    with np.load(artifact, allow_pickle=False) as current:
        arrays = {name: current[name] for name in current.files}
    arrays["term_0_coefficients"] = np.asarray([np.nan])
    np.savez(artifact, **arrays)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["artifacts"] if item["path"] == "model-artifacts/additive.npz")
    entry.update(sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), bytes=artifact.stat().st_size)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="finite"):
        ModelPackageLoader().load(root).load_predictor("target")

    additive_builder.build(destination, replace=True)
    root = destination / "point"
    artifact = root / "model-artifacts" / "additive.npz"
    with np.load(artifact, allow_pickle=False) as current:
        arrays = {name: current[name] for name in current.files}
    arrays["term_0_coefficients"] = np.asarray([1.0, 2.0])
    np.savez(artifact, **arrays)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["artifacts"] if item["path"] == "model-artifacts/additive.npz")
    entry.update(sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), bytes=artifact.stat().st_size)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="incompatible shapes"):
        ModelPackageLoader().load(root).load_predictor("target")

    additive_builder.build(destination, replace=True)
    predictor = ModelPackageLoader().load(destination / "point").load_predictor("target")
    with pytest.raises(PackageContractError, match="unknown category"):
        predictor.predict({"x": 0.3, "route_code": 99.0, "z": 0.2})


def test_checked_posterior_linear_example_is_deterministic_and_reported() -> None:
    root = ROOT / "examples" / "model-packages" / "posterior-linear"
    report = verify_model_package_example(root)
    package = ModelPackageLoader().load(root)
    predictor = package.load_predictor("target")
    features = json.loads((root / "smoke" / "input.json").read_text(encoding="utf-8"))["features"]
    first = predictor.predict(features, seed=17)
    second = predictor.predict(features, seed=17)
    selection = json.loads((root / "reports" / "selection-report.json").read_text(encoding="utf-8"))

    assert first == second
    assert first.quantiles["0.05"] < first.point_estimate < first.quantiles["0.95"]
    assert first.distribution["family"] == "empirical_quantiles"
    assert first.distribution["std_semantics"] == "posterior_predictive_samples"
    assert first.uncertainty_components["epistemic_std"] >= 0
    assert first.uncertainty_components["aleatoric_std"] > 0
    assert report.quality_metrics["posterior_draw_count"] == 192
    assert len(selection["features"]) == 8
    assert "causal" in selection["interpretation_warning"]
    assert "posterior-linear-sparse-example" not in (ROOT / "models" / "active-packages.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("nonfinite", "finite"),
        ("negative_noise", "noise scale"),
        ("shape", "shape or count"),
    ],
)
def test_posterior_linear_rejects_invalid_draw_artifacts(tmp_path: Path, mutation: str, message: str) -> None:
    source = ROOT / "examples" / "model-packages" / "posterior-linear"
    root = tmp_path / "posterior-linear"
    shutil.copytree(source, root)
    artifact = root / "model-artifacts" / "posterior-linear.npz"
    with np.load(artifact, allow_pickle=False) as current:
        arrays = {name: current[name] for name in current.files}
    if mutation == "nonfinite":
        arrays["beta_draws"][0, 0] = np.nan
    elif mutation == "negative_noise":
        arrays["noise_scale_draws"][0] = -0.1
    else:
        arrays["beta_draws"] = arrays["beta_draws"][:, :-1]
    np.savez(artifact, **arrays)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["artifacts"] if item["path"] == "model-artifacts/posterior-linear.npz")
    entry.update(sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(), bytes=artifact.stat().st_size)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PackageContractError, match=message):
        ModelPackageLoader().load(root).load_predictor("target")


def test_posterior_linear_rejects_feature_order_mismatch(tmp_path: Path) -> None:
    source = ROOT / "examples" / "model-packages" / "posterior-linear"
    root = tmp_path / "posterior-linear"
    shutil.copytree(source, root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["predictors"][0]["feature_names"] = list(reversed(manifest["predictors"][0]["feature_names"]))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PackageContractError, match="feature order"):
        ModelPackageLoader().load(root)


def test_predictive_mixture_design_golden_degeneracy_and_digest_binding() -> None:
    path = ROOT / "examples" / "model-packages" / "design" / "predictive-mixture-v1.json"
    fixture = PredictiveMixtureDesignFixture.model_validate_json(path.read_text(encoding="utf-8"))
    actual = {item.predictor_id: item.package_digest for item in fixture.components}

    validate_mixture_component_digests(fixture, actual)
    assert fixture.golden_mixture_mean == pytest.approx(13.5)

    document = fixture.model_dump(mode="json")
    document["components"][0]["weight"] = 1.0
    document["components"][1]["weight"] = 0.0
    document["golden_mixture_mean"] = 10.0
    degenerate = PredictiveMixtureDesignFixture.model_validate(document)
    assert degenerate.golden_mixture_mean == degenerate.golden_component_means["model_a"]

    with pytest.raises(ValueError, match="digest mismatch"):
        validate_mixture_component_digests(fixture, {**actual, "model_b": "sha256:" + "d" * 64})
