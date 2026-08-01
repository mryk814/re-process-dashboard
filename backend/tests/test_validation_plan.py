from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from decision_workbench.modeling.training.validation_plan import (
    ValidationPlan,
    build_validation_assignment,
)
from decision_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
)
from decision_workbench.modeling.training.estimators.types import (
    standard_training_metadata,
)
from decision_workbench.modeling.training.estimators import lightgbm, ridge
from dataclasses import replace
from pathlib import Path


def _plan(strategy: str, **values: object) -> ValidationPlan:
    return ValidationPlan.model_validate({"strategy": strategy, **values})


def test_validation_plan_is_typed_and_rejects_incoherent_roles() -> None:
    with pytest.raises(ValidationError, match="grouped plans require group_key"):
        _plan("grouped_kfold", folds=3)
    with pytest.raises(ValidationError, match="temporal plans require time_key"):
        _plan("temporal_holdout", holdout_fraction=0.2)
    with pytest.raises(ValidationError, match="stratified plans require"):
        _plan("stratified_kfold", folds=3)


def test_legacy_default_fold_digest_is_unchanged_and_plan_is_additive() -> None:
    canonical = {
        "feature_pipeline": {"features": [{"name": "x"}]},
        "rows": [
            {
                "observation_id": f"o{index}",
                "parent_key": f"g{index}",
                "features": {"x": index},
                "outputs": {"y": index},
            }
            for index in range(5)
        ],
    }
    data = compile_target_training_set(canonical, target="y", unit="1")

    assert data.fold_digest == (
        "sha256:e46b5a73f9e436185d9b5577224a607f"
        "72b7ae74c41a233297109083c3be0df8"
    )
    metadata = standard_training_metadata(
        data,
        estimator_id="ridge.v1",
        uncertainty="fixture",
        parameters={},
    )
    assert metadata["validation"]["method"] == "grouped_kfold"
    assert metadata["validation"]["fold_digest"] == data.fold_digest
    assert metadata["validation"]["plan_digest"] == data.validation_plan_digest


def test_grouped_kfold_never_crosses_groups_and_does_not_reduce_folds() -> None:
    plan = _plan(
        "grouped_kfold",
        folds=3,
        seed=7,
        group_key="parent_key",
    )
    result = build_validation_assignment(
        target="strength",
        keys=("A", "A", "B", "B", "C", "C"),
        labels=np.arange(6, dtype=float),
        plan=plan,
    )

    assert result.folds == 3
    assert result.diagnostics["group_overlap"] is False
    for group in ("A", "B", "C"):
        indexes = [
            index
            for index, value in enumerate(("A", "A", "B", "B", "C", "C"))
            if value == group
        ]
        assert len(set(result.fold_ids[indexes])) == 1

    with pytest.raises(ValueError, match="requested 4 folds"):
        build_validation_assignment(
            target="strength",
            keys=("A", "B", "C"),
            labels=np.arange(3, dtype=float),
            plan=plan.model_copy(update={"folds": 4}),
        )


def test_stratified_row_and_grouped_plans_preflight_every_training_fold() -> None:
    labels = np.asarray([0, 0, 0, 1, 1, 1], dtype=float)
    row_plan = _plan(
        "stratified_kfold",
        folds=3,
        seed=11,
        class_balance_policy="require_each_training_fold",
    )
    row_result = build_validation_assignment(
        target="failure",
        keys=tuple(f"row-{index}" for index in range(6)),
        labels=labels,
        plan=row_plan,
    )
    for fold in range(row_result.folds):
        assert set(labels[row_result.fold_ids != fold]) == {0.0, 1.0}
        assert row_result.diagnostics["folds"][fold]["class_ratio"] == pytest.approx(
            0.5
        )

    grouped_plan = _plan(
        "stratified_grouped_kfold",
        folds=3,
        seed=11,
        group_key="parent_key",
        class_balance_policy="require_each_training_fold",
    )
    grouped_labels = np.asarray([0, 1, 0, 1, 0, 1], dtype=float)
    grouped_result = build_validation_assignment(
        target="failure",
        keys=("A", "A", "B", "B", "C", "C"),
        labels=grouped_labels,
        plan=grouped_plan,
    )
    for group in ("A", "B", "C"):
        group_folds = {
            int(grouped_result.fold_ids[index])
            for index, value in enumerate(("A", "A", "B", "B", "C", "C"))
            if value == group
        }
        assert len(group_folds) == 1
    for fold in range(grouped_result.folds):
        assert set(grouped_labels[grouped_result.fold_ids != fold]) == {0.0, 1.0}


def test_temporal_holdout_keeps_future_out_of_training_and_honors_gap() -> None:
    plan = _plan(
        "temporal_holdout",
        holdout_fraction=0.25,
        time_key="process.cycle",
        gap=1,
        minimum_train_size=3,
    )
    result = build_validation_assignment(
        target="capacity",
        keys=tuple(f"row-{index}" for index in range(8)),
        labels=np.arange(8, dtype=float),
        times=(7, 0, 6, 1, 5, 2, 4, 3),
        plan=plan,
    )
    evaluation_indexes = np.flatnonzero(result.fold_ids == 0)
    assert set(evaluation_indexes) == {0, 2}
    assert max(
        time
        for index, time in enumerate((7, 0, 6, 1, 5, 2, 4, 3))
        if result.fold_ids[index] == -1
    ) < min((7, 6))
    assert set(result.fold_ids) == {-3, -2, -1, 0}
    assert result.diagnostics["temporal_order_verified"] is True


def test_grouped_temporal_rejects_group_and_time_leakage_together() -> None:
    plan = _plan(
        "grouped_temporal",
        holdout_fraction=0.25,
        time_key="process.cycle",
        group_key="parent_key",
        minimum_train_size=3,
    )
    safe = build_validation_assignment(
        target="capacity",
        keys=("A", "A", "B", "B", "C", "C", "D", "D"),
        labels=np.arange(8, dtype=float),
        times=tuple(range(8)),
        plan=plan,
    )
    assert set(safe.fold_ids[-2:]) == {0}
    assert set(safe.fold_ids[:-2]) == {-3, -1}

    with pytest.raises(ValueError, match="crosses groups"):
        build_validation_assignment(
            target="capacity",
            keys=("A", "A", "B", "B", "C", "D", "A", "A"),
            labels=np.arange(8, dtype=float),
            times=tuple(range(8)),
            plan=plan,
        )


def test_temporal_ridge_preprocessing_and_calibration_do_not_observe_holdout() -> None:
    canonical = {
        "feature_pipeline": {"features": [{"name": "time"}]},
        "rows": [
            {
                "observation_id": f"o{index}",
                "parent_key": f"g{index}",
                "features": {"time": index},
                "outputs": {"y": 2 * index + 1},
            }
            for index in range(12)
        ],
    }
    plan = _plan(
        "temporal_holdout",
        holdout_fraction=0.25,
        time_key="time",
        minimum_train_size=4,
    )
    data = compile_target_training_set(
        canonical,
        target="y",
        unit="1",
        validation_plan=plan,
    )
    predictions, _ = ridge._honest_grouped_evaluation(data, alpha=1.0)
    changed_y = data.y.copy()
    changed_y[data.quality_rows] += 10_000
    changed_predictions, _ = ridge._honest_grouped_evaluation(
        replace(data, y=changed_y),
        alpha=1.0,
    )

    np.testing.assert_allclose(
        changed_predictions[data.quality_rows],
        predictions[data.quality_rows],
        rtol=0,
        atol=1e-12,
    )


def test_temporal_holdout_labels_cannot_change_ridge_interval_artifact(
    tmp_path: Path,
) -> None:
    canonical = {
        "feature_pipeline": {"features": [{"name": "time"}]},
        "rows": [
            {
                "observation_id": f"o{index}",
                "parent_key": f"g{index}",
                "features": {"time": index},
                "outputs": {"y": 2 * index + 1},
            }
            for index in range(12)
        ],
    }
    plan = _plan(
        "temporal_holdout",
        holdout_fraction=0.25,
        time_key="time",
        minimum_train_size=4,
    )
    data = compile_target_training_set(
        canonical,
        target="y",
        unit="1",
        validation_plan=plan,
    )
    recipe = ridge.RidgeEstimatorRecipe(validation_plan=plan)
    first = ridge.train(data, recipe, tmp_path / "first.npz")
    changed_y = data.y.copy()
    changed_y[data.quality_rows] += 10_000
    changed = ridge.train(
        replace(data, y=changed_y),
        recipe,
        tmp_path / "changed.npz",
    )

    with np.load(first.artifact) as first_artifact, np.load(
        changed.artifact
    ) as changed_artifact:
        assert float(first_artifact["lower_offset"]) == pytest.approx(
            float(changed_artifact["lower_offset"])
        )
        assert float(first_artifact["upper_offset"]) == pytest.approx(
            float(changed_artifact["upper_offset"])
        )


class _SavedMeanBooster:
    def __init__(self, y: np.ndarray) -> None:
        self.value = float(np.mean(y))

    def predict(self, x: np.ndarray, **_: object) -> np.ndarray:
        return np.full(len(x), self.value, dtype=float)

    def save_model(self, path: str) -> None:
        Path(path).write_text(str(self.value), encoding="utf-8")


def test_temporal_holdout_labels_cannot_change_lightgbm_interval_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        lightgbm,
        "_train_booster",
        lambda _x, y, **_options: _SavedMeanBooster(y),
    )
    canonical = {
        "feature_pipeline": {"features": [{"name": "time"}]},
        "rows": [
            {
                "observation_id": f"o{index}",
                "parent_key": f"g{index}",
                "features": {"time": index},
                "outputs": {"y": 2 * index + 1},
            }
            for index in range(12)
        ],
    }
    plan = _plan(
        "temporal_holdout",
        holdout_fraction=0.25,
        time_key="time",
        minimum_train_size=4,
    )
    data = compile_target_training_set(
        canonical,
        target="y",
        unit="1",
        validation_plan=plan,
    )
    recipe = lightgbm.LightGBMRegressionEstimatorRecipe(
        num_boost_round=2,
        validation_plan=plan,
    )
    first = lightgbm.train(data, recipe, tmp_path / "first.txt")
    changed_y = data.y.copy()
    changed_y[data.quality_rows] += 10_000
    changed = lightgbm.train(
        replace(data, y=changed_y),
        recipe,
        tmp_path / "changed.txt",
    )

    for key in ("lower_offset", "upper_offset", "residual_std"):
        assert first.predictor["config"][key] == pytest.approx(
            changed.predictor["config"][key]
        )
