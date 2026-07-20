from __future__ import annotations

import json
from pathlib import Path
import sys

from material_workbench.model_packages import ModelPackageLoader


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from build_hot_rolling_model_package import build  # noqa: E402


def test_builder_emits_ts_only_hot_rolling_package(tmp_path: Path) -> None:
    destination = tmp_path / "hot-rolled-gp"

    build(SOURCE, destination)

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    expected = json.loads((destination / "smoke" / "expected.json").read_text(encoding="utf-8"))
    stats = json.loads((destination / "reference" / "training_stats.json").read_text(encoding="utf-8"))

    assert manifest["package_version"] == "0.2.0-ts-only"
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
