from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from material_workbench.model_packages import ModelPackageLoader


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import build_hot_rolling_model_package as builder  # noqa: E402

build = builder.build


def test_builder_emits_ts_only_hot_rolling_package(tmp_path: Path) -> None:
    destination = tmp_path / "hot-rolled-gp"

    build(SOURCE, destination)

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((destination / "smoke" / "expected.json").read_text(encoding="utf-8"))
    stats = json.loads((destination / "reference" / "training_stats.json").read_text(encoding="utf-8"))

    assert manifest["package_version"] == "0.4.0-lifecycle-v1"
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
    assert package.load_predictor("ts-gp").spec.target == "TS"
    assert package.manifest.quality_report == "reports/quality-report.json"

    with pytest.raises(FileExistsError, match="refusing to replace"):
        build(SOURCE, destination)


def test_builder_does_not_swap_unverified_package(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "hot-rolled-gp"
    destination.mkdir()
    original_manifest = '{"package_id":"current"}'
    (destination / "manifest.json").write_text(original_manifest, encoding="utf-8")

    def fake_build(_source: Path, staging: Path) -> None:
        staging.mkdir(parents=True)
        (staging / "manifest.json").write_text('{"package_id":"invalid"}', encoding="utf-8")

    def reject(*_args: object, **_kwargs: object) -> None:
        raise ValueError("smoke mismatch")

    monkeypatch.setattr(builder, "_build", fake_build)
    monkeypatch.setattr(builder, "verify_model_package", reject)

    with pytest.raises(ValueError, match="smoke mismatch"):
        build(SOURCE, destination, replace=True)

    assert (destination / "manifest.json").read_text(encoding="utf-8") == original_manifest
