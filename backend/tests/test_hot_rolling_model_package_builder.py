from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest
import numpy as np

from decision_workbench.modeling.packages.loader import ModelPackageLoader


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "material_workbench_tutorial_v2.xlsx"
sys.path.insert(0, str(ROOT / "backend" / "scripts" / "generators"))

import build_hot_rolling_model_package as builder  # noqa: E402

build = builder.build


@pytest.fixture(autouse=True)
def fast_horseshoe_training(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_train(x: np.ndarray, _y: np.ndarray, *, seed: int, draws: int, **_kwargs: object):
        rng = np.random.default_rng(seed)
        return {
            "beta_draws": rng.normal(0, 0.05, (draws, x.shape[1])),
            "intercept_draws": rng.normal(0, 0.02, draws),
            "noise_scale_draws": np.full(draws, 0.1),
            "local_scale_draws": np.ones((draws, x.shape[1])),
        }, {"chains": int(_kwargs.get("chains", 1)), "draws_per_chain": draws, "warmup_per_chain": int(_kwargs.get("warmup", 1)), "divergences": 0, "minimum_effective_sample_size": float(draws), "maximum_r_hat": 1.0}

    monkeypatch.setattr(builder, "_train_numpyro", fake_train)


def test_builder_emits_ts_only_hot_rolling_package(tmp_path: Path) -> None:
    destination = tmp_path / "hot-rolled-horseshoe"

    build(SOURCE, destination)

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((destination / "smoke" / "expected.json").read_text(encoding="utf-8"))
    stats = json.loads((destination / "reference" / "training_stats.json").read_text(encoding="utf-8"))

    assert manifest["package_id"] == "hot-rolled-tutorial-v2"
    assert manifest["package_version"] == "1.2.0-feature-design-v3"
    assert [predictor["target"] for predictor in manifest["predictors"]] == ["TS"]
    assert {artifact["path"] for artifact in manifest["artifacts"] if artifact["path"].startswith("model-artifacts/")} == {
        "model-artifacts/TS.npz"
    }
    assert set(expected) == {"TS"}
    assert set(stats["records"]) == {"TS"}
    assert not (destination / "model-artifacts" / "YS.npz").exists()
    assert not (destination / "model-artifacts" / "EL.npz").exists()

    package = ModelPackageLoader().load(destination)
    assert package.manifest.task_id == "hot-rolled-properties-v1"
    predictor = package.load_predictor("ts-horseshoe")
    assert predictor.spec.target == "TS"
    assert predictor.spec.runtime_type == "builtin.posterior_linear.v1"
    assert predictor.spec.predictive_family == "normal"
    assert package.manifest.quality_report == "reports/quality-report.json"
    assert not (destination / "reports" / "selection-report.json").exists()
    assert not (destination / "reports" / "training-diagnostics.json").exists()

    with pytest.raises(FileExistsError, match="refusing to replace"):
        build(SOURCE, destination)


def test_builder_does_not_swap_unverified_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "hot-rolled-horseshoe"
    destination.mkdir()
    original_manifest = '{"package_id":"current"}'
    (destination / "manifest.json").write_text(original_manifest, encoding="utf-8")

    def fake_build(
        _source: Path,
        staging: Path,
        _profile: object | None,
    ) -> None:
        staging.mkdir(parents=True)
        (staging / "manifest.json").write_text('{"package_id":"invalid"}', encoding="utf-8")

    def reject(*_args: object, **_kwargs: object) -> None:
        raise ValueError("smoke mismatch")

    monkeypatch.setattr(builder, "_build", fake_build)
    monkeypatch.setattr(builder, "verify_model_package", reject)

    with pytest.raises(ValueError, match="smoke mismatch"):
        build(SOURCE, destination, replace=True)

    assert (destination / "manifest.json").read_text(encoding="utf-8") == original_manifest
