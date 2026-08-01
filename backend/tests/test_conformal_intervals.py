from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from decision_workbench.contracts.model_capability_contracts import (
    CapabilityRequirement,
    ModelPackageCapabilityMatrix,
    TargetCapabilityMatrix,
)
from decision_workbench.modeling.conformal_intervals import (
    evaluate_split_conformal,
    verify_conformal_wrapper,
)
from decision_workbench.modeling.package_capabilities import resolve_capabilities
from decision_workbench.modeling.packages.contracts import (
    PackageContractError,
    PredictiveSummary,
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
    np.savez(artifact, weights=np.array([1.0]), bias=np.array(0.0))
    manifest = {
        "schema_version": "model-package/v1", "package_id": "point-fixture",
        "package_version": "1", "task_id": "fixture-task", "input_schema_version": "candidate-v1",
        "feature_pipeline": {"id": "fixture-pipeline", "version": "1", "spec": "feature-pipeline/pipeline.json", "canonical_input_paths": ["process.x"], "output_features": ["x"]},
        "predictors": [{"id": "point", "target": "y", "unit": "MPa", "target_kind": "continuous", "runtime_type": "builtin.linear.v1", "artifact": "model-artifacts/linear.npz", "predictive_family": "empirical_quantiles", "feature_names": ["x"]}],
        "provenance": {"training_data_id": "fixture", "feature_dataset_id": "fixture", "training_code_revision": "fixture"},
        "artifacts": [_artifact(pipeline, "feature-pipeline/pipeline.json"), _artifact(artifact, "model-artifacts/linear.npz")],
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


class _PointPredictor:
    def predict(self, values: dict[str, float], *, seed: int = 0) -> PredictiveSummary:
        return PredictiveSummary(
            target="y", target_kind="continuous", unit="MPa", point_statistic="mean",
            point_estimate=2.0, quantiles={"0.05": 1.0, "0.95": 3.0},
            distribution={"family": "empirical_quantiles"},
        )


def test_split_conformal_wrapper_is_bound_to_base_identity_and_exposes_explicit_interval(tmp_path: Path) -> None:
    package = _base_package(tmp_path / "base")
    wrapper = verify_conformal_wrapper(_wrapper(tmp_path / "wrapper", package), base_package=package)

    summary = wrapper.wrap(_PointPredictor()).predict({"x": 2.0})

    assert summary.prediction_interval is not None
    assert summary.prediction_interval.method == "conformal"
    assert summary.prediction_interval.coverage_level == pytest.approx(0.8)
    assert predictive_interval(summary) == pytest.approx((1.6, 2.4))
    assert summary.prediction_interval.calibration.calibration_sample_count == 4
    assert summary.distribution == {"family": "empirical_quantiles"}


def test_conformal_wrapper_enables_only_explicit_interval_capability(tmp_path: Path) -> None:
    package = _base_package(tmp_path / "base")
    wrapper = verify_conformal_wrapper(_wrapper(tmp_path / "wrapper", package), base_package=package)
    matrix = ModelPackageCapabilityMatrix(
        task_id="fixture-task", package_id="point-fixture",
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
    assert not resolve_capabilities(upgraded, target="y", requirements=(CapabilityRequirement(capability="standard_deviation"),)).available
    assert not resolve_capabilities(upgraded, target="y", requirements=(CapabilityRequirement(capability="goal_probability"),)).available
    assert upgraded.target("y").quantiles is False
    assert upgraded.target("y").parametric_distribution is False


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
