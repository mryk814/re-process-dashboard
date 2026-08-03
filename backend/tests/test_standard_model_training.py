from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from decision_workbench.modeling.model_lifecycle import canonical_training_dataset
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.modeling.model_lifecycle import ACTIVE_PACKAGES_PATH
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.training.estimators import exact_gp
from decision_workbench.modeling.training.estimators import bayesian_additive
from decision_workbench.modeling.training.estimators import lightgbm
from decision_workbench.modeling.training.estimators import ridge
from decision_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
)
from decision_workbench.modeling.training.recipe import estimator_recipe
from decision_workbench.modeling.training.recipe import validate_recipe_capability
from decision_workbench.adapters.builtin_additive_terms import (
    BuiltinAdditiveTermsAdapter,
)
from decision_workbench.modeling.packages.contracts import (
    PackageContractError,
    PredictionInterval,
    PredictorSpec,
    validate_predictive_summary,
)
from decision_workbench.modeling.training.package_assembler import (
    _target_point_error,
)
from decision_workbench.task_composition.builtin.catalog import BUILTIN_TASK_MODULES
from decision_workbench.task_composition.catalog import registered_task_modules
from decision_workbench.task_composition.catalog import resolve_task_source, task_module
from decision_workbench.tasks.task_registry import load_task_contracts


ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "backend" / "scripts" / "operations"
GENERATORS = ROOT / "backend" / "scripts" / "generators"
for script_root in (OPERATIONS, GENERATORS):
    if str(script_root) not in sys.path:
        sys.path.insert(0, str(script_root))

from model_workflow import (  # noqa: E402
    build_package,
    compare_estimators,
    estimator_inventory,
)
from build_default_model_package import _fit_gp_hyperparameters  # noqa: E402


HOT_ROLLING_TASK = "hot-rolled-properties-v1"
SOURCE = ROOT / "data/source/material_workbench_tutorial_v2.xlsx"
HEAT_SOURCE = ROOT / "data/source/external/heat_treatment_tradeoff_samples.csv"
BUNDLED_STANDARD_AUTHORING_TASK_IDS = tuple(
    sorted(
        task_id
        for task_id, module in BUILTIN_TASK_MODULES.items()
        if module.standard_model_authoring is not None
    )
)


def _training_set(task_id: str, target: str, unit: str):
    module = task_module(task_id)
    data = module.data_loader(resolve_task_source(task_id), None)
    canonical = canonical_training_dataset(
        task_id,
        data,
        load_task_contracts()[task_id],
    )
    return compile_target_training_set(
        canonical,
        target=target,
        unit=unit,
    )


def test_stable_gp_training_is_deterministic_scaled_ard_multistart() -> None:
    rng = np.random.default_rng(7)
    x = rng.normal(size=(28, 5))
    y = 12.0 + 3.5 * x[:, 0] + 0.15 * rng.normal(size=len(x))

    first = _fit_gp_hyperparameters(
        x,
        y,
        train_noise=0.04,
        restarts=3,
        seed=11,
    )
    second = _fit_gp_hyperparameters(
        x,
        y,
        train_noise=0.04,
        restarts=3,
        seed=11,
    )

    np.testing.assert_allclose(first[0], second[0], rtol=0, atol=1e-10)
    assert first[1] == pytest.approx(second[1], abs=1e-10)
    assert first[2] == pytest.approx(second[2], abs=1e-10)
    assert first[0].shape == (x.shape[1],)
    assert np.all(first[0] > 0)
    assert first[0][0] < np.median(first[0][1:])
    diagnostics = first[3]
    assert diagnostics["kernel"] == "ARD-RBF"
    assert diagnostics["restarts"] == 3
    assert diagnostics["input_standardization"] == (
        "per_feature_training_mean_std"
    )
    assert diagnostics["output_standardization"]["scale"] > 0


def test_estimator_recipe_is_allow_listed_and_bounded() -> None:
    gp = estimator_recipe(
        "exact-gp-rbf.v1",
        {"restarts": 2, "seed": 7, "max_rows": 50},
    )
    assert gp.estimator_id == "exact-gp-rbf.v1"
    assert gp.restarts == 2

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        estimator_recipe("ridge.v1", {"import_path": "unsafe.module"})
    with pytest.raises(ValidationError, match="less than or equal to 2000"):
        estimator_recipe("exact-gp-rbf.v1", {"max_rows": 50_000})
    with pytest.raises(ValidationError):
        estimator_recipe("unknown.v1")


def test_hot_rolling_compiles_training_context_not_plain_parent() -> None:
    module = task_module(HOT_ROLLING_TASK)
    data = module.data_loader(SOURCE)
    canonical = canonical_training_dataset(
        HOT_ROLLING_TASK,
        data,
        load_task_contracts()[HOT_ROLLING_TASK],
    )
    training = compile_target_training_set(canonical, target="TS", unit="MPa")

    assert len(canonical["rows"]) == 8
    assert len(training.replicate_contexts) == 7
    assert len(set(training.validation_groups)) == 6
    assert len(set(row["parent_key"] for row in canonical["rows"])) == 6
    assert sorted(training.repeat_counts) == [1, 1, 1, 1, 1, 1, 2]
    assert sorted(len(items) for items in training.observation_ids) == [1, 1, 1, 1, 1, 1, 2]
    assert {
        observation_id
        for items in training.observation_ids
        for observation_id in items
    } == {row["observation_id"] for row in canonical["rows"]}


def test_exact_gp_outer_fold_does_not_observe_held_out_targets() -> None:
    data = _training_set(HOT_ROLLING_TASK, "TS", "MPa")
    recipe = estimator_recipe(
        "exact-gp-rbf.v1",
        {"restarts": 1, "folds": 5, "seed": 19},
    )
    predictions, variances = exact_gp._honest_grouped_predictions(data, recipe)
    held_out = data.fold_ids == 0
    changed_y = data.y.copy()
    changed_y[held_out] += 10_000
    changed = replace(data, y=changed_y)

    changed_predictions, changed_variances = (
        exact_gp._honest_grouped_predictions(changed, recipe)
    )

    np.testing.assert_allclose(
        changed_predictions[held_out],
        predictions[held_out],
        rtol=0,
        atol=1e-10,
    )
    np.testing.assert_allclose(
        changed_variances[held_out],
        variances[held_out],
        rtol=0,
        atol=1e-10,
    )


def test_bayesian_additive_is_fold_honest_and_separates_interval_estimands(
    tmp_path: Path,
) -> None:
    data = _training_set(
        "heat-treatment-tradeoff-v1",
        "hardness_hv",
        "HV",
    )
    recipe = estimator_recipe(
        "bayesian-additive-spline.v1",
        {
            "folds": 4,
            "min_unique_values_for_smooth": 4,
            "max_basis_per_feature": 5,
        },
    )
    predictions, variances = bayesian_additive._honest_predictions(
        data, recipe
    )
    held_out = data.fold_ids == 0
    changed_y = data.y.copy()
    changed_y[held_out] += 10_000
    changed_predictions, changed_variances = (
        bayesian_additive._honest_predictions(
            replace(data, y=changed_y),
            recipe,
        )
    )
    np.testing.assert_allclose(
        changed_predictions[held_out],
        predictions[held_out],
        rtol=0,
        atol=1e-10,
    )
    np.testing.assert_allclose(
        changed_variances[held_out],
        variances[held_out],
        rtol=0,
        atol=1e-10,
    )

    artifact = tmp_path / "bayesian-additive.npz"
    trained = bayesian_additive.train(data, recipe, artifact)

    class _Artifacts:
        def artifact_path(self, _: str) -> Path:
            return artifact

    predictor = BuiltinAdditiveTermsAdapter().load(
        _Artifacts(),  # type: ignore[arg-type]
        PredictorSpec.model_validate(trained.predictor),
    )
    features = {
        name: float(value)
        for name, value in zip(data.feature_names, data.x[0], strict=True)
    }
    summary = predictor.predict(features)
    explanation = predictor.explain(features)

    assert summary.prediction_interval is not None
    assert summary.prediction_interval.method == "bayesian"
    assert summary.latent_mean_credible_interval is not None
    assert (
        summary.latent_mean_credible_interval.upper
        - summary.latent_mean_credible_interval.lower
        < summary.prediction_interval.upper
        - summary.prediction_interval.lower
    )
    assert summary.uncertainty_components is not None
    assert summary.uncertainty_components[
        "predictive_standard_deviation"
    ] ** 2 == pytest.approx(
        summary.uncertainty_components[
            "latent_mean_standard_deviation"
        ] ** 2
        + summary.uncertainty_components[
            "observation_standard_deviation"
        ] ** 2
    )
    assert explanation.intercept + sum(
        term.contribution for term in explanation.terms
    ) == pytest.approx(summary.point_estimate)
    cohort_contributions = np.asarray([
        [
            term.contribution
            for term in predictor.explain({
                name: float(value)
                for name, value in zip(
                    data.feature_names,
                    row,
                    strict=True,
                )
            }).terms
        ]
        for row in data.x
    ])
    np.testing.assert_allclose(
        cohort_contributions.mean(axis=0),
        0.0,
        rtol=0,
        atol=1e-8,
    )

    incompatible = summary.model_copy(update={
        "prediction_interval": PredictionInterval(
            method="bayesian",
            coverage_level=0.8,
            lower=summary.prediction_interval.lower,
            upper=summary.prediction_interval.upper,
        )
    })
    with pytest.raises(PackageContractError, match="same-coverage"):
        validate_predictive_summary(
            incompatible,
            PredictorSpec.model_validate(trained.predictor),
        )

    with np.load(artifact, allow_pickle=False) as stored:
        arrays = {name: stored[name] for name in stored.files}
    covariance = arrays["posterior_covariance"].copy()
    covariance[0, 0] = -1.0
    arrays["posterior_covariance"] = covariance
    np.savez(artifact, **arrays)
    with pytest.raises(PackageContractError, match="covariance is invalid"):
        BuiltinAdditiveTermsAdapter().load(
            _Artifacts(),  # type: ignore[arg-type]
            PredictorSpec.model_validate(trained.predictor),
        )


def test_bayesian_additive_recovers_main_effects_and_exposes_interaction_limit() -> None:
    recipe = estimator_recipe(
        "bayesian-additive-spline.v1",
        {
            "min_unique_values_for_smooth": 4,
            "max_basis_per_feature": 6,
            "smoothness_precision": 0.5,
        },
    )
    x = np.linspace(-1.0, 1.0, 81)
    category = (x > 0).astype(float)
    values = np.column_stack([x, category, np.ones(len(x))])
    target = 1.2 + 2.0 * x + 1.5 * x**2 + 0.6 * category
    fitted = bayesian_additive._fit(
        values,
        target,
        recipe,
        observation_variance_floor=1e-8,
    )
    recovered, _ = fitted.predict(values)

    assert [term.kind for term in fitted.terms] == [
        "bspline_univariate",
        "linear",
    ]
    assert np.sqrt(np.mean((target - recovered) ** 2)) < 0.08
    for term in fitted.terms:
        np.testing.assert_allclose(
            (term.basis(values) - term.center).mean(axis=0),
            0.0,
            rtol=0,
            atol=1e-12,
        )

    grid = np.linspace(-1.0, 1.0, 17)
    interaction_values = np.asarray([
        [left, right]
        for left in grid
        for right in grid
    ])
    interaction_target = (
        interaction_values[:, 0] * interaction_values[:, 1]
    )
    interaction_fit = bayesian_additive._fit(
        interaction_values,
        interaction_target,
        recipe,
        observation_variance_floor=1e-8,
    )
    interaction_prediction, _ = interaction_fit.predict(
        interaction_values
    )
    assert np.sqrt(np.mean(
        (interaction_target - interaction_prediction) ** 2
    )) > 0.25


def test_ridge_outer_prediction_does_not_observe_held_out_targets() -> None:
    data = _training_set("heat-treatment-tradeoff-v1", "hardness_hv", "HV")
    predictions, _ = ridge._honest_grouped_evaluation(data, alpha=1.0)
    held_out = data.fold_ids == 0
    changed_y = data.y.copy()
    changed_y[held_out] += 10_000

    changed_predictions, _ = ridge._honest_grouped_evaluation(
        replace(data, y=changed_y),
        alpha=1.0,
    )

    np.testing.assert_allclose(
        changed_predictions[held_out],
        predictions[held_out],
        rtol=0,
        atol=1e-10,
    )


class _MeanBooster:
    def __init__(self, value: float) -> None:
        self.value = value

    def predict(self, x: np.ndarray, **_: object) -> np.ndarray:
        return np.full(len(x), self.value, dtype=float)


def test_lightgbm_nested_evaluation_does_not_observe_outer_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_train(
        _: np.ndarray,
        y: np.ndarray,
        **options: object,
    ) -> _MeanBooster:
        objective = options["objective"]
        value = float(np.mean(y))
        if objective == "binary":
            value = min(max(value, 0.05), 0.95)
        return _MeanBooster(value)

    monkeypatch.setattr(lightgbm, "_train_booster", fake_train)

    regression = _training_set(
        "heat-treatment-tradeoff-v1",
        "hardness_hv",
        "HV",
    )
    regression_recipe = estimator_recipe(
        "lightgbm-regression.v1",
        {"num_boost_round": 2},
    )
    regression_predictions, _ = lightgbm._honest_regression_evaluation(
        regression,
        regression_recipe,
        [0] * len(regression.feature_names),
    )
    held_out = regression.fold_ids == 0
    changed_y = regression.y.copy()
    changed_y[held_out] += 10_000
    changed_predictions, _ = lightgbm._honest_regression_evaluation(
        replace(regression, y=changed_y),
        regression_recipe,
        [0] * len(regression.feature_names),
    )
    np.testing.assert_allclose(
        changed_predictions[held_out],
        regression_predictions[held_out],
        rtol=0,
        atol=1e-12,
    )

    binary = _training_set(
        "secom-yield-risk-v1",
        "fail_probability",
        "1",
    )
    binary_recipe = estimator_recipe(
        "lightgbm-binary.v1",
        {"num_boost_round": 2},
    )
    _, calibrated = lightgbm._honest_binary_evaluation(binary, binary_recipe)
    binary_held_out = binary.fold_ids == 0
    changed_binary_y = binary.y.copy()
    changed_binary_y[binary_held_out] = 1 - changed_binary_y[binary_held_out]
    _, changed_calibrated = lightgbm._honest_binary_evaluation(
        replace(binary, y=changed_binary_y),
        binary_recipe,
    )
    np.testing.assert_allclose(
        changed_calibrated[binary_held_out],
        calibrated[binary_held_out],
        rtol=0,
        atol=1e-12,
    )


def test_lightgbm_auc_is_tie_aware_and_row_order_invariant() -> None:
    y = np.asarray([0.0, 1.0, 0.0, 1.0, 1.0, 0.0])
    all_tied = np.full(len(y), 0.25)

    assert lightgbm._auc(y, all_tied) == pytest.approx(0.5)

    probabilities = np.asarray([0.1, 0.5, 0.5, 0.9, 0.5, 0.1])
    expected = lightgbm._auc(y, probabilities)
    permutation = np.asarray([4, 0, 5, 2, 1, 3])
    assert lightgbm._auc(
        y[permutation],
        probabilities[permutation],
    ) == pytest.approx(expected)


def test_missing_pattern_metrics_follow_target_semantics() -> None:
    binary_name, binary_value = _target_point_error(
        np.asarray([0.0, 1.0]),
        np.asarray([0.2, 0.8]),
        "binary",
    )
    count_name, count_value = _target_point_error(
        np.asarray([0.0, 3.0]),
        np.asarray([0.5, 2.5]),
        "count",
    )

    assert binary_name == "brier_score"
    assert binary_value == pytest.approx(0.04)
    assert count_name == "mean_poisson_deviance"
    assert count_value >= 0


def test_model_workflow_builds_hot_rolling_gp_without_a_new_task_builder(
    tmp_path: Path,
) -> None:
    package = tmp_path / "hot-rolling-gp"
    dataset = tmp_path / "hot-rolling-feature-dataset.json"
    active_before = ACTIVE_PACKAGES_PATH.read_bytes()
    result = build_package(
        HOT_ROLLING_TASK,
        SOURCE,
        package,
        dataset,
        package_id="hot-rolling-configured-gp-test",
        package_version="1.0.0",
        replace=False,
        estimator="exact-gp-rbf.v1",
        estimator_options={"restarts": 1, "seed": 11},
    )

    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    recipe = json.loads(
        (package / "reference/training-recipe.json").read_text(encoding="utf-8")
    )
    assert result["package"]["task_id"] == HOT_ROLLING_TASK
    assert result["dataset"]["rows"] == 8
    assert manifest["predictors"][0]["runtime_type"] == "builtin.exact_gp.v1"
    assert manifest["predictors"][0]["feature_names"] == [
        item["name"]
        for item in json.loads(dataset.read_text(encoding="utf-8"))[
            "feature_pipeline"
        ]["features"]
    ]
    assert recipe["estimator"] == {
        "estimator_id": "exact-gp-rbf.v1",
        "restarts": 1,
        "folds": 5,
        "seed": 11,
        "max_rows": 500,
    }
    assert result["package"]["quality_report"]["targets"][0][
        "parent_conditions"
    ] == 6
    assert manifest["predictors"][0]["config"]["validation_method"] == (
        "5-fold grouped validation"
    )
    assert ACTIVE_PACKAGES_PATH.read_bytes() == active_before


def test_model_workflow_builds_tabular_ridge_through_the_same_assembler(
    tmp_path: Path,
) -> None:
    package = tmp_path / "heat-treatment-ridge"
    result = build_package(
        "heat-treatment-tradeoff-v1",
        HEAT_SOURCE,
        package,
        tmp_path / "heat-treatment-feature-dataset.json",
        package_id="heat-treatment-configured-ridge-test",
        package_version="1.0.0",
        replace=False,
        estimator="ridge.v1",
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    recipe = json.loads(
        (package / "reference/training-recipe.json").read_text(encoding="utf-8")
    )

    assert result["package"]["task_id"] == "heat-treatment-tradeoff-v1"
    assert {
        predictor["runtime_type"]
        for predictor in manifest["predictors"]
    } == {"builtin.linear.v1"}
    assert {
        predictor["config"]["training_method"]
        for predictor in manifest["predictors"]
    } == {"ridge.v1"}
    quality = result["package"]["quality_report"]
    for target, evidence in quality["validation_evidence"].items():
        assert target
        assert evidence["cohort_digest"].startswith("sha256:")
        assert evidence["fold_digest"].startswith("sha256:")
        assert evidence["validation_plan_digest"].startswith("sha256:")
        assert evidence["cohort_digest"] == recipe["evaluation"][target][
            "cohort_digest"
        ]
        assert evidence["fold_digest"] == recipe["evaluation"][target][
            "fold_digest"
        ]


def test_tabular_ridge_package_builds_with_fold_fitted_missing_policy(
    tmp_path: Path,
) -> None:
    profile_document = json.loads(
        (
            ROOT
            / "backend/src/decision_workbench/data"
            / "tabular-profile-heat-treatment-v1.json"
        ).read_text(encoding="utf-8")
    )
    profile_document["inputs"][0].update({
        "transform": "linear",
        "numeric_missing": {
            "strategy": "training_median_with_indicator",
        },
    })
    profile = tmp_path / "missing-profile.json"
    profile.write_text(
        json.dumps(profile_document, ensure_ascii=False),
        encoding="utf-8",
    )
    with HEAT_SOURCE.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
        fieldnames = list(rows[0])
    rows[0]["carbon_pct"] = ""
    source = tmp_path / "missing-source.csv"
    with source.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    package = tmp_path / "missing-package"
    build_package(
        "heat-treatment-tradeoff-v1",
        source,
        package,
        tmp_path / "missing-feature-dataset.json",
        package_id="heat-treatment-missing-policy-test",
        package_version="1.0.0",
        replace=False,
        estimator="ridge.v1",
        profile=profile,
    )

    pipeline = json.loads(
        (package / "feature-pipeline/pipeline.json").read_text(encoding="utf-8")
    )
    stats = json.loads(
        (package / "reference/training_stats.json").read_text(encoding="utf-8")
    )
    assert "composition.carbon_pct__missing" in {
        feature["name"] for feature in pipeline["features"]
    }
    assert stats["missing_policy"]["missing_by_input"][
        "composition.carbon_pct"
    ] == 1
    assert stats["missing_policy"]["digest"].startswith("sha256:")
    assert stats["missing_policy"]["policy_digest"].startswith("sha256:")
    assert stats["missing_policy"]["training_rows"] == len(rows)
    assert stats["missing_policy"]["pattern_support_policy"][
        "policy_id"
    ] == "evaluated-missing-pattern"
    assert stats["missing_policy"]["pattern_support_policy"][
        "policy_digest"
    ].startswith("sha256:")
    missing_pattern = next(
        item
        for item in stats["missing_policy"]["pattern_evidence"]
        if item["pattern"]
    )
    assert missing_pattern["pattern"] == [{
        "path": "composition.carbon_pct",
        "kind": "not_measured",
    }]
    assert missing_pattern["training_count"] == 1
    assert missing_pattern["evaluation_count"] == 1
    assert all(
        evidence["rmse"] >= 0
        and evidence["prediction_failure_rate"] == 0
        and "point_shift_vs_complete" in evidence
        and "rmse_delta_vs_complete" in evidence
        for evidence in missing_pattern["metrics_by_target"].values()
    )


def test_omitting_estimator_uses_the_task_default_training_recipe(
    tmp_path: Path,
) -> None:
    package = tmp_path / "heat-treatment-specialized"
    result = build_package(
        "heat-treatment-tradeoff-v1",
        HEAT_SOURCE,
        package,
        tmp_path / "heat-treatment-specialized-dataset.json",
        package_id="heat-treatment-specialized-test",
        package_version="1.0.0",
        replace=False,
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    assert result["package"]["task_id"] == "heat-treatment-tradeoff-v1"
    assert {
        predictor["runtime_type"]
        for predictor in manifest["predictors"]
    } == {"builtin.linear.v1"}
    recipe = json.loads(
        (package / "reference/training-recipe.json").read_text(encoding="utf-8")
    )
    assert recipe["estimator"]["estimator_id"] == "ridge.v1"
    assert (
        task_module("heat-treatment-tradeoff-v1").specialized_package_builder
        is None
    )


def test_legacy_profile_estimator_fields_do_not_select_new_training(
    tmp_path: Path,
) -> None:
    profile_document = json.loads(
        (
            ROOT
            / "backend/src/decision_workbench/data"
            / "tabular-profile-heat-treatment-v1.json"
        ).read_text(encoding="utf-8")
    )
    profile_document.update({
        "model_family": "ridge",
        "ridge_alpha": 321.0,
    })
    profile = tmp_path / "legacy-estimator-profile.json"
    profile.write_text(
        json.dumps(profile_document, ensure_ascii=False),
        encoding="utf-8",
    )
    package = tmp_path / "profile-independent-recipe"

    build_package(
        "heat-treatment-tradeoff-v1",
        HEAT_SOURCE,
        package,
        tmp_path / "profile-independent-feature-dataset.json",
        package_id="profile-independent-recipe-test",
        package_version="1.0.0",
        replace=False,
        profile=profile,
    )
    recipe = json.loads(
        (package / "reference/training-recipe.json").read_text(encoding="utf-8")
    )

    assert recipe["estimator"]["estimator_id"] == "ridge.v1"
    assert recipe["estimator"]["alpha"] == 1.0


def test_exact_gp_row_limit_fails_without_a_partial_package(
    tmp_path: Path,
) -> None:
    package = tmp_path / "too-large-gp"
    with pytest.raises(ValueError, match="recipe max_rows is 3"):
        build_package(
            HOT_ROLLING_TASK,
            SOURCE,
            package,
            tmp_path / "feature-dataset.json",
            package_id="too-large-gp",
            package_version="1.0.0",
            replace=False,
            estimator="exact-gp-rbf.v1",
            estimator_options={"max_rows": 3},
        )
    assert not package.exists()


def test_task_can_build_a_standard_baseline_without_matching_active_package_capabilities(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "feature-dataset.json"
    package = tmp_path / "ridge-baseline"
    build_package(
        HOT_ROLLING_TASK,
        SOURCE,
        package,
        dataset,
        package_id="hot-rolling-ridge-baseline",
        package_version="1.0.0",
        replace=False,
        estimator="ridge.v1",
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["predictors"][0]["config"]["training"]["estimator_id"] == "ridge.v1"
    assert manifest["predictors"][0]["predictive_family"] == "empirical_quantiles"
    assert dataset.is_file()


def test_estimator_inventory_exposes_only_compatible_task_choices() -> None:
    assert estimator_inventory(HOT_ROLLING_TASK)["tasks"] == {
        HOT_ROLLING_TASK: [
            "ridge.v1",
            "exact-gp-rbf.v1",
            "bayesian-additive-spline.v1",
            "lightgbm-regression.v1",
        ]
    }
    assert estimator_inventory("heat-treatment-tradeoff-v1")["tasks"] == {
        "heat-treatment-tradeoff-v1": [
            "ridge.v1",
            "exact-gp-rbf.v1",
            "bayesian-additive-spline.v1",
            "lightgbm-regression.v1",
        ]
    }
    assert estimator_inventory("battery-degradation-v1")["tasks"] == {
        "battery-degradation-v1": ["lightgbm-regression.v1"]
    }


def test_capability_validation_rejects_unavailable_standard_outputs() -> None:
    capability = load_task_contracts()[HOT_ROLLING_TASK].runtime_capability
    incompatible_target = capability.targets[0].model_copy(
        update={"samples": True}
    )
    incompatible = capability.model_copy(
        update={"targets": (incompatible_target,)}
    )
    with pytest.raises(ValueError, match="exact GP exposes no samples"):
        validate_recipe_capability(
            estimator_recipe("exact-gp-rbf.v1"),
            incompatible,
        )


@pytest.mark.parametrize(
    "task_id",
    BUNDLED_STANDARD_AUTHORING_TASK_IDS,
)
def test_every_standard_estimator_task_compiles_all_targets(task_id: str) -> None:
    module = task_module(task_id)
    source = resolve_task_source(task_id)
    data = module.data_loader(source)
    contract = load_task_contracts()[task_id]
    canonical = canonical_training_dataset(task_id, data, contract)
    positive_targets = module.standard_model_authoring.positive_targets  # type: ignore[union-attr]

    for output in contract.task_definition.outputs:
        training = compile_target_training_set(
            canonical,
            target=output.key,
            unit=output.unit,
            target_kind=(
                "continuous_positive"
                if output.key in positive_targets
                else "continuous"
            ),
        )
        assert len(training.y) >= 3
        assert len(training.validation_groups) == len(training.y)


def test_annealed_standard_gp_preserves_positive_lambda_target(
    tmp_path: Path,
) -> None:
    package = tmp_path / "annealed-gp"
    build_package(
        "annealed-properties-v1",
        resolve_task_source("annealed-properties-v1"),
        package,
        tmp_path / "annealed-feature-dataset.json",
        package_id="annealed-configured-gp-test",
        package_version="1.0.0",
        replace=False,
        estimator="exact-gp-rbf.v1",
        estimator_options={"restarts": 1, "seed": 11},
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    target_kinds = {
        predictor["target"]: predictor["target_kind"]
        for predictor in manifest["predictors"]
    }

    assert target_kinds["lambda"] == "continuous_positive"
    assert {
        target_kind
        for target, target_kind in target_kinds.items()
        if target != "lambda"
    } == {"continuous"}


def test_standard_lightgbm_binary_uses_the_allow_list_and_training_metadata(
    tmp_path: Path,
) -> None:
    task_id = "secom-yield-risk-v1"
    package = tmp_path / "secom-lightgbm"
    build_package(
        task_id,
        resolve_task_source(task_id),
        package,
        tmp_path / "secom-feature-dataset.json",
        package_id="secom-configured-lightgbm-test",
        package_version="1.0.0",
        replace=False,
        estimator="lightgbm-binary.v1",
        estimator_options={"num_boost_round": 2},
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    predictor = manifest["predictors"][0]

    assert predictor["runtime_type"] == "lightgbm.booster.v1"
    assert predictor["predictive_family"] == "bernoulli_logit"
    assert predictor["config"]["training"]["estimator_id"] == (
        "lightgbm-binary.v1"
    )
    assert predictor["config"]["training"]["validation"][
        "cohort_digest"
    ].startswith("sha256:")
    assert predictor["config"]["training"]["validation"][
        "fold_digest"
    ].startswith("sha256:")


def test_standard_lightgbm_regression_keeps_monotonicity_in_the_recipe(
    tmp_path: Path,
) -> None:
    task_id = "battery-degradation-v1"
    package = tmp_path / "battery-lightgbm"
    build_package(
        task_id,
        resolve_task_source(task_id),
        package,
        tmp_path / "battery-feature-dataset.json",
        package_id="battery-configured-lightgbm-test",
        package_version="1.0.0",
        replace=False,
        estimator="lightgbm-regression.v1",
        estimator_options={
            "num_boost_round": 2,
            "predictive_family": "normal",
            "monotone_decreasing_features": ["process.cycle_index"],
        },
    )
    recipe = json.loads(
        (package / "reference/training-recipe.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    assert recipe["estimator"]["monotone_decreasing_features"] == [
        "process.cycle_index"
    ]
    assert manifest["predictors"][0]["predictive_family"] == "normal"
    assert manifest["predictors"][0]["config"]["residual_std"] > 0


def test_battery_recipe_policy_rejects_dropping_scientific_monotonicity(
    tmp_path: Path,
) -> None:
    task_id = "battery-degradation-v1"
    with pytest.raises(ValueError, match="fixed by Task scientific constraints"):
        build_package(
            task_id,
            resolve_task_source(task_id),
            tmp_path / "battery-invalid",
            tmp_path / "battery-invalid-dataset.json",
            package_id="battery-invalid",
            package_version="1.0.0",
            replace=False,
            estimator="lightgbm-regression.v1",
            estimator_options={"monotone_decreasing_features": []},
        )


def test_battery_recipe_policy_rejects_unconstrained_standard_baseline(
    tmp_path: Path,
) -> None:
    task_id = "battery-degradation-v1"
    with pytest.raises(ValueError, match="outside the Task recipe policy"):
        build_package(
            task_id,
            resolve_task_source(task_id),
            tmp_path / "battery-ridge",
            tmp_path / "battery-ridge-dataset.json",
            package_id="battery-ridge",
            package_version="1.0.0",
            replace=False,
            estimator="ridge.v1",
        )


def test_standard_lightgbm_empirical_adapter_returns_fitted_interval(
    tmp_path: Path,
) -> None:
    task_id = "heat-treatment-tradeoff-v1"
    package = tmp_path / "heat-lightgbm"
    build_package(
        task_id,
        HEAT_SOURCE,
        package,
        tmp_path / "heat-feature-dataset.json",
        package_id="heat-configured-lightgbm-test",
        package_version="1.0.0",
        replace=False,
        estimator="lightgbm-regression.v1",
        estimator_options={"num_boost_round": 2},
    )
    loaded = ModelPackageLoader().load(package)
    spec = loaded.manifest.predictors[0]
    predictor = loaded.load_predictor(spec.id)
    summary = predictor.predict({
        feature_name: 0.0
        for feature_name in spec.feature_names
    })

    assert spec.predictive_family == "empirical_quantiles"
    assert set(summary.quantiles) == {"0.05", "0.50", "0.95"}
    assert summary.quantiles["0.05"] < summary.quantiles["0.95"]
    assert summary.quantiles["0.05"] == pytest.approx(
        summary.point_estimate + float(spec.config["lower_offset"])
    )
    assert summary.quantiles["0.95"] == pytest.approx(
        summary.point_estimate + float(spec.config["upper_offset"])
    )


def test_explicit_comparison_uses_one_feature_dataset_and_fold_plan(
    tmp_path: Path,
) -> None:
    active_before = ACTIVE_PACKAGES_PATH.read_bytes()
    result = compare_estimators(
        HOT_ROLLING_TASK,
        SOURCE,
        tmp_path / "comparison",
        tmp_path / "feature-dataset.json",
        estimators=(
            "ridge.v1",
            "bayesian-additive-spline.v1",
            "exact-gp-rbf.v1",
            "lightgbm-regression.v1",
        ),
        estimator_options={
            "bayesian-additive-spline.v1": {
                "min_unique_values_for_smooth": 4,
                "max_basis_per_feature": 5,
            },
            "exact-gp-rbf.v1": {"restarts": 1},
            "lightgbm-regression.v1": {"num_boost_round": 2},
        },
        package_prefix="hot-rolling-comparison",
        package_version="1.0.0",
    )

    assert result["selection"] is None
    assert len(result["models"]) == 4
    for target, identity in result["evaluation"].items():
        assert target
        assert identity["cohort_digest"].startswith("sha256:")
        assert identity["fold_digest"].startswith("sha256:")
        assert {
            model["evaluation"][target]["fold_digest"]
            for model in result["models"]
        } == {identity["fold_digest"]}
    assert ACTIVE_PACKAGES_PATH.read_bytes() == active_before


def test_model_workflow_builds_bayesian_additive_with_typed_intervals(
    tmp_path: Path,
) -> None:
    package = tmp_path / "heat-treatment-bayesian-additive"
    result = build_package(
        "heat-treatment-tradeoff-v1",
        HEAT_SOURCE,
        package,
        tmp_path / "heat-treatment-feature-dataset.json",
        package_id="heat-treatment-bayesian-additive-test",
        package_version="1.0.0",
        replace=False,
        estimator="bayesian-additive-spline.v1",
        estimator_options={
            "min_unique_values_for_smooth": 4,
            "max_basis_per_feature": 5,
        },
    )

    loaded = ModelPackageLoader().load(package)
    manifest = loaded.manifest
    assert result["package"]["task_id"] == "heat-treatment-tradeoff-v1"
    assert {
        predictor.runtime_type for predictor in manifest.predictors
    } == {"builtin.additive_terms.v1"}
    assert all(
        predictor.config["posterior"]["conditioning"]
        == "fixed_basis_fixed_smoothing_plugin_noise"
        for predictor in manifest.predictors
    )
    dataset = json.loads(
        (tmp_path / "heat-treatment-feature-dataset.json").read_text(
            encoding="utf-8"
        )
    )
    features = dataset["rows"][0]["features"]
    for predictor_spec in manifest.predictors:
        summary = loaded.load_predictor(predictor_spec.id).predict(
            {
                name: float(features[name])
                for name in predictor_spec.feature_names
            }
        )
        assert summary.prediction_interval is not None
        assert summary.prediction_interval.method == "bayesian"
        assert summary.latent_mean_credible_interval is not None
        assert summary.latent_mean_credible_interval.estimand == "latent_mean"

    module = task_module("heat-treatment-tradeoff-v1")
    runtime = module.runtime_factory(
        module.data_loader(HEAT_SOURCE, None),
        loaded,
    )
    contract = load_task_contracts()["heat-treatment-tradeoff-v1"]
    raw = contract.canonical_candidate
    now = datetime.now(UTC)
    candidate = Candidate(
        id="bayesian-additive-smoke",
        project_id="bayesian-additive-test",
        revision=1,
        created_at=now,
        updated_at=now,
        name="Bayesian additive smoke",
        inputs={
            "composition": dict(raw.composition),
            "process": dict(raw.process),
            "categorical": dict(raw.categorical),
            "heat_pattern": None,
        },
        provenance=raw.provenance,
    )
    prediction = runtime.predict(candidate)["predictions"]["hardness_hv"]
    assert prediction.latent_mean_credible_interval is not None
    variable = contract.task_definition.response_curve_variables[0].path
    assert variable is not None
    curve = runtime.response_curve_result(
        candidate,
        "hardness_hv",
        variable,
        9,
    )
    assert all(
        point["latent_mean_credible_interval"]["estimand"] == "latent_mean"
        for point in curve["points"]
    )
