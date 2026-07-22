from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pytest

from material_workbench.model_package_verify import verify_model_package_example
from material_workbench.model_packages import ModelPackageLoader, PackageContractError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import build_quantile_model_example as quantile_builder  # noqa: E402


def _replace_model_artifact(root: Path, **arrays: np.ndarray) -> None:
    path = root / "model-artifacts" / "quantiles.npz"
    np.savez(path, **arrays)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["artifacts"] if item["path"] == "model-artifacts/quantiles.npz")
    entry["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    entry["bytes"] = path.stat().st_size
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_quantile_example_builds_and_verifies_without_activation(tmp_path: Path) -> None:
    destination = tmp_path / "quantile"
    quantile_builder.build(destination)

    report = verify_model_package_example(destination)
    summary = ModelPackageLoader().load(destination).load_predictor("target").predict({"x": 0.4, "scale": 1.2}, seed=7)

    assert report.package_id == "quantile-linear-example"
    assert summary.point_statistic == "median"
    assert summary.point_estimate == pytest.approx(1.84)
    assert summary.quantiles == pytest.approx({"0.05": 0.64, "0.5": 1.84, "0.95": 3.04})
    assert summary.distribution == {"family": "empirical_quantiles", "support": "runtime_defined"}
    assert "quantile-linear-example" not in (ROOT / "models" / "active-packages.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("arrays", "message"),
    [
        ({"quantile_levels": np.array([0.05, 0.5, 0.95]), "coefficients": np.zeros((3, 2))}, "unexpected tensor schema"),
        ({"quantile_levels": np.array([0.05, 0.5, 1.2]), "coefficients": np.zeros((3, 2)), "intercepts": np.zeros(3)}, r"inside \(0, 1\)"),
        ({"quantile_levels": np.array([0.05, 0.5, 0.95]), "coefficients": np.zeros((3, 3)), "intercepts": np.zeros(3)}, "incompatible shapes"),
        ({"quantile_levels": np.array([0.05, 0.5, 0.95]), "coefficients": np.array([[0.0, 0.0], [np.nan, 0.0], [0.0, 0.0]]), "intercepts": np.zeros(3)}, "finite"),
    ],
)
def test_quantile_adapter_rejects_malformed_artifacts(tmp_path: Path, arrays: dict[str, np.ndarray], message: str) -> None:
    destination = tmp_path / "quantile"
    quantile_builder.build(destination)
    _replace_model_artifact(destination, **arrays)
    package = ModelPackageLoader().load(destination)

    with pytest.raises(PackageContractError, match=message):
        package.load_predictor("target")


def test_quantile_adapter_rejects_crossing_instead_of_sorting_it(tmp_path: Path) -> None:
    destination = tmp_path / "quantile"
    quantile_builder.build(destination)
    _replace_model_artifact(
        destination,
        quantile_levels=np.array([0.05, 0.5, 0.95]),
        coefficients=np.array([[3.0, 0.0], [2.0, 0.0], [1.0, 0.0]]),
        intercepts=np.array([0.0, 1.0, 2.0]),
    )
    predictor = ModelPackageLoader().load(destination).load_predictor("target")

    with pytest.raises(PackageContractError, match="cross"):
        predictor.predict({"x": 2.0, "scale": 0.0})
