"""Build the inactive, library-neutral quantile-only Model Package example."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from material_workbench.contracts.model_example_contracts import ExampleQualityReport, ExampleSmokeExpected, ExampleSmokeInput
from material_workbench.modeling.model_lifecycle import staged_package_destination
from material_workbench.modeling.model_package_verify import verify_model_package_example
from material_workbench.modeling.packages.contracts import PredictiveSummary
from material_workbench.contracts.task_contracts import TargetRuntimeCapability


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
    }


def _build(staging: Path) -> None:
    pipeline = staging / "feature-pipeline" / "pipeline.json"
    model = staging / "model-artifacts" / "quantiles.npz"
    smoke_input_path = staging / "smoke" / "input.json"
    smoke_expected_path = staging / "smoke" / "expected.json"
    quality_path = staging / "reports" / "quality-report.json"
    model.parent.mkdir(parents=True)
    _write_json(pipeline, {
        "id": "quantile-two-feature-example",
        "version": "1.0.0",
        "canonical_input_paths": ["composition.x", "process.scale"],
        "features": [
            {"name": "x", "unit": "1", "meaning": "synthetic location and spread driver", "group": "composition"},
            {"name": "scale", "unit": "1", "meaning": "synthetic common shift", "group": "process"},
        ],
    })
    levels = np.asarray([0.05, 0.5, 0.95])
    coefficients = np.asarray([[1.0, 0.2], [1.5, 0.2], [2.0, 0.2]])
    intercepts = np.asarray([0.0, 1.0, 2.0])
    np.savez(model, quantile_levels=levels, coefficients=coefficients, intercepts=intercepts)

    smoke_input = ExampleSmokeInput(predictor_id="target", features={"x": 0.4, "scale": 1.2}, seed=7)
    predictions = coefficients @ np.asarray([0.4, 1.2]) + intercepts
    summary = PredictiveSummary(
        target="synthetic_response",
        target_kind="continuous",
        unit="a.u.",
        point_statistic="median",
        point_estimate=float(predictions[1]),
        quantiles={"0.05": float(predictions[0]), "0.5": float(predictions[1]), "0.95": float(predictions[2])},
        distribution={"family": "empirical_quantiles", "support": "runtime_defined"},
    )
    capability = TargetRuntimeCapability(
        target="synthetic_response",
        point_statistics=("median",),
        standard_deviation=False,
        quantiles=True,
        samples=False,
        parametric_distribution=False,
        uncertainty_components=False,
        support=True,
        warnings=True,
        goal_probability="unavailable",
    )
    _write_json(smoke_input_path, smoke_input.model_dump(mode="json"))
    _write_json(smoke_expected_path, ExampleSmokeExpected(summary=summary, capability=capability).model_dump(mode="json"))
    grid = np.linspace(0, 1, 21)
    crossing_count = sum(int(np.any(np.diff(coefficients @ np.asarray([x, 1.0]) + intercepts) < 0)) for x in grid)
    if crossing_count:
        raise ValueError("quantile example crosses on the quality-gate grid")
    parent_blocks = np.repeat(np.arange(7), len(grid))
    evaluation_x = np.tile(grid, 7)
    predictions_grid = np.asarray([
        coefficients @ np.asarray([x, 1.0]) + intercepts for x in evaluation_x
    ])
    standardized = np.asarray([-1.15, -0.72, -0.36, 0.0, 0.31, 0.68, 1.08])
    observations = predictions_grid[:, 1] + standardized[parent_blocks] * (predictions_grid[:, 2] - predictions_grid[:, 0]) / 2
    errors = observations[:, None] - predictions_grid
    pinball = np.mean(np.maximum(levels * errors, (levels - 1) * errors), axis=0)
    quality = ExampleQualityReport(
        schema_version="model-example-quality/v1",
        evaluation_unit="leave-one-synthetic-parent-block-out rows",
        metrics={
            "pinball_q05": float(pinball[0]),
            "pinball_q50": float(pinball[1]),
            "pinball_q95": float(pinball[2]),
            "median_mae": float(np.mean(np.abs(errors[:, 1]))),
            "interval_coverage": float(np.mean((observations >= predictions_grid[:, 0]) & (observations <= predictions_grid[:, 2]))),
            "mean_interval_width": float(np.mean(predictions_grid[:, 2] - predictions_grid[:, 0])),
            "quantile_crossing_count": float(crossing_count),
            "parent_block_count": float(len(np.unique(parent_blocks))),
        },
        notes=("Metrics use fixed heteroscedastic synthetic observations, grouped by parent condition; they are not a production quality claim.",),
    )
    _write_json(quality_path, quality.model_dump(mode="json"))
    artifacts = [_artifact(staging, path) for path in (pipeline, model, smoke_input_path, smoke_expected_path, quality_path)]
    _write_json(staging / "manifest.json", {
        "schema_version": "model-package/v1",
        "package_id": "quantile-linear-example",
        "package_version": "1.0.0",
        "task_id": "model-runtime-example",
        "input_schema_version": "example-input/v1",
        "feature_pipeline": {
            "id": "quantile-two-feature-example",
            "version": "1.0.0",
            "spec": "feature-pipeline/pipeline.json",
            "canonical_input_paths": ["composition.x", "process.scale"],
            "output_features": ["x", "scale"],
            "artifacts": [],
        },
        "predictors": [{
            "id": "target",
            "target": "synthetic_response",
            "unit": "a.u.",
            "target_kind": "continuous",
            "runtime_type": "builtin.quantile_linear.v1",
            "architecture_id": "quantile_linear_v1",
            "artifact": "model-artifacts/quantiles.npz",
            "predictive_family": "empirical_quantiles",
            "feature_names": ["x", "scale"],
            "config": {"crossing_policy": "reject"},
        }],
        "provenance": {
            "training_data_id": "synthetic:known-heteroscedastic-quantiles",
            "feature_dataset_id": "synthetic:x-scale-grid",
            "training_code_revision": "build_quantile_model_example.py",
        },
        "artifacts": artifacts,
        "smoke_test": {"input": "smoke/input.json", "expected": "smoke/expected.json"},
        "quality_report": "reports/quality-report.json",
    })


def build(destination: Path, *, replace: bool = False) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(staging)
    verify_model_package_example(destination)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("examples/model-packages/quantile-linear"))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build(args.output, replace=args.replace)


if __name__ == "__main__":
    main()
