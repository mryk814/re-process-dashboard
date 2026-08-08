from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest
from backend.scripts.operations import model_workflow
from pydantic import ValidationError

from decision_workbench.contracts.inference_policy_contracts import (
    InferenceDiagnostics,
    InferenceIdentity,
)
from decision_workbench.modeling.inference_policy import inference_policy
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.packages.contracts import (
    MissingOptionalDependency,
    ModelPackageManifest,
)
from decision_workbench.modeling.training.estimators import bayesian_linear
from decision_workbench.modeling.training.estimators.bayesian_linear import (
    BayesianDiagnosticsQualityError,
    BayesianSamplingFailureError,
    BayesianTrainingUnavailableError,
)
from decision_workbench.modeling.training.feature_dataset import TargetTrainingSet
from decision_workbench.modeling.training.recipe import (
    ESTIMATOR_IDS,
    BayesianRidgeEstimatorRecipe,
    HorseshoeLinearEstimatorRecipe,
    estimator_recipe,
    validate_recipe_capability,
)
from decision_workbench.modeling.training.readiness import (
    EstimatorReadinessContext,
    buildable_standard_estimator_ids,
    compatible_standard_estimator_ids,
    resolve_estimator_readiness,
    standard_estimator_catalog,
)
from decision_workbench.modeling.training.validation_plan import grouped_kfold_plan


ROOT = Path(__file__).resolve().parents[2]


def _continuous_context(estimator_id: str) -> EstimatorReadinessContext:
    return EstimatorReadinessContext(
        estimator_id=estimator_id,
        target_kind="continuous",
        row_count=18,
        independent_group_count=6,
        feature_count=2,
        target_contract="ready",
        validation_plan="ready",
        validation_strategy="grouped_kfold",
        feature_recipe="ready",
    )


def _inference_identity() -> InferenceIdentity:
    diagnostics = InferenceDiagnostics(
        status="passed",
        max_r_hat=1.01,
        min_effective_sample_size=100.0,
        divergence_count=0,
    )
    return InferenceIdentity.create(
        policy=inference_policy("nuts"),
        parameterization="test-standardized-linear/v1",
        diagnostics=diagnostics,
        seed=20260730,
        chains=2,
        warmup=8,
        draws=4,
        resource_limits={"chain_method": "sequential"},
        convergence_criteria={
            "max_r_hat": 1.05,
            "min_ess": 50.0,
            "max_divergences": 0,
        },
    )


def _training_set() -> TargetTrainingSet:
    x = np.asarray(
        [
            [0.0, 0.0],
            [0.2, 0.1],
            [0.4, 0.2],
            [0.6, 0.3],
            [0.8, 0.4],
            [1.0, 0.5],
        ],
        dtype=float,
    )
    y = 1.0 + 2.0 * x[:, 0] - 0.5 * x[:, 1]
    return TargetTrainingSet(
        target="response",
        unit="unit",
        target_kind="continuous",
        feature_names=("signal", "correlated_signal"),
        x=x,
        y=y,
        replicate_contexts=tuple(f"context-{index}" for index in range(len(y))),
        validation_groups=tuple(f"group-{index}" for index in range(len(y))),
        observation_ids=tuple((f"observation-{index}",) for index in range(len(y))),
        repeat_counts=(1,) * len(y),
        within_context_sse=np.zeros(len(y), dtype=float),
        within_context_df=np.zeros(len(y), dtype=int),
        observation_variance=0.1,
        cohort_digest="sha256:" + "1" * 64,
        fold_assignments=(
            ("group-0", 0),
            ("group-1", 1),
            ("group-2", 2),
            ("group-3", 0),
            ("group-4", 1),
            ("group-5", 2),
        ),
        fold_ids=np.asarray([0, 1, 2, 0, 1, 2], dtype=int),
        fold_digest="sha256:" + "2" * 64,
        folds=3,
        validation_plan=grouped_kfold_plan(folds=3, seed=20260730),
        validation_plan_digest="sha256:" + "3" * 64,
        validation_diagnostics={},
    )


def _manifest_with_inference_provenance() -> dict[str, object]:
    identity = _inference_identity()
    predictor_id = "response-bayesian-ridge"
    return {
        "schema_version": "model-package/v1",
        "package_id": "bayesian-contract-test",
        "package_version": "1.0.0",
        "task_id": "test-task",
        "input_schema_version": "canonical-candidate/v1",
        "feature_pipeline": {
            "id": "test-pipeline",
            "version": "1",
            "spec": "feature-pipeline/pipeline.json",
            "canonical_input_paths": ["input.x"],
            "output_features": ["x"],
            "artifacts": [],
        },
        "predictors": [
            {
                "id": predictor_id,
                "target": "response",
                "unit": "unit",
                "target_kind": "continuous",
                "runtime_type": "builtin.posterior_linear.v1",
                "architecture_id": "posterior_linear_v1",
                "artifact": "model-artifacts/response.npz",
                "predictive_family": "normal",
                "feature_names": ["x"],
                "inference_identity": identity.model_dump(mode="json"),
            }
        ],
        "provenance": {
            "training_data_id": "sha256:" + "1" * 64,
            "feature_dataset_id": "sha256:" + "2" * 64,
            "training_code_revision": "standard-model-training/v1:test",
            "inference_identities": {
                predictor_id: identity.model_dump(mode="json"),
            },
            "inference_provenance": {
                predictor_id: {
                    "recipe_id": "bayesian-ridge.v1",
                    "recipe_parameters": {
                        "estimator_id": "bayesian-ridge.v1",
                        "coefficient_prior_scale": 1.0,
                    },
                    "inference_identity_digest": identity.identity_digest,
                    "diagnostics": identity.diagnostics.model_dump(mode="json"),
                },
            },
        },
        "artifacts": [
            {
                "path": "feature-pipeline/pipeline.json",
                "sha256": "a" * 64,
                "bytes": 1,
            },
            {
                "path": "model-artifacts/response.npz",
                "sha256": "b" * 64,
                "bytes": 1,
            },
        ],
    }
def _fake_fit(
    recipe: BayesianRidgeEstimatorRecipe | HorseshoeLinearEstimatorRecipe,
    *,
    local_scales: bool,
) -> bayesian_linear._Fit:
    identity = _inference_identity()
    coefficients = np.asarray(
        [
            [1.0, -0.5],
            [1.1, -0.4],
            [0.9, -0.6],
            [1.0, -0.5],
        ],
        dtype=float,
    )
    return bayesian_linear._Fit(
        coefficients=coefficients,
        standardized_coefficients=coefficients,
        intercepts=np.asarray([1.0, 1.01, 0.99, 1.0]),
        observation_scales=np.asarray([0.2, 0.21, 0.19, 0.2]),
        local_scales=(
            np.full_like(coefficients, 0.7)
            if local_scales
            else None
        ),
        inference_identity=identity,
    )


def test_shrinkage_recipe_ids_and_fixed_inference_policy_are_distinct() -> None:
    ridge = estimator_recipe("bayesian-ridge.v1")
    horseshoe = estimator_recipe("horseshoe-linear.v1")

    assert isinstance(ridge, BayesianRidgeEstimatorRecipe)
    assert isinstance(horseshoe, HorseshoeLinearEstimatorRecipe)
    assert ridge.estimator_id != horseshoe.estimator_id
    assert ridge.parameterization != horseshoe.parameterization
    assert ridge.sampler == horseshoe.sampler == "nuts"
    assert (ridge.chains, ridge.warmup, ridge.draws) == (2, 256, 256)
    assert (horseshoe.chains, horseshoe.warmup, horseshoe.draws) == (2, 256, 256)
    assert ridge.observation_scale_prior == "half-normal-1"
    assert horseshoe.regularization_policy == "fixed-student-t-capped-horseshoe/v1"
    assert horseshoe.parameterization == (
        "standardized-fixed-student-t-capped-horseshoe/v1"
    )
    assert "coefficient_prior_scale" not in horseshoe.model_dump()
    assert "local_scale_prior" not in ridge.model_dump()
    assert set((ridge.estimator_id, horseshoe.estimator_id)) <= set(ESTIMATOR_IDS)


def test_horseshoe_recipe_rejects_canonical_regularized_horseshoe_identity() -> None:
    with pytest.raises(ValidationError):
        estimator_recipe(
            "horseshoe-linear.v1",
            {
                "regularization_policy": "regularized-horseshoe/v1",
            },
        )


def test_horseshoe_slab_degrees_of_freedom_is_bound_to_the_slab_prior() -> None:
    numpyro = pytest.importorskip("numpyro")
    import jax.numpy as jnp
    from jax import random
    from numpyro.handlers import seed, trace
    import numpyro.distributions as dist

    default = estimator_recipe("horseshoe-linear.v1")
    sensitivity = estimator_recipe(
        "horseshoe-linear.v1",
        {"slab_degrees_of_freedom": 12.0},
    )
    default_model = bayesian_linear._sampler_model(
        default,
        feature_count=2,
        jnp=jnp,
        numpyro=numpyro,
        dist=dist,
    )
    sensitivity_model = bayesian_linear._sampler_model(
        sensitivity,
        feature_count=2,
        jnp=jnp,
        numpyro=numpyro,
        dist=dist,
    )
    default_trace = trace(seed(default_model, random.PRNGKey(3))).get_trace(
        jnp.zeros((2, 2)),
        None,
    )
    sensitivity_trace = trace(
        seed(sensitivity_model, random.PRNGKey(3))
    ).get_trace(jnp.zeros((2, 2)), None)

    assert float(default_trace["slab_variance"]["value"]) == pytest.approx(8.0)
    assert float(sensitivity_trace["slab_variance"]["value"]) == pytest.approx(
        4.8
    )


def test_shrinkage_readiness_is_experimental_and_not_production_resolver_default() -> None:
    catalog = {
        entry.estimator_id: entry
        for entry in standard_estimator_catalog().entries
    }
    outputs = (type("Output", (), {"target_kind": "continuous"})(),)
    # The helper consumes the same output contract shape as the real resolver;
    # this small object keeps the test independent of a Task fixture.
    buildable = buildable_standard_estimator_ids(outputs)
    production = compatible_standard_estimator_ids(outputs)

    assert {"bayesian-ridge.v1", "horseshoe-linear.v1"}.issubset(buildable)
    assert "bayesian-ridge.v1" not in production
    assert "horseshoe-linear.v1" not in production
    for estimator_id in ("bayesian-ridge.v1", "horseshoe-linear.v1"):
        entry = catalog[estimator_id]
        assert entry.adoption_status == "experimental"
        assert entry.runtime_type == "builtin.posterior_linear.v1"
        assert entry.artifact_format == "bounded-npz"
        assert entry.required_dependency == "numpyro"
        assert "posterior_convergence" in entry.quality_metrics
        assert any("correlated" in item for item in entry.known_limitations)

    missing = resolve_estimator_readiness(
        _continuous_context("bayesian-ridge.v1"),
        available_dependencies=frozenset(),
    )
    assert missing.status == "unavailable_missing_dependency"
    assert "no alternative estimator was selected" in missing.reasons[0]


def test_posterior_linear_capability_requires_predictive_distribution_and_components() -> None:
    capability = type(
        "Capability",
        (),
        {
            "joint_samples": False,
            "targets": (
                type(
                    "Target",
                    (),
                    {
                        "target": "response",
                        "point_statistics": ("mean",),
                        "standard_deviation": True,
                        "quantiles": True,
                        "samples": False,
                        "parametric_distribution": True,
                        "uncertainty_components": True,
                        "goal_probability": "distribution",
                    },
                )(),
            ),
            "task_id": "test",
        },
    )()
    validate_recipe_capability(estimator_recipe("bayesian-ridge.v1"), capability)
    validate_recipe_capability(estimator_recipe("horseshoe-linear.v1"), capability)


@pytest.mark.parametrize(
    ("estimator_id", "has_local_scales"),
    (("bayesian-ridge.v1", False), ("horseshoe-linear.v1", True)),
)
def test_trainer_writes_existing_safe_posterior_linear_npz_and_keeps_all_features(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    estimator_id: str,
    has_local_scales: bool,
) -> None:
    data = _training_set()
    recipe = estimator_recipe(estimator_id)
    calls: list[tuple[np.ndarray, np.ndarray]] = []

    def fake_fit(values: np.ndarray, target: np.ndarray, recipe: object, *, seed: int):
        calls.append((np.asarray(values).copy(), np.asarray(target).copy()))
        return _fake_fit(recipe, local_scales=has_local_scales)

    monkeypatch.setattr(bayesian_linear, "_fit", fake_fit)
    artifact = tmp_path / f"{estimator_id}.npz"
    trained = bayesian_linear.train(data, recipe, artifact)

    with np.load(artifact, allow_pickle=False) as arrays:
        assert set(arrays.files) == (
            {"beta_draws", "intercept_draws", "noise_scale_draws"}
            | ({"local_scale_draws"} if has_local_scales else set())
        )
        assert arrays["beta_draws"].shape == (4, len(data.feature_names))
        assert arrays["intercept_draws"].shape == (4,)
        assert arrays["noise_scale_draws"].shape == (4,)
        if has_local_scales:
            assert arrays["local_scale_draws"].shape == arrays["beta_draws"].shape

    assert trained.predictor["runtime_type"] == "builtin.posterior_linear.v1"
    assert trained.predictor["architecture_id"] == "posterior_linear_v1"
    assert trained.predictor["feature_names"] == list(data.feature_names)
    assert trained.predictor["inference_identity"]["algorithm_id"] == "nuts"
    assert trained.predictor["inference_identity"]["draws"] == 4
    assert trained.diagnostics["adoption_status"] == "experimental"
    assert set(trained.diagnostics["coefficient_summary"]["features"]) == set(
        data.feature_names
    )
    assert trained.diagnostics["correlation_caution"]["high_correlation_pairs"]
    assert not list(tmp_path.glob("*.pkl"))
    assert not list(tmp_path.glob("*.joblib"))
    # Three outer folds exclude their held-out target rows; the final refit is
    # the only fit that receives all rows.
    assert [len(target) for _, target in calls] == [4, 4, 4, 6]


def test_missing_dependency_is_typed_and_does_not_select_ridge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> tuple[object, ...]:
        raise BayesianTrainingUnavailableError("numpyro")

    monkeypatch.setattr(bayesian_linear, "_dependencies", missing)
    with pytest.raises(BayesianTrainingUnavailableError) as error:
        bayesian_linear._fit(
            np.ones((4, 2)),
            np.ones(4),
            estimator_recipe("bayesian-ridge.v1"),
            seed=7,
        )
    assert error.value.reason_code == "unavailable_missing_dependency"
    assert "fallback" in str(error.value)
    finding = error.value.quality_finding(
        estimator_id="bayesian-ridge.v1",
        target="response",
    )
    assert finding.status == "unavailable"
    assert finding.diagnostics is not None
    assert finding.diagnostics.status == "not_applicable"


def test_divergence_and_ess_are_quality_findings() -> None:
    settings = bayesian_linear._InferenceSettings(
        chains=2,
        warmup=8,
        draws=4,
        max_r_hat=1.05,
        min_ess=50.0,
        max_divergences=0,
    )
    diagnostics = bayesian_linear._diagnostics_from_summary(
        {
            "coefficients": {
                "r_hat": np.asarray([1.2, 1.01]),
                "n_eff": np.asarray([4.0, 100.0]),
            }
        },
        divergence_count=1,
        settings=settings,
    )
    assert diagnostics.status == "failed"
    assert any("R-hat" in finding for finding in diagnostics.findings)
    assert any("ESS" in finding for finding in diagnostics.findings)
    assert any("divergences" in finding for finding in diagnostics.findings)
    error = BayesianDiagnosticsQualityError(
        "quality gate failed",
        diagnostics=diagnostics,
    )
    assert error.reason_code == "sampling_quality_failed"


def test_sampling_failure_serializes_a_typed_diagnostic_finding() -> None:
    error = BayesianSamplingFailureError("sampler failed before draw export")
    finding = error.quality_finding(
        estimator_id="bayesian-ridge.v1",
        target="response",
    )

    assert finding.status == "failed"
    assert finding.reason_code == "sampling_failed"
    assert finding.target == "response"
    assert finding.diagnostics is not None
    assert finding.diagnostics.status == "failed"


def test_existing_hot_rolling_package_load_and_predict_regression() -> None:
    root = ROOT / "models" / "packages" / "hot-rolled-horseshoe-process-v2"
    package = ModelPackageLoader().load(root)
    predictor = package.load_predictor("ts-horseshoe")
    manifest_predictor = next(
        item for item in package.manifest.predictors if item.target == "TS"
    )
    features = {name: 0.0 for name in manifest_predictor.feature_names}
    summary = predictor.predict(features)

    assert np.isfinite(summary.point_estimate)
    assert summary.prediction_interval is not None
    assert summary.uncertainty_components is not None
    assert np.isfinite(
        summary.uncertainty_components["total_predictive_std"]
    )


def test_rope_uses_standardized_coefficients_and_is_unit_conversion_invariant() -> None:
    fit = _fake_fit(estimator_recipe("bayesian-ridge.v1"), local_scales=False)
    scaled_raw_fit = replace(
        fit,
        coefficients=fit.coefficients / 100.0,
        standardized_coefficients=fit.standardized_coefficients,
    )

    original = bayesian_linear._coefficient_summary(
        fit,
        ("signal", "correlated_signal"),
        rope_half_width=0.1,
    )
    converted = bayesian_linear._coefficient_summary(
        scaled_raw_fit,
        ("signal", "correlated_signal"),
        rope_half_width=0.1,
    )

    assert original["coefficient_scale"] == (
        "standardized_predictor_and_response"
    )
    assert original["rope_semantics"]
    assert original["features"] == converted["features"]
    assert original["rope_half_width"] == converted["rope_half_width"]


def test_package_provenance_contract_accepts_fixed_inference_identity() -> None:
    identity = _inference_identity()
    payload = {
        "training_data_id": "sha256:" + "4" * 64,
        "feature_dataset_id": "sha256:" + "5" * 64,
        "training_code_revision": "standard-model-training/v1:bayesian-ridge.v1",
        "inference_identities": {"response-bayesian-ridge": identity.model_dump(mode="json")},
        "inference_provenance": {
            "response-bayesian-ridge": {
                "recipe_id": "bayesian-ridge.v1",
                "recipe_parameters": estimator_recipe(
                    "bayesian-ridge.v1"
                ).model_dump(mode="json"),
                "inference_identity_digest": identity.identity_digest,
                "diagnostics": identity.diagnostics.model_dump(mode="json"),
            }
        },
    }
    from decision_workbench.modeling.packages.contracts import ProvenanceSpec

    provenance = ProvenanceSpec.model_validate(payload)
    assert provenance.inference_identities["response-bayesian-ridge"].draws == 4
    assert (
        provenance.inference_provenance["response-bayesian-ridge"].recipe_id
        == "bayesian-ridge.v1"
    )


def test_manifest_provenance_maps_are_cross_validated_against_predictors() -> None:
    manifest = ModelPackageManifest.model_validate(
        _manifest_with_inference_provenance()
    )
    predictor_id = manifest.predictors[0].id
    assert set(manifest.provenance.inference_identities) == {predictor_id}
    assert set(manifest.provenance.inference_provenance) == {predictor_id}


def test_manifest_rejects_unknown_inference_provenance_key() -> None:
    payload = _manifest_with_inference_provenance()
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    identities = provenance["inference_identities"]
    inference_provenance = provenance["inference_provenance"]
    assert isinstance(identities, dict)
    assert isinstance(inference_provenance, dict)
    identities["unknown-predictor"] = next(iter(identities.values()))
    inference_provenance["unknown-predictor"] = next(
        iter(inference_provenance.values())
    )

    with pytest.raises(ValidationError, match="exactly match predictors"):
        ModelPackageManifest.model_validate(payload)


def test_manifest_rejects_inference_provenance_digest_mismatch() -> None:
    payload = _manifest_with_inference_provenance()
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    entry = provenance["inference_provenance"]["response-bayesian-ridge"]
    assert isinstance(entry, dict)
    entry["inference_identity_digest"] = "sha256:" + "c" * 64

    with pytest.raises(
        ValidationError,
        match="provenance inference identity digest does not match",
    ):
        ModelPackageManifest.model_validate(payload)


def test_manifest_rejects_arbitrary_inference_provenance_fields() -> None:
    payload = _manifest_with_inference_provenance()
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    entry = provenance["inference_provenance"]["response-bayesian-ridge"]
    assert isinstance(entry, dict)
    entry["unreviewed_field"] = "must not be accepted"

    with pytest.raises(ValidationError):
        ModelPackageManifest.model_validate(payload)


def test_manifest_rejects_inference_provenance_recipe_id_mismatch() -> None:
    payload = _manifest_with_inference_provenance()
    provenance = payload["provenance"]
    assert isinstance(provenance, dict)
    entry = provenance["inference_provenance"]["response-bayesian-ridge"]
    assert isinstance(entry, dict)
    entry["recipe_id"] = "horseshoe-linear.v1"

    with pytest.raises(ValidationError, match="recipe_parameters.estimator_id"):
        ModelPackageManifest.model_validate(payload)


@pytest.mark.parametrize("estimator_id", ("bayesian-ridge.v1", "horseshoe-linear.v1"))
def test_numpyro_fit_produces_safe_draws_when_optional_runtime_is_installed(
    monkeypatch: pytest.MonkeyPatch,
    estimator_id: str,
) -> None:
    pytest.importorskip("numpyro")
    settings = bayesian_linear._InferenceSettings(
        chains=2,
        warmup=64,
        draws=64,
        max_r_hat=1.5,
        min_ess=1.0,
        max_divergences=0,
    )
    monkeypatch.setattr(bayesian_linear, "_settings", lambda recipe: settings)
    values = np.column_stack(
        (
            np.linspace(-1.0, 1.0, 12),
            np.sin(np.linspace(-1.0, 1.0, 12) * 2.5),
        )
    )
    target = 0.4 + 0.8 * values[:, 0] + np.asarray(
        [0.02, -0.01, 0.01, -0.02] * 3
    )
    fit = bayesian_linear._fit(
        values,
        target,
        estimator_recipe(estimator_id),
        seed=13,
    )
    assert fit.coefficients.shape == (128, 2)
    assert fit.inference_identity.algorithm_id == "nuts"
    assert fit.inference_identity.draws == 64
    if estimator_id == "horseshoe-linear.v1":
        assert fit.local_scales is not None
        assert fit.local_scales.shape == fit.coefficients.shape


def test_same_cohort_comparison_saves_explicit_adoption_and_interpretation_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_dataset_id = "sha256:" + "6" * 64

    def fake_build_package(*args: object, **kwargs: object) -> dict[str, object]:
        package_path = Path(args[2])
        package_path.mkdir(parents=True, exist_ok=True)
        (package_path / "reference").mkdir(exist_ok=True)
        (package_path / "reference" / "training_stats.json").write_text(
            json.dumps(
                {
                    "cohort_digests": {"response": "sha256:" + "7" * 64},
                    "fold_digests": {"response": "sha256:" + "8" * 64},
                }
            ),
            encoding="utf-8",
        )
        return {
            "dataset": {"feature_dataset_id": feature_dataset_id},
            "package": {
                "quality_report": {
                    "schema_version": "model-quality-report/v1",
                    "targets": [{"target": "response", "interval_coverage_90": 0.9}],
                }
            },
        }

    monkeypatch.setattr(model_workflow, "build_package", fake_build_package)
    report = model_workflow.compare_estimators(
        "heat-treatment-tradeoff-v1",
        ROOT / "data" / "source" / "heat-treatment-tradeoff-v1.xlsx",
        tmp_path / "comparison",
        tmp_path / "feature-dataset.json",
        estimators=(
            "ridge.v1",
            "bayesian-ridge.v1",
            "horseshoe-linear.v1",
        ),
        estimator_options=None,
        package_prefix="shrinkage-comparison",
        package_version="1.0.0",
    )

    assert report["selection"] is None
    assert report["feature_dataset_id"] == feature_dataset_id
    assert [item["adoption_status"] for item in report["models"]] == [
        "production",
        "experimental",
        "experimental",
    ]
    assert report["adoption_policy"]["no_adopt"]
    assert report["interpretation_contract"]["prediction_uncertainty"]
    assert report["counterexample_protocol"]["noisy_fixture"]["status"] == (
        "required_before_adoption"
    )
    assert report["counterexample_protocol"]["correlated_features"]["status"] == (
        "reported_per_model"
    )
    saved = json.loads(
        (tmp_path / "comparison" / "comparison.json").read_text(
            encoding="utf-8"
        )
    )
    assert saved["models"][1]["adoption_decision"] == "experimental"
    assert report["models"][0]["quality"] is not None
    assert report["models"][0]["quality"]["targets"][0][
        "interval_coverage_90"
    ] == pytest.approx(0.9)


def test_comparison_serializes_typed_bayesian_failure_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_dataset_id = "sha256:" + "9" * 64
    failure = BayesianDiagnosticsQualityError(
        "minimum ESS 2 is below 50",
        diagnostics=InferenceDiagnostics(
            status="failed",
            max_r_hat=1.01,
            min_effective_sample_size=2.0,
            divergence_count=0,
            findings=("minimum ESS 2 is below 50",),
        ),
    ).bind_target("response")

    def fake_build_package(*args: object, **kwargs: object) -> dict[str, object]:
        dataset_output = Path(args[3])
        dataset_output.write_text("{}", encoding="utf-8")
        if kwargs["estimator"] == "bayesian-ridge.v1":
            raise failure
        package_path = Path(args[2])
        (package_path / "reference").mkdir(parents=True, exist_ok=True)
        (package_path / "reference" / "training_stats.json").write_text(
            json.dumps(
                {
                    "cohort_digests": {"response": "sha256:" + "a" * 64},
                    "fold_digests": {"response": "sha256:" + "b" * 64},
                }
            ),
            encoding="utf-8",
        )
        return {
            "dataset": {"feature_dataset_id": feature_dataset_id},
            "package": {
                "quality_report": {
                    "schema_version": "model-quality-report/v1",
                    "targets": [{"target": "response", "interval_coverage_90": 0.8}],
                }
            },
        }

    monkeypatch.setattr(model_workflow, "build_package", fake_build_package)
    monkeypatch.setattr(
        model_workflow,
        "canonical_training_dataset_digest",
        lambda _payload: feature_dataset_id,
    )
    report = model_workflow.compare_estimators(
        "heat-treatment-tradeoff-v1",
        ROOT / "data" / "source" / "heat-treatment-tradeoff-v1.xlsx",
        tmp_path / "comparison",
        tmp_path / "feature-dataset.json",
        estimators=("bayesian-ridge.v1", "ridge.v1"),
        estimator_options=None,
        package_prefix="failure-comparison",
        package_version="1.0.0",
    )

    failed = report["models"][0]
    assert failed["package"] is None
    assert failed["adoption_decision"] == "sampling_quality_failed"
    assert failed["quality_findings"][0]["schema_version"] == (
        "bayesian-quality-finding/v1"
    )
    assert failed["quality_findings"][0]["diagnostics"]["status"] == "failed"
    assert report["models"][1]["estimator_id"] == "ridge.v1"


def test_comparison_serializes_missing_optional_dependency_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_dataset_id = "sha256:" + "4" * 64

    def fake_build_package(*args: object, **kwargs: object) -> dict[str, object]:
        dataset_output = Path(args[3])
        dataset_output.write_text("{}", encoding="utf-8")
        if kwargs["estimator"] == "lightgbm-regression.v1":
            raise MissingOptionalDependency(
                "lightgbm estimator requires the allow-listed LightGBM dependency"
            )
        package_path = Path(args[2])
        (package_path / "reference").mkdir(parents=True, exist_ok=True)
        (package_path / "reference" / "training_stats.json").write_text(
            json.dumps(
                {
                    "cohort_digests": {"response": "sha256:" + "c" * 64},
                    "fold_digests": {"response": "sha256:" + "d" * 64},
                }
            ),
            encoding="utf-8",
        )
        return {
            "dataset": {"feature_dataset_id": feature_dataset_id},
            "package": {
                "quality_report": {
                    "schema_version": "model-quality-report/v1",
                    "targets": [{"target": "response", "interval_coverage_90": 0.8}],
                }
            },
        }

    monkeypatch.setattr(model_workflow, "build_package", fake_build_package)
    monkeypatch.setattr(
        model_workflow,
        "canonical_training_dataset_digest",
        lambda _payload: feature_dataset_id,
    )
    report = model_workflow.compare_estimators(
        "heat-treatment-tradeoff-v1",
        ROOT / "data" / "source" / "heat-treatment-tradeoff-v1.xlsx",
        tmp_path / "comparison",
        tmp_path / "feature-dataset.json",
        estimators=("lightgbm-regression.v1", "ridge.v1"),
        estimator_options=None,
        package_prefix="dependency-comparison",
        package_version="1.0.0",
    )

    unavailable = report["models"][0]
    assert unavailable["package"] is None
    assert unavailable["feature_dataset_id"] == feature_dataset_id
    finding = unavailable["quality_findings"][0]
    assert finding["schema_version"] == "standard-comparison-quality-finding/v1"
    assert finding["status"] == "unavailable"
    assert finding["reason_code"] == "unavailable_missing_dependency"
    assert finding["diagnostics"]["status"] == "not_applicable"
    assert report["models"][1]["estimator_id"] == "ridge.v1"
