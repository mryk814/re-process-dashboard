import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("sklearn") is None
    or importlib.util.find_spec("skops") is None,
    reason="requires the runtime-sklearn extra",
)

from decision_workbench.adapters.sklearn_skops import SklearnSkopsAdapter
from decision_workbench.modeling.packages.contracts import PredictorSpec
from decision_workbench.modeling.training.estimators import estimator_trainer
from decision_workbench.modeling.training.feature_dataset import TargetTrainingSet
from decision_workbench.modeling.training.recipe import estimator_recipe
from decision_workbench.modeling.training.validation_plan import ValidationPlan


class _Artifacts:
    def __init__(self, root: Path) -> None:
        self.root = root

    def artifact_path(self, relative_path: str) -> Path:
        return self.root / relative_path


def _load_trained_artifact(result: object, root: Path) -> object:
    predictor = dict(result.predictor)  # type: ignore[attr-defined]
    predictor["artifact"] = result.artifact.name  # type: ignore[attr-defined]
    return SklearnSkopsAdapter().load(
        _Artifacts(root),
        PredictorSpec.model_validate(predictor),
    )


def _training_set(target_kind: str, y: np.ndarray) -> TargetTrainingSet:
    rows = len(y)
    feature = np.linspace(-1.0, 1.0, rows)
    x = np.column_stack((feature, feature**2))
    fold_ids = np.arange(rows, dtype=int) % 3
    plan = ValidationPlan(
        strategy=(
            "stratified_grouped_kfold"
            if target_kind == "binary"
            else "grouped_kfold"
        ),
        folds=3,
        group_key="parent_key",
        class_balance_policy=(
            "require_each_training_fold"
            if target_kind == "binary"
            else None
        ),
    )
    return TargetTrainingSet(
        target="event" if target_kind == "binary" else "defects",
        unit="1" if target_kind == "binary" else "個",
        target_kind=target_kind,
        feature_names=("x", "x_squared"),
        x=x,
        y=y.astype(float),
        replicate_contexts=tuple(f"context-{index}" for index in range(rows)),
        validation_groups=tuple(f"group-{index}" for index in range(rows)),
        observation_ids=tuple((f"observation-{index}",) for index in range(rows)),
        repeat_counts=(1,) * rows,
        within_context_sse=np.zeros(rows),
        within_context_df=np.zeros(rows, dtype=int),
        observation_variance=0.1,
        cohort_digest="sha256:cohort",
        fold_assignments=tuple(
            (f"group-{index}", int(fold_ids[index]))
            for index in range(rows)
        ),
        fold_ids=fold_ids,
        fold_digest="sha256:fold",
        folds=3,
        validation_plan=plan,
        validation_plan_digest="sha256:plan",
        validation_diagnostics={},
    )


def test_logistic_trainer_builds_a_safe_probability_artifact(
    tmp_path: Path,
) -> None:
    data = _training_set(
        "binary",
        np.array([0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1]),
    )
    recipe = estimator_recipe("logistic.v1")
    result = estimator_trainer(recipe.estimator_id)(
        data,
        recipe,
        tmp_path / "event.skops",
    )

    assert result.artifact.is_file()
    assert result.predictor["runtime_type"] == "sklearn.skops.v1"
    assert result.predictor["predictive_family"] == "bernoulli_logit"
    assert result.predictor["config"]["estimator_family"] == (
        "logistic_regression_v1"
    )
    assert 0 <= result.predict(np.array([0.0, 0.0])) <= 1
    assert 0 <= result.diagnostics["brier_score"] <= 1
    assert result.diagnostics["calibration"] == "intrinsic-logistic-link"
    loaded = _load_trained_artifact(result, tmp_path)
    summary = loaded.predict({"x": 0.0, "x_squared": 0.0})
    assert summary.target_kind == "binary"
    assert 0 <= summary.event_probability <= 1


def test_poisson_trainer_builds_a_safe_nonnegative_rate_artifact(
    tmp_path: Path,
) -> None:
    data = _training_set(
        "count",
        np.array([0, 1, 0, 2, 1, 3, 2, 4, 3, 5, 4, 6]),
    )
    recipe = estimator_recipe("poisson.v1")
    result = estimator_trainer(recipe.estimator_id)(
        data,
        recipe,
        tmp_path / "defects.skops",
    )

    assert result.artifact.is_file()
    assert result.predictor["runtime_type"] == "sklearn.skops.v1"
    assert result.predictor["predictive_family"] == "poisson_log"
    assert result.predictor["config"]["estimator_family"] == (
        "poisson_regression_v1"
    )
    assert result.predict(np.array([0.0, 0.0])) >= 0
    assert result.diagnostics["minimum_oof_rate"] >= 0
    assert result.diagnostics["mean_poisson_deviance"] >= 0
    assert 0 <= result.quality.interval_coverage_90 <= 1
    assert result.diagnostics["interval_coverage_90"] == (
        result.quality.interval_coverage_90
    )
    assert result.diagnostics["interval_observations"] == len(data.y)
    loaded = _load_trained_artifact(result, tmp_path)
    summary = loaded.predict({"x": 0.0, "x_squared": 0.0})
    assert summary.target_kind == "count"
    assert summary.distribution["support"] == "nonnegative_integers"


def test_poisson_trainer_rejects_negative_or_fractional_counts(
    tmp_path: Path,
) -> None:
    data = _training_set(
        "count",
        np.array([0, 1, 0, 2, 1, 3, 2, 4, 3, 5, 4, -1]),
    )
    recipe = estimator_recipe("poisson.v1")

    with pytest.raises(ValueError, match="nonnegative integer"):
        estimator_trainer(recipe.estimator_id)(
            data,
            recipe,
            tmp_path / "invalid.skops",
        )
    assert not (tmp_path / "invalid.skops").exists()
