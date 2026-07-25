from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from material_workbench.modeling import tabular_model_builder as builder
from material_workbench.modeling.model_lifecycle import TargetQualityMetric
from material_workbench.modeling.tabular_regression import TabularDatasetProfile


ROOT = Path(__file__).resolve().parents[2]


def test_cross_fit_masks_keep_calibration_and_evaluation_disjoint() -> None:
    fold_ids = np.asarray([0, 1, 2, 0, 1, 2])

    for calibrate, evaluate in builder._cross_fit_masks(fold_ids):
        assert not np.any(calibrate & evaluate)
        assert np.all(calibrate | evaluate)
        assert np.any(calibrate)
        assert np.any(evaluate)


def test_cross_fitted_quantile_coverage_is_not_self_calibrated() -> None:
    residuals = np.asarray([
        0.346, 0.822, 0.330, -1.303, 0.905,
        0.446, -0.537, 0.581, 0.365, 0.294,
        0.028, 0.547, -0.736, -0.163, -0.482,
        0.599, 0.040, -0.292, -0.782, -0.257,
    ])
    fold_ids = np.arange(len(residuals)) % 5
    lower, upper = np.quantile(residuals, (0.05, 0.95))
    self_calibrated = float(np.mean((residuals >= lower) & (residuals <= upper)))

    cross_fitted = builder._cross_fitted_quantile_coverage(residuals, fold_ids)

    assert cross_fitted != self_calibrated


class _FakeDataset:
    def __init__(self, data, *, label, **_kwargs) -> None:
        self.data = np.asarray(data)
        self.label = np.asarray(label)


class _FakeBooster:
    def predict(self, values, *, num_iteration):
        assert num_iteration > 0
        raw = np.asarray(values)[:, 0]
        return np.clip(raw / 100, 1e-4, 1 - 1e-4)


def _fake_lightgbm(train_calls: list[dict[str, object]]) -> SimpleNamespace:
    def train(_parameters, dataset, **kwargs):
        train_calls.append({"dataset": dataset, **kwargs})
        return _FakeBooster()

    return SimpleNamespace(
        Dataset=_FakeDataset,
        train=train,
        log_evaluation=lambda _period: "log-callback",
    )


def test_lightgbm_outer_fold_does_not_select_boosting_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    train_calls: list[dict[str, object]] = []
    monkeypatch.setitem(sys.modules, "lightgbm", _fake_lightgbm(train_calls))
    x = np.arange(12, dtype=float).reshape(-1, 1)
    y = np.arange(12, dtype=float)
    groups = [f"group-{index}" for index in range(12)]

    builder._lightgbm_grouped_fit(x, y, groups, [0], num_boost_round=17)

    assert len(train_calls) == 6
    assert all(call["num_boost_round"] == 17 for call in train_calls)
    assert all("valid_sets" not in call for call in train_calls)


def test_binary_quality_calibration_excludes_each_evaluation_fold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    train_calls: list[dict[str, object]] = []
    monkeypatch.setitem(sys.modules, "lightgbm", _fake_lightgbm(train_calls))
    calibration_inputs: list[np.ndarray] = []

    def record_calibration(probabilities, _labels):
        calibration_inputs.append(np.asarray(probabilities).copy())
        return 0.0, 1.0

    monkeypatch.setattr(builder, "_fit_platt", record_calibration)
    x = np.arange(10, 20, dtype=float).reshape(-1, 1)
    y = np.asarray([0.0, 1.0] * 5)

    builder._lightgbm_binary_fit(x, y, num_boost_round=11)

    fold_ids = builder._stratified_folds(y, 5)
    raw_oof = x[:, 0] / 100
    assert len(calibration_inputs[0]) == len(y)  # deployed calibrator
    for fold, calibration_values in enumerate(calibration_inputs[1:]):
        evaluated = raw_oof[fold_ids == fold]
        assert not np.any(np.isin(evaluated, calibration_values))
    assert all(call["num_boost_round"] == 11 for call in train_calls)
    assert all("valid_sets" not in call for call in train_calls)


def test_lightgbm_profile_requires_versioned_fixed_rounds() -> None:
    payload = {
        "schema_version": "tabular-dataset-profile/v1",
        "profile_id": "test-profile",
        "name": "test",
        "task_id": "test-task",
        "package_id": "test-package",
        "model_family": "lightgbm_binary",
        "inputs": [{"path": "process.x", "column": "x", "kind": "number"}],
        "outputs": [{"key": "y", "column": "y", "unit": "1"}],
    }

    with pytest.raises(ValueError, match="fixed num_boost_round"):
        TabularDatasetProfile.model_validate(payload)


def test_existing_quality_reports_remain_readable_without_method_metadata() -> None:
    metric = TargetQualityMetric.model_validate({
        "target": "legacy",
        "parent_conditions": 2,
        "mae": 1.0,
        "rmse": 1.5,
        "interval_coverage_90": 0.5,
    })

    assert metric.interval_coverage_method is None
    assert metric.interval_coverage_observations is None


@pytest.mark.parametrize(
    "package_id",
    (
        "heat-treatment-ridge-external-v1",
        "concrete-strength-ridge-external-v1",
        "wear-curve-ridge-external-v1",
        "battery-degradation-lightgbm-calce-v1",
        "mpea-literature-tys-ridge-v1",
        "mpea-room-tensile-ridge-v1",
        "mpea-hardness-ridge-v1",
        "annealed-lightgbm-standard-tutorial-v1",
        "annealed-lightgbm-standard-process-v1",
    ),
)
def test_regenerated_continuous_packages_record_coverage_evidence(
    package_id: str,
) -> None:
    report = json.loads(
        (ROOT / "models" / "packages" / package_id / "reports" / "quality-report.json")
        .read_text(encoding="utf-8")
    )

    for metric in report["targets"]:
        assert metric["interval_coverage_method"].startswith("cross-fitted-")
        assert metric["interval_coverage_observations"] >= metric["parent_conditions"]


def test_regenerated_binary_package_records_cross_fitted_calibration() -> None:
    package = ROOT / "models" / "packages" / "secom-yield-lightgbm-calibrated-v1"
    diagnostics = json.loads(
        (package / "reports" / "classification-diagnostics.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))

    assert diagnostics["calibration"].startswith("cross-fitted")
    assert manifest["provenance"]["training_code_revision"] == (
        "tabular-lightgbm-binary-crossfit-v2"
    )
