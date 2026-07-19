from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from material_workbench.feature_pipeline import FEATURE_DEFINITIONS, FEATURE_NAMES, FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION
from material_workbench.importer import load_workbook_data
from material_workbench.runtime import INPUT_SCHEMA_VERSION, MODEL_ID, MODEL_VERSION, TASK_ID, ModelRuntime
from material_workbench.schemas import CandidateInput


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(path), "bytes": path.stat().st_size}


def build(source: Path, destination: Path) -> None:
    data = load_workbook_data(source)
    runtime = ModelRuntime(data, load_package=False)
    if destination.exists():
        shutil.rmtree(destination)
    artifacts_dir = destination / "model-artifacts"
    feature_dir = destination / "feature-pipeline"
    reference_dir = destination / "reference"
    smoke_dir = destination / "smoke"
    for folder in (artifacts_dir, feature_dir, reference_dir, smoke_dir):
        folder.mkdir(parents=True, exist_ok=True)

    pipeline_path = feature_dir / "pipeline.json"
    pipeline_path.write_text(json.dumps({
        "id": FEATURE_PIPELINE_ID,
        "version": FEATURE_PIPELINE_VERSION,
        "features": [{"name": item.name, "unit": item.unit, "meaning": item.meaning} for item in FEATURE_DEFINITIONS],
        "missing_composition": "training_median_from_source_workbook",
        "heat_interpolation": "piecewise_linear",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    predictors: list[dict[str, object]] = []
    files = [pipeline_path]
    residual_payload: dict[str, np.ndarray] = {}
    training_counts: dict[str, int] = {}
    for target, model in sorted(runtime.models.items()):
        raw_weights = model.weights[1:] / model.feature_scale
        raw_bias = float(model.weights[0] - np.sum(model.feature_mean / model.feature_scale * model.weights[1:]))
        lower, upper = model.interval_offsets()
        path = artifacts_dir / f"{target}.npz"
        np.savez(path, weights=raw_weights, bias=np.asarray(raw_bias), lower_offset=np.asarray(lower), upper_offset=np.asarray(upper))
        files.append(path)
        residual_payload[target] = model.oof_residuals
        training_counts[target] = len(model.rows)
        unit = model.unit
        predictors.append({
            "id": f"{target.lower()}-ridge", "target": target, "unit": unit,
            "target_kind": "continuous_positive" if target == "lambda" else "continuous",
            "runtime_type": "builtin.linear.v1", "artifact": path.relative_to(destination).as_posix(),
            "predictive_family": "empirical_quantiles", "feature_names": list(FEATURE_NAMES),
            "config": {"calibration": "grouped_oof_residual_quantiles", "grouping": "parent_key"},
        })

    residual_path = reference_dir / "oof_residuals.npz"
    np.savez(residual_path, **residual_payload)
    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({
        "records": training_counts,
        "source_sha256": data.source_sha256,
        "composition_defaults": data.medians,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    files.extend([residual_path, stats_path])

    smoke_input = {
        "name": "package smoke",
        "composition": data.medians,
        "thickness_mm": 1.4,
        "line_speed_m_min": 103.0,
        "coating": "GI",
        "heat_pattern": [{"time_s": 0, "temperature_c": 25}, {"time_s": 300, "temperature_c": 800}, {"time_s": 360, "temperature_c": 810}, {"time_s": 650, "temperature_c": 120}],
    }
    smoke_input_path = smoke_dir / "input.json"
    smoke_input_path.write_text(json.dumps(smoke_input, ensure_ascii=False, indent=2), encoding="utf-8")
    smoke_candidate = CandidateInput.model_validate(smoke_input)
    smoke_values = runtime.vector_for_candidate(smoke_candidate)
    expected = {target: round(model.predict(smoke_values), 8) for target, model in runtime.models.items()}
    smoke_expected_path = smoke_dir / "expected.json"
    smoke_expected_path.write_text(json.dumps(expected, indent=2), encoding="utf-8")
    files.extend([smoke_input_path, smoke_expected_path])

    manifest = {
        "schema_version": "model-package/v1", "package_id": "annealed-ridge-2026-07", "package_version": MODEL_VERSION,
        "task_id": TASK_ID, "input_schema_version": INPUT_SCHEMA_VERSION,
        "feature_pipeline": {"id": FEATURE_PIPELINE_ID, "version": FEATURE_PIPELINE_VERSION, "spec": pipeline_path.relative_to(destination).as_posix(), "output_features": list(FEATURE_NAMES), "artifacts": [residual_path.relative_to(destination).as_posix(), stats_path.relative_to(destination).as_posix()]},
        "predictors": predictors,
        "provenance": {"training_data_id": f"sha256:{data.source_sha256}", "feature_dataset_id": f"sha256:{digest(stats_path)}", "training_code_revision": MODEL_VERSION},
        "artifacts": [artifact(destination, path) for path in files],
        "smoke_test": {"input": smoke_input_path.relative_to(destination).as_posix(), "expected": smoke_expected_path.relative_to(destination).as_posix()},
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/source/process_dashboard_realistic_excel_v2.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("models/packages/annealed-ridge-2026-07"))
    args = parser.parse_args()
    build(args.source, args.output)
