from __future__ import annotations

import pytest

from decision_workbench.contracts.task_contracts import RuntimeCapability
from decision_workbench.contracts.model_capability_contracts import (
    CapabilityAvailability,
    ModelPackageCapabilityMatrix,
)
from decision_workbench.modeling.package_capabilities import (
    CapabilityRequirement,
    package_capability_matrix,
    resolve_capabilities,
    standard_predictor_capability,
)
from decision_workbench.modeling.packages.contracts import ModelPackageManifest


def _manifest() -> ModelPackageManifest:
    runtimes = (
        ("linear", "builtin.linear.v1", "empirical_quantiles", "mean"),
        ("lightgbm", "lightgbm.booster.v1", "normal", "mean"),
        ("gp", "builtin.exact_gp.v1", "normal", "mean"),
        ("posterior", "builtin.posterior_linear.v1", "normal", "mean"),
        ("quantile", "builtin.quantile_linear.v1", "empirical_quantiles", "median"),
    )
    artifacts = [{"path": "pipeline.json", "sha256": "0" * 64, "bytes": 1}]
    predictors = []
    for target, runtime_type, family, _ in runtimes:
        artifact = f"{target}.npz"
        artifacts.append({"path": artifact, "sha256": "1" * 64, "bytes": 1})
        predictors.append({"id": target, "target": target, "unit": "1", "target_kind": "continuous", "runtime_type": runtime_type, "architecture_id": {"gp": "exact_rbf_ard_v1", "posterior": "posterior_linear_v1", "quantile": "quantile_linear_v1"}.get(target), "artifact": artifact, "predictive_family": family, "feature_names": ["x"]})
    return ModelPackageManifest.model_validate({
        "schema_version": "model-package/v1", "package_id": "matrix-fixture", "package_version": "1", "task_id": "matrix-task", "input_schema_version": "canonical-candidate/v1",
        "feature_pipeline": {"id": "p", "version": "1", "spec": "pipeline.json", "canonical_input_paths": ["process.x"], "output_features": ["x"]},
        "predictors": predictors,
        "provenance": {"training_data_id": "sha256:x", "feature_dataset_id": "sha256:y", "training_code_revision": "git:test"}, "artifacts": artifacts,
    })


def _capability() -> RuntimeCapability:
    fields = []
    for target, point, std, parametric in (
        ("linear", "mean", False, False), ("lightgbm", "mean", True, True),
        ("gp", "mean", True, True), ("posterior", "mean", True, True),
        ("quantile", "median", False, False),
    ):
        fields.append({"target": target, "point_statistics": [point], "standard_deviation": std, "quantiles": True, "samples": False, "parametric_distribution": parametric, "uncertainty_components": std, "support": True, "warnings": True, "goal_probability": "distribution" if parametric else "unavailable"})
    return RuntimeCapability.model_validate({"schema_version": "runtime-capability/v1", "task_id": "matrix-task", "model_package_schema_version": "model-package/v1", "targets": fields, "joint_samples": False, "operations": {"preview": True, "detailed_prediction": True, "response_curve": False, "similarity": False, "snapshot": True, "actual_measurement": False}})


def test_matrix_compares_linear_lightgbm_gp_posterior_and_quantile_semantics() -> None:
    matrix = package_capability_matrix(_manifest(), _capability(), manifest_digest="a" * 64)
    assert matrix.target("linear").predictive_family == "empirical_quantiles"  # type: ignore[union-attr]
    assert matrix.target("quantile").point_statistics == ("median",)  # type: ignore[union-attr]
    assert resolve_capabilities(matrix, target="gp", requirements=(CapabilityRequirement(capability="normal_mean_std"),)).available
    assert resolve_capabilities(matrix, target="quantile", requirements=(CapabilityRequirement(capability="normal_mean_std"),)).available is False
    assert resolve_capabilities(matrix, target="quantile", requirements=(CapabilityRequirement(capability="standard_deviation"),)).available is False
    assert resolve_capabilities(matrix, target="linear", requirements=(CapabilityRequirement(capability="joint_samples"),)).available is False


def test_matrix_rejects_target_declaration_mismatch() -> None:
    capability = _capability().model_copy(update={"targets": _capability().targets[:-1]})
    with pytest.raises(ValueError, match="predictors do not match"):
        package_capability_matrix(_manifest(), capability, manifest_digest="a" * 64)


def test_standard_predictor_projects_actual_capability_instead_of_active_package_shape() -> None:
    predictor = _manifest().predictors[2].model_copy(
        update={
            "runtime_type": "builtin.linear.v1",
            "architecture_id": None,
            "predictive_family": "empirical_quantiles",
            "config": {"training": {"estimator_id": "ridge.v1"}},
        }
    )
    active_gp_capability = _capability().targets[2]
    projected = standard_predictor_capability(predictor, active_gp_capability)

    assert projected.quantiles is True
    assert projected.standard_deviation is False
    assert projected.parametric_distribution is False
    assert projected.goal_probability == "unavailable"


def test_standard_predictor_rejects_unknown_recipe_metadata() -> None:
    predictor = _manifest().predictors[0].model_copy(
        update={"config": {"training": {"estimator_id": "arbitrary.v1"}}}
    )
    with pytest.raises(ValueError, match="unknown estimator recipe"):
        standard_predictor_capability(predictor, _capability().targets[0])


def test_matrix_contract_rejects_duplicate_targets_and_invalid_joint_samples() -> None:
    matrix = package_capability_matrix(
        _manifest(),
        _capability(),
        manifest_digest="a" * 64,
    )
    with pytest.raises(ValueError, match="targets must be unique"):
        ModelPackageCapabilityMatrix.model_validate(
            {
                **matrix.model_dump(mode="json"),
                "targets": [matrix.targets[0], matrix.targets[0]],
            }
        )
    with pytest.raises(ValueError, match="joint samples require predictive samples"):
        ModelPackageCapabilityMatrix.model_validate(
            {
                **matrix.model_dump(mode="json"),
                "joint_samples": True,
            }
        )


def test_capability_availability_requires_reasons_only_when_unavailable() -> None:
    CapabilityAvailability(available=True)
    CapabilityAvailability(available=False, reasons=("理由",))
    with pytest.raises(ValueError, match="availabilityと理由"):
        CapabilityAvailability(available=True, reasons=("不要な理由",))
    with pytest.raises(ValueError, match="availabilityと理由"):
        CapabilityAvailability(available=False)
