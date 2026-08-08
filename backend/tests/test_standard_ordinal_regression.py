from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from decision_workbench.adapters.numpyro_posterior import NumpyroDensePosteriorAdapter
from decision_workbench.contracts.sampling_identity_contracts import SamplingRequest
from decision_workbench.modeling.packages.contracts import (
    PackageContractError,
    PredictorSpec,
)
from decision_workbench.modeling.training.estimators import ordered_logit
from decision_workbench.modeling.training.estimators.bayesian_linear import (
    _diagnostics_from_summary,
    _InferenceSettings,
)
from decision_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
)
from decision_workbench.modeling.training.readiness import (
    buildable_standard_estimator_ids,
    compatible_standard_estimator_ids,
    standard_estimator_catalog,
)
from decision_workbench.modeling.training.recipe import estimator_recipe

CATEGORIES = ("low", "medium", "high")


def _canonical(labels: tuple[str, ...]) -> dict[str, object]:
    return {
        "task_id": "ordinal-contract-test",
        "feature_pipeline": {
            "features": [{"name": "x"}],
            "missing_policy": {"imputation_values": {}},
        },
        "rows": [
            {
                "observation_id": f"obs-{index}",
                "condition_context_id": f"context-{index}",
                "parent_key": f"group-{index}",
                "features": {"x": float(index)},
                "outputs": {"grade": label},
            }
            for index, label in enumerate(labels)
        ],
    }


def _training_set():
    labels = tuple(CATEGORIES[index % 3] for index in range(30))
    return compile_target_training_set(
        _canonical(labels),
        target="grade",
        unit="1",
        target_kind="ordinal",
        ordinal_categories=CATEGORIES,
        folds=2,
        seed=791,
    )


def test_compiler_uses_task_order_and_records_fold_category_diagnostics() -> None:
    data = _training_set()

    assert data.ordinal_categories == CATEGORIES
    assert data.ordinal_category_order_digest.startswith("sha256:")
    assert set(data.y) == {0.0, 1.0, 2.0}
    diagnostics = data.validation_diagnostics["ordinal_category_diagnostics"]
    assert diagnostics["category_order"] == list(CATEGORIES)
    assert all(
        not item["missing_categories"] for item in diagnostics["training_cohorts"]
    )


def test_compiler_rejects_unknown_labels_and_missing_fold_category() -> None:
    with pytest.raises(ValueError, match="unknown ordinal categories: surprise"):
        compile_target_training_set(
            _canonical(("low", "medium", "high", "surprise", "low", "medium")),
            target="grade",
            unit="1",
            target_kind="ordinal",
            ordinal_categories=CATEGORIES,
            folds=2,
            seed=791,
        )

    labels = tuple(
        "high" if index == 0 else ("low", "medium")[index % 2] for index in range(12)
    )
    with pytest.raises(ValueError, match="missing Task categories: high"):
        compile_target_training_set(
            _canonical(labels),
            target="grade",
            unit="1",
            target_kind="ordinal",
            ordinal_categories=CATEGORIES,
            folds=2,
            seed=791,
        )


def test_recipe_is_experimental_buildable_but_not_production_compatible() -> None:
    recipe = estimator_recipe("ordered-logit.v1")
    entry = next(
        item
        for item in standard_estimator_catalog().entries
        if item.estimator_id == recipe.estimator_id
    )
    assert entry.adoption_status == "experimental"
    assert entry.required_dependency == "numpyro"
    assert "ordered-logit.v1" in buildable_standard_estimator_ids((_ordinal_output(),))
    assert "ordered-logit.v1" not in compatible_standard_estimator_ids(
        (_ordinal_output(),)
    )


def _ordinal_output():
    from decision_workbench.contracts.task_contracts import OutputDefinition

    return OutputDefinition.model_validate(
        {
            "key": "grade",
            "label": "Grade",
            "unit": "1",
            "target_kind": "ordinal",
            "ordinal": {"categories": list(CATEGORIES)},
            "goal_direction": "at_least",
            "plausibility_range": None,
            "preferred_display_range": None,
        }
    )


class _Artifacts:
    def __init__(self, path: Path) -> None:
        self.path = path

    def artifact_path(self, relative_path: str) -> Path:
        assert Path(relative_path).name == self.path.name
        return self.path


def _predictor() -> PredictorSpec:
    return PredictorSpec.model_validate(
        {
            "id": "grade-ordered-logit",
            "target": "grade",
            "unit": "1",
            "target_kind": "ordinal",
            "runtime_type": "numpyro.dense_posterior.v1",
            "architecture_id": "dense_mlp_v1",
            "artifact": "posterior.npz",
            "predictive_family": "ordinal_logit",
            "feature_names": ["x"],
            "config": {"categories": list(CATEGORIES), "thresholds": [-0.5, 0.5]},
        }
    )


def test_runtime_uses_posterior_threshold_draws_and_rejects_unordered(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "posterior.npz"
    np.savez(
        artifact,
        w0=np.asarray([[[1.0]], [[1.2]], [[0.8]]]),
        b0=np.zeros((3, 1)),
        ordinal_thresholds=np.asarray([[-1.0, 0.5], [-0.8, 0.7], [-1.2, 0.4]]),
    )
    loaded = NumpyroDensePosteriorAdapter().load(_Artifacts(artifact), _predictor())
    summary = loaded.predict(
        {"x": 0.2},
        sampling_request=SamplingRequest.create(
            operation="package_verification",
            policy_id="ordinal-regression-test/v1",
            seed=791,
            requested_sample_count=3,
        ),
    )
    assert summary.distribution["categories"] == list(CATEGORIES)
    assert 0.0 <= summary.point_estimate <= 2.0

    np.savez(
        artifact,
        w0=np.asarray([[[1.0]], [[1.2]]]),
        b0=np.zeros((2, 1)),
        ordinal_thresholds=np.asarray([[0.5, -1.0], [-0.8, 0.7]]),
    )
    with pytest.raises(PackageContractError, match="threshold draws"):
        NumpyroDensePosteriorAdapter().load(_Artifacts(artifact), _predictor())


def test_missing_numpyro_is_typed_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = __import__

    def missing(name: str, *args, **kwargs):
        if name == "jax.numpy":
            raise ImportError("missing for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", missing)
    with pytest.raises(
        ordered_logit.OrderedLogitTrainingUnavailableError,
        match="no continuous, nominal, or other estimator fallback",
    ):
        ordered_logit._dependencies()


def test_finite_sample_rhat_below_one_is_persisted_at_theoretical_bound() -> None:
    diagnostics = _diagnostics_from_summary(
        {"coefficient": {"r_hat": np.asarray([0.997]), "n_eff": np.asarray([100.0])}},
        0,
        _InferenceSettings(
            chains=2,
            warmup=256,
            draws=256,
            max_r_hat=1.05,
            min_ess=50.0,
            max_divergences=0,
        ),
    )
    assert diagnostics.status == "passed"
    assert diagnostics.max_r_hat == 1.0


def test_real_same_cohort_ordered_logit_beats_fold_local_frequency_baseline(
    tmp_path: Path,
) -> None:
    pytest.importorskip("numpyro")
    rng = np.random.default_rng(791)
    x = np.linspace(-2.5, 2.5, 48)
    latent = x + rng.normal(0.0, 0.35, len(x))
    labels = tuple(CATEGORIES[index] for index in np.digitize(latent, [-0.65, 0.65]))
    canonical = _canonical(labels)
    for index, row in enumerate(canonical["rows"]):
        row["features"]["x"] = float(x[index])
    data = compile_target_training_set(
        canonical,
        target="grade",
        unit="1",
        target_kind="ordinal",
        ordinal_categories=CATEGORIES,
        folds=2,
        seed=791,
    )
    recipe = estimator_recipe("ordered-logit.v1", {"folds": 2, "seed": 791})
    trained = ordered_logit.train(data, recipe, tmp_path / "ordered-logit.npz")
    observed = data.y.astype(int)
    assert trained.evaluation_predictions is not None
    expected = trained.evaluation_predictions
    current_mae = float(np.mean(np.abs(data.y - expected)))
    current_rps = trained.diagnostics["ordinal_quality"]["ranked_probability_score"]
    baseline = np.zeros((len(data.y), 3), dtype=float)
    for fold in range(data.folds):
        train_rows = data.fold_ids != fold
        evaluation_rows = data.fold_ids == fold
        rates = np.bincount(observed[train_rows], minlength=3) / train_rows.sum()
        baseline[evaluation_rows] = rates
    baseline_expected = baseline @ np.arange(3)
    baseline_mae = float(np.mean(np.abs(data.y - baseline_expected)))
    baseline_rps = float(
        np.mean(
            np.sum(
                (
                    np.cumsum(baseline[:, :-1], axis=1)
                    - (observed[:, None] <= np.arange(2))
                )
                ** 2,
                axis=1,
            )
        )
    )
    assert current_mae < baseline_mae
    assert current_rps < baseline_rps
    assert all(
        item["inference_identity"]["diagnostics"]["status"] == "passed"
        for item in trained.diagnostics["fold_inference"]
    )
    assert (
        trained.predictor["config"]["category_order_digest"]
        == data.ordinal_category_order_digest
    )
    assert (
        trained.predictor["config"]["threshold_draws"] == "artifact:ordinal_thresholds"
    )
    identity = trained.predictor["inference_identity"]
    assert identity["seed"] == 1_000_791
    assert identity["resource_limits"]["chain_method"] == "sequential"
    assert identity["diagnostics"]["divergence_count"] == 0
    predictor_payload = dict(trained.predictor)
    predictor_payload["artifact"] = "ordered-logit.npz"
    loaded = NumpyroDensePosteriorAdapter().load(
        _Artifacts(trained.artifact),
        PredictorSpec.model_validate(predictor_payload),
    )
    summary = loaded.predict(
        {"x": 0.0},
        sampling_request=SamplingRequest.create(
            operation="package_verification",
            policy_id="issue-791-real-artifact/v1",
            seed=791,
            requested_sample_count=128,
        ),
    )
    assert summary.distribution["categories"] == list(CATEGORIES)
