from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pytest

from material_workbench.model_packages import ModelPackageLoader


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import build_multitask_model_package as builder  # noqa: E402


def test_builder_emits_joint_multitask_package(tmp_path: Path) -> None:
    destination = tmp_path / "annealed-mtgp"

    builder.build(SOURCE, destination)

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((destination / "smoke" / "expected.json").read_text(encoding="utf-8"))
    quality = json.loads((destination / "reports" / "quality-report.json").read_text(encoding="utf-8"))

    assert manifest["task_id"] == "annealed-properties-v1"
    assert [predictor["target"] for predictor in manifest["predictors"]] == ["TS", "YS", "EL", "lambda"]
    assert {predictor["runtime_type"] for predictor in manifest["predictors"]} == {"builtin.multitask_gp.v1"}
    assert {predictor["artifact"] for predictor in manifest["predictors"]} == {"model-artifacts/multitask.npz"}
    assert [predictor["config"]["task_index"] for predictor in manifest["predictors"]] == [0, 1, 2, 3]
    assert set(expected) == {"TS", "YS", "EL", "lambda"}
    assert quality["split"] == "leave-one-parent-condition-out"
    assert {item["target"] for item in quality["targets"]} == {"TS", "YS", "EL", "lambda"}

    with np.load(destination / "model-artifacts" / "multitask.npz", allow_pickle=False) as arrays:
        task_count = arrays["task_covariance"].shape[0]
        assert task_count == 4
        assert set(np.unique(arrays["train_task"])) == {0, 1, 2, 3}
        assert len(arrays["train_y"]) == len(arrays["train_task"]) == len(arrays["train_x"])
        eigenvalues = np.linalg.eigvalsh(arrays["task_covariance"])
        assert eigenvalues.min() >= -1e-8 * eigenvalues.max()

    package = ModelPackageLoader().load(destination)
    assert package.manifest.quality_report == "reports/quality-report.json"
    predictor = package.load_predictor("ts-mtgp")
    assert predictor.spec.target == "TS"

    with pytest.raises(FileExistsError, match="refusing to replace"):
        builder.build(SOURCE, destination)
