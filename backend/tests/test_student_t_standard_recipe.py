from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from decision_workbench.modeling.training.estimators import ridge
from decision_workbench.modeling.training.estimators import student_t_linear
from decision_workbench.modeling.training.feature_dataset import TargetTrainingSet
from decision_workbench.modeling.training.readiness import (
    EstimatorReadinessContext,
    compatible_standard_estimator_ids,
    resolve_estimator_readiness,
    standard_estimator_catalog,
)
from decision_workbench.modeling.training.recipe import estimator_recipe
from decision_workbench.modeling.training.validation_plan import (
    grouped_kfold_plan,
)
from decision_workbench.contracts.task_contracts import OutputDefinition


def _training_set(
    *,
    family: str,
    seed: int = 793,
) -> tuple[TargetTrainingSet, np.ndarray]:
    rng = np.random.default_rng(seed)
    rows = 30
    values = rng.normal(size=(rows, 3))
    signal = 1.5 + 2.2 * values[:, 0] - 0.8 * values[:, 1]
    if family == "normal":
        target = signal + rng.normal(0.0, 0.25, rows)
        large_residual_rows = np.asarray([], dtype=int)
    elif family == "student_t":
        target = signal + rng.standard_t(3.5, rows) * 0.22
        large_residual_rows = np.asarray([2, 11, 20])
        target[large_residual_rows] += np.asarray([4.5, -5.0, 4.0])
    else:
        raise ValueError(f"unsupported family: {family}")
    groups = tuple(f"group-{index:02d}" for index in range(rows))
    fold_ids = np.arange(rows, dtype=int) % 3
    plan = grouped_kfold_plan(folds=3, seed=seed)
    training = TargetTrainingSet(
        target="response",
        unit="a.u.",
        target_kind="continuous",
        feature_names=("x0", "x1", "x2"),
        x=values,
        y=target,
        replicate_contexts=groups,
        validation_groups=groups,
        observation_ids=tuple((f"obs-{index:02d}",) for index in range(rows)),
        repeat_counts=tuple(1 for _ in range(rows)),
        within_context_sse=np.zeros(rows),
        within_context_df=np.zeros(rows),
        observation_variance=0.0,
        cohort_digest=f"sha256:{'1' * 64}",
        fold_assignments=tuple(
            (group, int(fold))
            for group, fold in zip(groups, fold_ids, strict=True)
        ),
        fold_ids=fold_ids,
        fold_digest=f"sha256:{'2' * 64}",
        folds=3,
        validation_plan=plan,
        validation_plan_digest=f"sha256:{'3' * 64}",
        validation_diagnostics={},
    )
    return training, large_residual_rows


def _student_recipe() -> Any:
    return estimator_recipe(
        "student-t-linear-regression.v1",
        {
            "folds": 3,
            "seed": 793,
        },
    )


def test_student_t_recipe_is_bounded_optional_and_explicitly_available() -> None:
    recipe = _student_recipe()
    assert recipe.inference_preset == "standard-evidence"
    assert recipe.df_policy == "bounded-beta-2-5-on-2p1-30"
    with pytest.raises(ValueError, match="standard-evidence"):
        estimator_recipe(
            recipe.estimator_id,
            {"inference_preset": "quick-evidence"},
        )
    entry = next(
        item
        for item in standard_estimator_catalog().entries
        if item.estimator_id == recipe.estimator_id
    )
    assert entry.runtime_type == "numpyro.dense_posterior.v1"
    assert entry.artifact_format == "bounded-npz"
    assert entry.required_dependency == "numpyro"
    assert entry.adoption_status == "production"
    assert {
        "mean_log_predictive_density",
        "interval_coverage_90",
        "mean_interval_width",
        "posterior_convergence",
    }.issubset(entry.quality_metrics)
    output = OutputDefinition(
        key="response",
        label="response",
        unit="a.u.",
        target_kind="continuous",
        goal_direction="at_least",
        plausibility_range={"min": -100, "max": 100},
        preferred_display_range={"min": -10, "max": 10},
    )
    assert recipe.estimator_id in compatible_standard_estimator_ids((output,))
    unavailable = resolve_estimator_readiness(
        EstimatorReadinessContext(
            estimator_id=recipe.estimator_id,
            target_kind="continuous",
            row_count=30,
            independent_group_count=30,
            feature_count=3,
            target_contract="ready",
            validation_plan="ready",
            validation_strategy="grouped_kfold",
            feature_recipe="ready",
        ),
        available_dependencies=frozenset(),
    )
    assert unavailable.status == "unavailable_missing_dependency"
    assert "no alternative estimator was selected" in unavailable.reasons[0]


def test_student_t_outer_fold_does_not_observe_held_out_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, _ = _training_set(family="student_t")
    recipe = _student_recipe()
    fitted_targets: list[np.ndarray] = []

    def fake_fit(
        values: np.ndarray,
        target: np.ndarray,
        *_: object,
        **__: object,
    ) -> object:
        fitted_targets.append(target.copy())
        return SimpleNamespace(
            mean=float(np.mean(target)),
            inference_identity=SimpleNamespace(
                identity_digest=f"sha256:{'4' * 64}",
                diagnostics=SimpleNamespace(
                    max_r_hat=1.0,
                    min_effective_sample_size=100.0,
                    divergence_count=0,
                ),
            ),
        )

    def fake_evaluate(
        fitted: SimpleNamespace,
        values: np.ndarray,
        observed: np.ndarray,
        *_: object,
        **__: object,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        point = np.full(len(values), fitted.mean)
        return (
            point,
            point - 1.0,
            point + 1.0,
            np.full(len(observed), -1.0),
        )

    monkeypatch.setattr(student_t_linear, "_fit", fake_fit)
    monkeypatch.setattr(student_t_linear, "_evaluate", fake_evaluate)
    baseline = student_t_linear._honest_predictions(data, recipe)[0]
    held_out = data.fold_ids == 0
    changed_target = data.y.copy()
    changed_target[held_out] += 10_000
    changed = student_t_linear._honest_predictions(
        replace(data, y=changed_target),
        recipe,
    )[0]
    np.testing.assert_allclose(changed[held_out], baseline[held_out])
    assert all(len(target) == 20 for target in fitted_targets)


def test_student_t_rejects_malformed_values_before_robust_fit() -> None:
    data, _ = _training_set(family="student_t")
    malformed = data.y.copy()
    malformed[0] = np.nan
    with pytest.raises(ValueError, match="does not repair"):
        student_t_linear._fit(
            data.x,
            malformed,
            _student_recipe(),
            seed=793,
        )


def test_student_t_heavy_tail_gain_and_normal_efficiency_loss_are_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("numpyro")
    monkeypatch.setattr(
        student_t_linear,
        "_settings",
        lambda _: student_t_linear._InferenceSettings(
            chains=2,
            warmup=96,
            draws=96,
            max_r_hat=1.15,
            min_ess=20.0,
            max_divergences=0,
        ),
    )
    recipe = _student_recipe()
    ridge_recipe = estimator_recipe(
        "ridge.v1",
        {"alpha": 0.01, "folds": 3, "seed": 793},
    )
    normal, _ = _training_set(family="normal")
    heavy, large_residual_rows = _training_set(family="student_t")
    normal_student = student_t_linear.train(
        normal,
        recipe,
        tmp_path / "normal-student-t.npz",
    )
    normal_ridge = ridge.train(
        normal,
        ridge_recipe,
        tmp_path / "normal-ridge.npz",
    )
    heavy_student = student_t_linear.train(
        heavy,
        recipe,
        tmp_path / "heavy-student-t.npz",
    )
    heavy_ridge = ridge.train(
        heavy,
        ridge_recipe,
        tmp_path / "heavy-ridge.npz",
    )
    assert normal_student.quality.mae <= normal_ridge.quality.mae * 1.35
    normal_efficiency_loss = (
        normal_student.quality.mae / normal_ridge.quality.mae - 1.0
    )
    assert normal_efficiency_loss <= 0.35
    clean_rows = np.ones(len(heavy.y), dtype=bool)
    clean_rows[large_residual_rows] = False
    assert heavy_student.evaluation_predictions is not None
    assert heavy_ridge.evaluation_predictions is not None
    student_clean_mae = float(np.mean(np.abs(
        heavy.y[clean_rows]
        - heavy_student.evaluation_predictions[clean_rows]
    )))
    ridge_clean_mae = float(np.mean(np.abs(
        heavy.y[clean_rows]
        - heavy_ridge.evaluation_predictions[clean_rows]
    )))
    assert student_clean_mae < ridge_clean_mae
    assert heavy_student.quality.mean_log_predictive_density is not None
    assert np.isfinite(heavy_student.quality.mean_log_predictive_density)
    assert heavy_student.quality.mean_interval_width is not None
    assert heavy_student.quality.mean_interval_width > 0
    with np.load(tmp_path / "heavy-student-t.npz", allow_pickle=False) as arrays:
        assert set(arrays.files) == {"w0", "b0", "obs_scale", "df"}
        assert np.all(arrays["obs_scale"] > 0)
        assert np.all(arrays["df"] > 2)
        assert np.all(arrays["df"] <= 30)
    identity = heavy_student.predictor["inference_identity"]
    assert identity["algorithm_id"] == "nuts"
    assert identity["diagnostics"]["status"] == "passed"
    assert identity["fallback_policy"] == "forbid_implicit_switch"
