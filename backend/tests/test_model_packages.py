from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np
import pytest

from decision_workbench.modeling.model_package_verify import (
    _smoke_outputs_equivalent,
    verify_model_package_example,
)
from decision_workbench.modeling.packages.registry import AdapterRegistry
from decision_workbench.modeling.packages.contracts import (
    PackageContractError,
    PredictiveSummary,
    PredictorSpec,
    ordered_canonical_input_paths,
    validate_predictive_summary,
    validate_task_definition_canonical_inputs,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.contracts.task_contracts import TaskContractFixture
from decision_workbench.contracts.sampling_identity_contracts import SamplingRequest
from decision_workbench.adapters.numpyro_posterior import MAX_NPZ_COMPRESSION_RATIO
from decision_workbench.adapters.sklearn_skops import _TRUSTED_TYPES_BY_FAMILY


def _numpyro_request(
    predictor: object,
    *,
    seed: int,
    sample_count: int | None = None,
) -> SamplingRequest:
    posterior_draw_count = int(getattr(predictor, "draws"))
    if sample_count is None:
        return SamplingRequest.for_package_verification(
            seed=seed, posterior_draw_count=posterior_draw_count
        )
    return SamplingRequest.create(
        operation="detailed_prediction",
        policy_id="test-explicit-sample-budget/v1",
        seed=seed,
        requested_sample_count=sample_count,
    )


def test_stage_c_builder_bootstraps_backend_src_outside_repository(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [
            sys.executable,
            str(
                root
                / "backend"
                / "scripts"
                / "generators"
                / "build_welding_stage_c_model_package.py"
            ),
            "--help",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert "Build the allow-listed Stage C Model Package" in completed.stdout


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


def test_verified_package_consumes_an_immutable_artifact_snapshot(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    package = ModelPackageLoader().load(root)
    relative = "model-artifacts/posterior.npz"
    verified_bytes = package.artifact_path(relative).read_bytes()
    manifest_digest = package.manifest_sha256

    (root / relative).write_bytes(b"replacement artifact")
    (root / "manifest.json").write_text("{}", encoding="utf-8")

    assert package.artifact_path(relative).read_bytes() == verified_bytes
    assert package.manifest_sha256 == manifest_digest
    predictor = package.load_predictor("target")
    assert predictor.predict(
        {"C": 0.1, "Mn": 1.4},
        sampling_request=_numpyro_request(predictor, seed=19),
    )


def test_model_package_rejects_an_excessive_aggregate_artifact_size(tmp_path: Path) -> None:
    root = _write_package(tmp_path)

    with pytest.raises(PackageContractError, match="aggregate byte limit"):
        ModelPackageLoader(max_package_bytes=1).load(root)


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
    request = _numpyro_request(predictor, seed=19)
    first = predictor.predict({"C": 0.1, "Mn": 1.4}, sampling_request=request)
    second = predictor.predict({"C": 0.1, "Mn": 1.4}, sampling_request=request)
    assert first == second
    assert first.target_kind == target_kind
    assert first.quantiles["0.05"] <= first.quantiles["0.50"] <= first.quantiles["0.95"]
    assert first.prediction_interval is not None
    assert first.prediction_interval.method == "bayesian"
    assert first.prediction_interval.coverage_level == pytest.approx(0.9)
    assert first.prediction_interval.lower == first.quantiles["0.05"]
    assert first.prediction_interval.upper == first.quantiles["0.95"]
    assert first.sampling_identity is not None
    assert first.sampling_identity.seed == 19
    assert first.sampling_identity.requested_sample_count == 12
    assert first.sampling_identity.effective_sample_count == 12
    assert first.sampling_identity.posterior_draw_count == 12
    assert first.sampling_identity.draw_selection_policy == "all_posterior_draws"
    assert first.sampling_identity.predictive_resampling_policy == (
        "none-posterior-probability-summary/v1"
        if family == "bernoulli_logit"
        else "numpy-default-rng-likelihood/v1"
    )
    if family == "bernoulli_logit":
        assert 0 <= first.point_estimate <= 1 and first.event_probability == first.point_estimate
    if family in {"lognormal", "poisson_log", "negative_binomial_log", "zero_inflated_poisson_log"}:
        assert first.point_estimate >= 0 and first.quantiles["0.05"] >= 0
    if target_kind in {"count", "ordinal"}:
        assert all(float(value).is_integer() for value in first.quantiles.values())
    if family == "ordinal_logit":
        assert 0 <= first.point_estimate <= 2


@pytest.mark.parametrize(
    ("sample_count", "selection_policy"),
    [
        (6, "seeded_without_replacement"),
        (18, "seeded_with_replacement"),
    ],
)
def test_zero_inflated_poisson_resampling_uses_effective_draw_count(
    tmp_path: Path,
    sample_count: int,
    selection_policy: str,
) -> None:
    package = ModelPackageLoader().load(
        _write_package(
            tmp_path,
            family="zero_inflated_poisson_log",
            target_kind="count",
            output_width=2,
        )
    )
    predictor = package.load_predictor("target")

    summary = predictor.predict(
        {"C": 0.1, "Mn": 1.4},
        sampling_request=_numpyro_request(
            predictor,
            seed=31,
            sample_count=sample_count,
        ),
    )

    assert summary.sampling_identity is not None
    assert summary.sampling_identity.effective_sample_count == sample_count
    assert summary.sampling_identity.draw_selection_policy == selection_policy
    assert all(float(value).is_integer() for value in summary.quantiles.values())


def test_numpyro_sampling_identity_distinguishes_seed_and_sample_budget(
    tmp_path: Path,
) -> None:
    package = ModelPackageLoader().load(_write_package(tmp_path, family="normal"))
    predictor = package.load_predictor("target")
    values = {"C": 0.1, "Mn": 1.4}

    first = predictor.predict(
        values, sampling_request=_numpyro_request(predictor, seed=17, sample_count=6)
    )
    repeated = predictor.predict(
        values, sampling_request=_numpyro_request(predictor, seed=17, sample_count=6)
    )
    other_seed = predictor.predict(
        values, sampling_request=_numpyro_request(predictor, seed=23, sample_count=6)
    )
    other_budget = predictor.predict(
        values, sampling_request=_numpyro_request(predictor, seed=17, sample_count=8)
    )

    assert first == repeated
    assert first.sampling_identity is not None
    assert first.sampling_identity.draw_selection_policy == (
        "seeded_without_replacement"
    )
    assert first.sampling_identity.parameter_digest != (
        other_seed.sampling_identity.parameter_digest
    )
    assert first.sampling_identity.parameter_digest != (
        other_budget.sampling_identity.parameter_digest
    )
    assert first.quantiles != other_seed.quantiles
    assert first.quantiles != other_budget.quantiles


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
    assert set(AdapterRegistry()._adapters) == {
        "builtin.linear.v1", "builtin.exact_gp.v1", "builtin.heteroscedastic_exact_gp.v1",
        "builtin.deterministic_linear.v1",
        "builtin.additive_terms.v1", "builtin.quantile_linear.v1", "builtin.posterior_linear.v1",
        "sklearn.skops.v1", "lightgbm.booster.v1", "gpytorch.static_exact_rbf.v1",
        "numpyro.dense_posterior.v1",
    }


def test_checked_in_individual_observation_packages_expose_distinct_uncertainty_components() -> None:
    root = Path(__file__).resolve().parents[2]
    hetero = ModelPackageLoader().load(
        root / "models" / "packages" / "annealed-heteroscedastic-gp-process-v2"
    )
    hierarchical = ModelPackageLoader().load(
        root / "models" / "packages" / "annealed-hierarchical-bayes-process-v2"
    )
    for package in (hetero, hierarchical):
        spec = package.manifest.predictors[0]
        assert spec.config["training_unit"] == "individual_observation"
        values = {name: 0.0 for name in spec.feature_names}
        summary = package.load_predictor(spec.id).predict(values, seed=7)
        assert summary.quantiles["0.05"] <= summary.quantiles["0.50"] <= summary.quantiles["0.95"]
        assert summary.distribution["std"] > 0
    hetero_summary = hetero.load_predictor(hetero.manifest.predictors[0].id).predict(
        {name: 0.0 for name in hetero.manifest.predictors[0].feature_names}
    )
    hierarchy_summary = hierarchical.load_predictor(hierarchical.manifest.predictors[0].id).predict(
        {name: 0.0 for name in hierarchical.manifest.predictors[0].feature_names}
    )
    assert "input_dependent_observation_variance" in hetero_summary.uncertainty_components
    assert hierarchy_summary.uncertainty_components["between_parent_std"] > 0
    assert hierarchy_summary.uncertainty_components["within_parent_observation_std"] > 0


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


def test_loader_allows_predictor_specific_ordered_feature_subsets(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["predictors"][0]["feature_names"] = ["C"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    package = ModelPackageLoader().load(root)

    assert package.manifest.predictors[0].feature_names == ("C",)


def test_loader_rejects_predictor_features_missing_from_pipeline(tmp_path: Path) -> None:
    root = _write_package(tmp_path)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["predictors"][0]["feature_names"] = ["C", "unknown"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PackageContractError, match="declared by feature pipeline"):
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
        ("annealed-properties-v1", "annealed-gp-stable-ard-tutorial-v2", "2.1.0-stable-ard", "4.0.0"),
        ("hot-rolled-properties-v1", "hot-rolled-tutorial-v2", "1.2.0-feature-design-v3", "3.0.0"),
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
        (root / "backend" / "src" / "decision_workbench" / "tasks" / "task_definitions" / f"{task_id}.json").read_text(
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


def test_superseded_model_package_manifests_remain_byte_immutable() -> None:
    root = Path(__file__).resolve().parents[2] / "models" / "packages"
    expected = {
        "annealed-gp-stable-ard-process-v1": "00c15d389619f9ed440b846235b8474d99cdd16fc5f596197da050be61142da1",
        "annealed-gp-stable-ard-tutorial-v1": "9e0e25859643aaeb7a6f5d7c8bc9ff4438dc04bb2e0eee8121db6e04da7c9b15",
        "annealed-heteroscedastic-gp-process-v1": "e41b2746da65aae54560c4dbff9e36f6cdbe93dd7527576b4ce0f32c14175f59",
        "annealed-hierarchical-bayes-process-v1": "94918dc22dc733742befe6fc986510b463a262bbb5280b84b90ca80488278c2a",
        "annealed-lightgbm-standard-process-v1": "40fef65618cc0f21e0e685374c096f33e1de28d4750f4869858b031343bc5bd0",
        "annealed-lightgbm-standard-tutorial-v1": "ec43b93d9d501824dd469904f51202c576e1f0a1b60f21bba22533ba1dcd1db6",
        "hot-rolled-horseshoe-process-v1": "e3a9ff4bd9b10d050d08a006473a32205e803fc3a571cd7bd449905535c0e885",
        "hot-rolled-tutorial-v1": "35564d807e682c2773125126109b82c918d24aedd46182155981798c96418a0d",
    }
    assert {
        package_id: hashlib.sha256(
            (root / package_id / "manifest.json").read_bytes()
        ).hexdigest()
        for package_id in expected
    } == expected


def test_canonical_input_order_includes_optional_declared_fields() -> None:
    root = Path(__file__).resolve().parents[2]
    fixture = TaskContractFixture.model_validate_json(
        (root / "backend" / "src" / "decision_workbench" / "tasks" / "task_definitions" / "annealed-properties-v1.json").read_text(
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
    source_root = Path(__file__).resolve().parents[1] / "src" / "decision_workbench"
    files = [
        *(source_root / "modeling" / "packages").glob("*.py"),
        *(source_root / "adapters").glob("*.py"),
    ]
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


def test_package_loader_and_verification_do_not_import_concrete_adapters() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "decision_workbench"
    packages = source_root / "modeling" / "packages"
    for name in ("loader.py", "verification.py"):
        tree = ast.parse((packages / name).read_text(encoding="utf-8"))
        imported_modules = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        assert not any(
            module.startswith("decision_workbench.adapters")
            for module in imported_modules
        )


def test_sklearn_trusted_types_are_owned_by_the_application() -> None:
    assert set(_TRUSTED_TYPES_BY_FAMILY) == {
        "linear_regression_v1",
        "ridge_regression_v1",
        "logistic_regression_v1",
        "poisson_regression_v1",
    }
    assert all(all(item.startswith(("sklearn.", "numpy.")) for item in types) for types in _TRUSTED_TYPES_BY_FAMILY.values())


def test_checked_in_numpyro_examples_are_all_loadable() -> None:
    examples = Path(__file__).resolve().parents[2] / "examples" / "model-packages" / "numpyro"
    package_roots = sorted(path.parent for path in examples.glob("*/manifest.json"))
    assert len(package_roots) == 8
    for root in package_roots:
        package = ModelPackageLoader().load(root)
        predictor = package.load_predictor("target")
        result = predictor.predict(
            {"C": 0.08, "Mn": 1.5},
            sampling_request=_numpyro_request(predictor, seed=7),
        )
        report = verify_model_package_example(root)
        assert np.isfinite(result.point_estimate)
        assert result.quantiles["0.05"] <= result.quantiles["0.50"] <= result.quantiles["0.95"]
        assert report.quality_metrics


def test_example_smoke_comparison_rejects_semantic_drift_but_ignores_tail_bits() -> None:
    expected = {"point": 0.3454642902960188, "labels": ["low", "high"]}
    assert _smoke_outputs_equivalent(
        {"point": 0.3454642902960191, "labels": ["low", "high"]},
        expected,
    )
    assert not _smoke_outputs_equivalent(
        {"point": 0.3455, "labels": ["low", "high"]},
        expected,
    )
    assert not _smoke_outputs_equivalent(
        {"point": 0.3454642902960188, "labels": ["high", "low"]},
        expected,
    )
