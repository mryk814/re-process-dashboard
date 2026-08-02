from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic_core import to_jsonable_python

from decision_workbench.contracts.model_capability_contracts import (
    CapabilityRequirement,
    ModelPackageCapabilityMatrix,
    TargetCapabilityMatrix,
)
from decision_workbench.contracts.prediction_catalog_contracts import Prediction
from decision_workbench.application.records import RecordService
from decision_workbench.contracts.task_contracts import (
    RuntimeCapability,
    RuntimeOperationsCapability,
    TargetRuntimeCapability,
)
from decision_workbench.modeling.conformal_intervals import (
    evaluate_split_conformal,
    verify_conformal_wrapper,
)
from decision_workbench.modeling.package_capabilities import resolve_capabilities
from decision_workbench.modeling.packages.contracts import (
    PackageContractError,
    predictive_interval,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader


def _artifact(path: Path, relative_path: str) -> dict[str, object]:
    payload = path.read_bytes()
    return {
        "path": relative_path,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
    }


def _base_package(root: Path):
    (root / "feature-pipeline").mkdir(parents=True)
    (root / "model-artifacts").mkdir()
    pipeline = root / "feature-pipeline" / "pipeline.json"
    pipeline.write_text(json.dumps({
        "id": "fixture-pipeline", "version": "1",
        "canonical_input_paths": ["process.x"],
        "features": [{"name": "x", "unit": "1", "meaning": "fixture", "group": "process"}],
    }), encoding="utf-8")
    artifact = root / "model-artifacts" / "linear.npz"
    training_stats = root / "feature-pipeline" / "training_stats.json"
    training_stats.write_text(json.dumps({"records": {"total": 1}}), encoding="utf-8")
    np.savez(
        artifact,
        weights=np.array([1.0]),
        bias=np.array(0.0),
        lower_offset=np.array(-0.5),
        upper_offset=np.array(0.5),
    )
    manifest = {
        "schema_version": "model-package/v1", "package_id": "ridge-fixture",
        "package_version": "1", "task_id": "fixture-task", "input_schema_version": "candidate-v1",
        "feature_pipeline": {"id": "fixture-pipeline", "version": "1", "spec": "feature-pipeline/pipeline.json", "canonical_input_paths": ["process.x"], "output_features": ["x"], "artifacts": ["feature-pipeline/training_stats.json"]},
        "predictors": [{"id": "point", "target": "y", "unit": "MPa", "target_kind": "continuous", "runtime_type": "builtin.linear.v1", "architecture_id": "profile_transformed_ridge_v1", "artifact": "model-artifacts/linear.npz", "predictive_family": "empirical_quantiles", "feature_names": ["x"], "config": {"training_method": "ridge.v1"}}],
        "provenance": {"training_data_id": "fixture", "feature_dataset_id": "fixture", "training_code_revision": "fixture"},
        "artifacts": [_artifact(pipeline, "feature-pipeline/pipeline.json"), _artifact(training_stats, "feature-pipeline/training_stats.json"), _artifact(artifact, "model-artifacts/linear.npz")],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return ModelPackageLoader().load(root)


def _wrapper(root: Path, package, *, calibration_digest: str = "a" * 64) -> Path:
    root.mkdir()
    scores = root / "calibration-scores.json"
    scores.write_text(json.dumps({"schema_version": "conformal-calibration-scores/v1", "scores": [0.1, 0.2, 0.3, 0.4]}), encoding="utf-8")
    pipeline = package.manifest.feature_pipeline
    assert pipeline is not None
    pipeline_artifact = next(item for item in package.manifest.artifacts if item.path == pipeline.spec)
    manifest = {
        "schema_version": "conformal-wrapper/v1", "wrapper_id": "point-fixture-conformal",
        "wrapper_version": "1", "base_package_id": package.manifest.package_id,
        "base_package_manifest_digest": f"sha256:{package.manifest_sha256}",
        "base_predictor_id": "point",
        "target": "y", "unit": "MPa",
        "feature_pipeline": {"id": pipeline.id, "version": pipeline.version, "spec_sha256": pipeline_artifact.sha256},
        "calibration": {"dataset_view_digest": f"sha256:{calibration_digest}", "training_snapshot_digest": f"sha256:{'b' * 64}", "split_policy_id": "split-conformal/train-calibration/v1", "group_policy_id": "row/v1"},
        "score_id": "absolute_residual/v1", "finite_sample_rule": "ceil_n_plus_1_over_coverage/v1", "alpha": 0.2,
        "calibration_scores": _artifact(scores, "calibration-scores.json"), "calibration_score_count": 4,
        "quality": {"evaluation_dataset_digest": f"sha256:{'c' * 64}", "evaluation_split_policy_id": "held-out-evaluation/v1", "sample_count": 5, "empirical_marginal_coverage": 0.8, "mean_interval_width": 0.8, "group_coverage": {"fixture": 0.8}, "small_calibration_warning": True, "base_point_metric": {"mae": 0.2}},
        "build_code_revision": "fixture",
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_split_conformal_wrapper_is_bound_to_base_identity_and_exposes_explicit_interval(tmp_path: Path) -> None:
    package = _base_package(tmp_path / "base")
    wrapper = verify_conformal_wrapper(_wrapper(tmp_path / "wrapper", package), base_package=package)

    summary = wrapper.load_predictor().predict({"x": 2.0})

    assert summary.prediction_interval is not None
    assert summary.prediction_interval.method == "conformal"
    assert summary.prediction_interval.coverage_level == pytest.approx(0.8)
    assert predictive_interval(summary) == pytest.approx((1.6, 2.4))
    assert summary.prediction_interval.calibration.calibration_sample_count == 4
    assert summary.prediction_interval.conformal_wrapper.wrapper_id == "point-fixture-conformal"
    assert summary.prediction_interval.conformal_wrapper.manifest_digest.startswith("sha256:")
    assert summary.prediction_interval.conformal_wrapper.calibration_score_artifact_digest.startswith("sha256:")
    assert summary.distribution == {
        "family": "empirical_quantiles",
        "support": "real",
    }


def test_conformal_wrapper_enables_only_explicit_interval_capability(tmp_path: Path) -> None:
    package = _base_package(tmp_path / "base")
    wrapper = verify_conformal_wrapper(_wrapper(tmp_path / "wrapper", package), base_package=package)
    matrix = ModelPackageCapabilityMatrix(
        task_id="fixture-task", package_id="ridge-fixture",
        package_manifest_digest=f"sha256:{package.manifest_sha256}",
        targets=(TargetCapabilityMatrix(
            target="y", target_kind="continuous", predictive_family="empirical_quantiles",
            point_statistics=("mean",), quantiles=False, standard_deviation=False,
            predictive_samples=False, parametric_distribution=False,
            uncertainty_components=False, support=True, warnings=True,
            goal_probability="unavailable",
        ),),
    )

    upgraded = wrapper.apply_capability(matrix)

    assert resolve_capabilities(upgraded, target="y", requirements=(CapabilityRequirement(capability="conformal_interval"),)).available
    unavailable = resolve_capabilities(
        matrix, target="y",
        requirements=(CapabilityRequirement(capability="conformal_interval"),),
    )
    assert unavailable.available is False
    assert unavailable.reasons == ("Conformal予測区間に対応するModel Packageが必要です",)
    assert not resolve_capabilities(upgraded, target="y", requirements=(CapabilityRequirement(capability="standard_deviation"),)).available
    assert not resolve_capabilities(upgraded, target="y", requirements=(CapabilityRequirement(capability="goal_probability"),)).available
    assert upgraded.target("y").quantiles is False
    assert upgraded.target("y").parametric_distribution is False
    assert upgraded.capability_layers[0].layer_id == "point-fixture-conformal"
    assert upgraded.capability_layers[0].manifest_digest.startswith("sha256:")


def test_tabular_runtime_injects_verified_wrapper_without_changing_base_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import decision_workbench.modeling.tabular.runtime as runtime_module

    package = _base_package(tmp_path / "base")
    wrapper = verify_conformal_wrapper(_wrapper(tmp_path / "wrapper", package), base_package=package)
    capability = RuntimeCapability(
        schema_version="runtime-capability/v1",
        task_id="fixture-task",
        model_package_schema_version="model-package/v1",
        targets=(TargetRuntimeCapability(
            target="y", point_statistics=("mean",), standard_deviation=False,
            quantiles=False, samples=False, parametric_distribution=False,
            uncertainty_components=False, support=True, warnings=True,
            goal_probability="unavailable",
        ),),
        operations=RuntimeOperationsCapability(
            preview=True, detailed_prediction=True, response_curve=False,
            similarity=False, snapshot=True, actual_measurement=False,
        ),
    )
    output = SimpleNamespace(key="y", lower_bound=None, upper_bound=None)
    monkeypatch.setattr(runtime_module, "load_task_definitions", lambda: {
        "fixture-task": SimpleNamespace(
            outputs=(SimpleNamespace(key="y", goal_direction="at_least"),),
            canonical_candidate_schema_version="canonical-candidate/v1",
            model_dump=lambda **_: {
                "input_groups": [],
                "outputs": [{"key": "y"}],
            },
        ),
    })
    monkeypatch.setattr(runtime_module, "load_task_contracts", lambda: {
        "fixture-task": SimpleNamespace(runtime_capability=capability),
    })
    monkeypatch.setattr(runtime_module, "feature_definitions", lambda profile: (SimpleNamespace(name="x"),))
    monkeypatch.setattr(runtime_module, "validate_task_definition_canonical_inputs", lambda *args: None)
    monkeypatch.setattr(runtime_module.TabularRegressionRuntime, "_verify_smoke", lambda self: None)
    monkeypatch.setattr(runtime_module.TabularRegressionRuntime, "_build_support_reference", lambda self: None)

    runtime = runtime_module.TabularRegressionRuntime(
        SimpleNamespace(
            profile=SimpleNamespace(task_id="fixture-task", outputs=(output,), group_column=None),
            source_path="fixture.csv", source_sha256="source-digest", profile_id="fixture-profile",
        ),
        package,
        conformal_wrappers=(wrapper,),
    )

    assert runtime.model_package.manifest_sha256 == package.manifest_sha256
    assert runtime.predictors["y"].predict({"x": 2.0}).prediction_interval is not None
    assert runtime.capability_matrix.target("y").conformal_interval is True
    candidate = SimpleNamespace(
        id="candidate-1", inputs=SimpleNamespace(composition={}, process={}, categorical={}),
        model_dump=lambda mode: {"id": "candidate-1"},
    )
    result = runtime.predict_core(candidate, _prepared_values={"x": 2.0})
    saved_prediction = result["predictions"]["y"]
    assert saved_prediction.interval_wrapper_id == "point-fixture-conformal"
    assert saved_prediction.interval_calibration_score_artifact_digest.startswith("sha256:")
    assert result["model_meta"]["package"]["manifest_sha256"] == package.manifest_sha256
    assert result["model_meta"]["prediction_interval"]["calibration"]["y"]["wrapper"]["id"] == "point-fixture-conformal"
    assert result["model_meta"]["prediction_interval"]["coverage"] == {"y": 0.8}
    snapshot_payload = to_jsonable_python(RecordService._snapshot_payload(
        SimpleNamespace(design_space_digest="design-space", design_space_binding_provenance="explicit"),
        candidate,
        result,
    ))
    assert snapshot_payload["prediction"]["predictions"]["y"]["interval_wrapper_id"] == "point-fixture-conformal"
    assert snapshot_payload["prediction"]["model_meta"]["prediction_interval"]["coverage"] == {"y": 0.8}

    second_wrapper = verify_conformal_wrapper(
        _wrapper(tmp_path / "second-wrapper", package), base_package=package,
    )
    with pytest.raises(ValueError, match="multiple conformal wrappers"):
        runtime_module.TabularRegressionRuntime(
            SimpleNamespace(
                profile=SimpleNamespace(task_id="fixture-task", outputs=(output,), group_column=None),
                source_path="fixture.csv", source_sha256="source-digest", profile_id="fixture-profile",
            ),
            package,
            conformal_wrappers=(wrapper, second_wrapper),
        )


def test_conformal_wrapper_rejects_base_or_calibration_identity_drift(tmp_path: Path) -> None:
    package = _base_package(tmp_path / "base")
    wrapper_root = _wrapper(tmp_path / "wrapper", package)
    manifest_path = wrapper_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["base_package_manifest_digest"] = f"sha256:{'d' * 64}"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="base package digest mismatch"):
        verify_conformal_wrapper(wrapper_root, base_package=package)

    manifest["base_package_manifest_digest"] = f"sha256:{package.manifest_sha256}"
    manifest["quality"]["evaluation_dataset_digest"] = manifest["calibration"]["dataset_view_digest"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="held-out evaluation dataset"):
        verify_conformal_wrapper(wrapper_root, base_package=package)

    manifest["quality"]["evaluation_dataset_digest"] = f"sha256:{'c' * 64}"
    manifest["base_predictor_id"] = "unrelated"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="predictor/unit"):
        verify_conformal_wrapper(wrapper_root, base_package=package)


def test_conformal_wrapper_rejects_tampered_or_misshaped_score_artifact(tmp_path: Path) -> None:
    package = _base_package(tmp_path / "base")
    wrapper_root = _wrapper(tmp_path / "wrapper", package)
    scores_path = wrapper_root / "calibration-scores.json"
    scores_path.write_text(json.dumps({"schema_version": "conformal-calibration-scores/v1", "scores": [0.1, 0.2]}), encoding="utf-8")
    with pytest.raises(PackageContractError, match="artifact (size|hash) mismatch"):
        verify_conformal_wrapper(wrapper_root, base_package=package)

    manifest_path = wrapper_root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["calibration_scores"] = _artifact(scores_path, "calibration-scores.json")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PackageContractError, match="score shape mismatch"):
        verify_conformal_wrapper(wrapper_root, base_package=package)


def test_held_out_conformal_evaluation_reports_coverage_width_and_groups() -> None:
    metrics = evaluate_split_conformal(
        [0.0, 1.0, 2.0], [0.2, 1.3, 1.5], interval_radius=0.4,
        groups=["A", "A", "B"],
    )

    assert metrics.empirical_marginal_coverage == pytest.approx(2 / 3)
    assert metrics.mean_interval_width == pytest.approx(0.8)
    assert metrics.group_coverage == {"A": 1.0, "B": 0.0}


def test_conformal_prediction_identity_keeps_complete_wrapper_evidence_in_snapshot() -> None:
    prediction = Prediction(
        value=2.0, lower=1.6, upper=2.4, unit="MPa",
        target_kind="continuous", point_statistic="mean",
        predictive_family="empirical_quantiles", quantiles={},
        interval_method="conformal", interval_coverage_level=0.8,
        interval_calibration_dataset_digest=f"sha256:{'a' * 64}",
        interval_calibration_sample_count=4,
        interval_wrapper_id="point-fixture-conformal",
        interval_wrapper_version="1",
        interval_wrapper_manifest_digest=f"sha256:{'b' * 64}",
        interval_calibration_score_artifact_digest=f"sha256:{'c' * 64}",
    )

    assert prediction.interval_method == "conformal"
    assert prediction.interval_calibration_sample_count == 4
    assert prediction.goal_probability is None
    snapshot_payload = RecordService._snapshot_payload(
        SimpleNamespace(design_space_digest="design-space", design_space_binding_provenance="explicit"),
        SimpleNamespace(id="candidate-1", model_dump=lambda mode: {"id": "candidate-1"}),
        {"canonical_input": {}, "predictions": {"y": prediction.model_dump(mode="json")}, "model_meta": {}},
    )
    saved = snapshot_payload["prediction"]["predictions"]["y"]
    assert saved["interval_wrapper_manifest_digest"] == f"sha256:{'b' * 64}"
    assert saved["interval_calibration_score_artifact_digest"] == f"sha256:{'c' * 64}"
    invalid_goal_probability = prediction.model_dump()
    invalid_goal_probability["goal_probability"] = 0.8
    with pytest.raises(ValueError, match="must not manufacture goal probability"):
        Prediction(**invalid_goal_probability)


def test_nonconformal_prediction_rejects_conformal_only_evidence() -> None:
    prediction = Prediction(
        value=2.0, lower=1.6, upper=2.4, unit="MPa",
        target_kind="continuous", point_statistic="mean",
        predictive_family="normal", quantiles={"0.05": 1.6, "0.95": 2.4},
        interval_method="parametric", interval_coverage_level=0.9,
    )

    assert prediction.interval_wrapper_id is None
    assert prediction.interval_calibration_dataset_digest is None
    invalid = prediction.model_dump()
    invalid["interval_wrapper_id"] = "wrong-on-nonconformal"
    with pytest.raises(ValueError, match="only conformal intervals"):
        Prediction(**invalid)


def test_interval_method_uses_only_declared_semantics() -> None:
    from decision_workbench.modeling.packages.contracts import (
        PredictionInterval,
        PredictiveSummary,
        PredictorSpec,
        prediction_interval_semantics,
    )

    normal = PredictiveSummary(
        target="y", target_kind="continuous", unit="MPa", point_statistic="mean",
        point_estimate=2.0, quantiles={"0.05": 1.6, "0.95": 2.4},
        distribution={"family": "normal", "support": "real"},
    )
    posterior_normal = normal.model_copy(update={"distribution": {
        "family": "normal",
        "support": "real",
        "approximation": "posterior_predictive_moment_matched",
    }})
    declared_bayesian = normal.model_copy(update={"prediction_interval": PredictionInterval(
        method="bayesian", coverage_level=0.9, lower=1.6, upper=2.4,
    )})
    residual_quantile_spec = PredictorSpec(
        id="linear", target="y", unit="MPa", target_kind="continuous",
        runtime_type="builtin.linear.v1", artifact="model.npz",
        predictive_family="empirical_quantiles", feature_names=("x",),
    )
    gp_spec = residual_quantile_spec.model_copy(update={
        "id": "gp",
        "runtime_type": "builtin.exact_gp.v1",
        "predictive_family": "normal",
    })

    assert prediction_interval_semantics(normal) == (None, None)
    assert prediction_interval_semantics(posterior_normal) == (None, None)
    assert prediction_interval_semantics(normal, residual_quantile_spec) == ("quantile", 0.9)
    assert prediction_interval_semantics(normal, gp_spec) == ("bayesian", 0.9)
    assert prediction_interval_semantics(declared_bayesian) == ("bayesian", 0.9)
