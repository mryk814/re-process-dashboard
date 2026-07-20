from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from material_workbench.model_package_verify import ModelPackageVerificationError, main, verify_model_package


def _artifact(path: Path, relative: str) -> dict[str, object]:
    return {
        "path": relative,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def _write_linear_package(
    tmp_path: Path,
    *,
    output_features: tuple[str, ...] = ("C", "Mn"),
    predictor_features: tuple[str, ...] | None = None,
    smoke_expected: dict[str, float] | None = None,
    include_smoke: bool = True,
) -> Path:
    root = tmp_path / "package"
    (root / "feature-pipeline").mkdir(parents=True)
    (root / "model-artifacts").mkdir()
    (root / "smoke").mkdir()

    pipeline = root / "feature-pipeline" / "pipeline.json"
    pipeline.write_text(
        json.dumps({"features": [{"name": name} for name in output_features]}),
        encoding="utf-8",
    )
    model = root / "model-artifacts" / "linear.npz"
    np.savez(
        model,
        weights=np.array([2.0, 3.0]),
        bias=np.array(1.0),
        lower_offset=np.array(-2.0),
        upper_offset=np.array(4.0),
    )
    smoke_input = root / "smoke" / "input.json"
    smoke_input.write_text('{"schema_version":"candidate-v1","composition":{"C":0.1,"Mn":1.5}}', encoding="utf-8")
    smoke_output = root / "smoke" / "expected.json"
    smoke_output.write_text(json.dumps(smoke_expected or {"TS": 5.7}), encoding="utf-8")

    artifacts = [
        _artifact(pipeline, "feature-pipeline/pipeline.json"),
        _artifact(model, "model-artifacts/linear.npz"),
    ]
    if include_smoke:
        artifacts.extend(
            [
                _artifact(smoke_input, "smoke/input.json"),
                _artifact(smoke_output, "smoke/expected.json"),
            ],
        )

    manifest: dict[str, object] = {
        "schema_version": "model-package/v1",
        "package_id": "linear-fixture",
        "package_version": "1",
        "task_id": "annealed-properties-v1",
        "input_schema_version": "candidate-v1",
        "feature_pipeline": {
            "id": "fixture-pipeline",
            "version": "1",
            "spec": "feature-pipeline/pipeline.json",
            "output_features": list(output_features),
            "artifacts": [],
        },
        "predictors": [
            {
                "id": "linear",
                "target": "TS",
                "unit": "MPa",
                "target_kind": "continuous",
                "runtime_type": "builtin.linear.v1",
                "artifact": "model-artifacts/linear.npz",
                "predictive_family": "empirical_quantiles",
                "feature_names": list(predictor_features or output_features),
            },
        ],
        "provenance": {
            "training_data_id": "sha256:training",
            "feature_dataset_id": "sha256:features",
            "training_code_revision": "git:test",
        },
        "artifacts": artifacts,
    }
    if include_smoke:
        manifest["smoke_test"] = {"input": "smoke/input.json", "expected": "smoke/expected.json"}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_verify_model_package_loads_adapter_and_reports_contract(tmp_path: Path) -> None:
    report = verify_model_package(
        _write_linear_package(tmp_path),
        expected_task_id="annealed-properties-v1",
        expected_input_schema_version="candidate-v1",
    )
    assert report.package_id == "linear-fixture"
    assert report.feature_count == 2
    assert report.smoke_test_present
    assert report.predictors[0].runtime_type == "builtin.linear.v1"
    assert report.predictors[0].target == "TS"


def test_verify_model_package_rejects_predictor_feature_order_drift(tmp_path: Path) -> None:
    root = _write_linear_package(tmp_path, predictor_features=("Mn", "C"))
    with pytest.raises(ModelPackageVerificationError, match="feature names/order"):
        verify_model_package(root)


def test_verify_model_package_rejects_smoke_target_drift(tmp_path: Path) -> None:
    root = _write_linear_package(tmp_path, smoke_expected={"YS": 400.0})
    with pytest.raises(ModelPackageVerificationError, match="smoke expected targets"):
        verify_model_package(root)


def test_verify_model_package_requires_smoke_by_default(tmp_path: Path) -> None:
    root = _write_linear_package(tmp_path, include_smoke=False)
    with pytest.raises(ModelPackageVerificationError, match="must declare a smoke test"):
        verify_model_package(root)
    assert not verify_model_package(root, require_smoke=False).smoke_test_present


def test_cli_outputs_machine_readable_report_and_nonzero_on_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _write_linear_package(tmp_path)
    assert main([str(root), "--json", "--expect-task-id", "annealed-properties-v1"]) == 0
    success = json.loads(capsys.readouterr().out)
    assert success["ok"] is True
    assert success["report"]["package_id"] == "linear-fixture"

    assert main([str(root), "--json", "--expect-task-id", "hot-rolled-properties-v1"]) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["ok"] is False
    assert "task_id mismatch" in failure["error"]
