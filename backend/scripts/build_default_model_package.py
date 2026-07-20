from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.feature_contracts import feature_index_families
from material_workbench.feature_pipeline import CANONICAL_INPUT_PATHS, FEATURE_DEFINITIONS, FEATURE_NAMES, FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION
from material_workbench.importer import load_workbook_data
from material_workbench.runtime import INPUT_SCHEMA_VERSION, TARGETS, TASK_ID, ModelRuntime
from material_workbench.schemas import CandidateInput


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(path), "bytes": path.stat().st_size}


PACKAGE_ID = "annealed-gp-2026-07"
PACKAGE_VERSION = "0.7.0-exact-gp-v1"
TRAINING_CODE_REVISION = "0.6.0-exact-gp-v1"
FEATURE_GROUP_INDICES = feature_index_families(
    FEATURE_DEFINITIONS,
    {
        "composition": ("composition",),
        "process": ("process", "categorical"),
        "metallurgy": ("metallurgy",),
        "heat_pattern": ("heat_pattern",),
    },
)


def _grouped_training(model: object, target: str) -> tuple[np.ndarray, np.ndarray, float, float]:
    column = TARGETS[target][0]
    grouped: dict[str, list[int]] = {}
    rows = model.rows  # type: ignore[attr-defined]
    for index, row in enumerate(rows):
        grouped.setdefault(str(row["parent_key"]), []).append(index)
    x_rows: list[np.ndarray] = []
    y_rows: list[float] = []
    within_sse = 0.0
    within_df = 0
    repeat_counts: list[int] = []
    normalized = model.x_train  # type: ignore[attr-defined]
    for indexes in grouped.values():
        values = np.asarray([float(rows[index]["outputs"][column]) for index in indexes])
        x_rows.append(normalized[indexes].mean(axis=0))
        y_rows.append(float(values.mean()))
        repeat_counts.append(len(values))
        if len(values) > 1:
            within_sse += float(np.sum((values - values.mean()) ** 2))
            within_df += len(values) - 1
    y = np.asarray(y_rows, dtype=np.float64)
    total_variance = max(float(np.var(y)), 1e-6)
    observation_variance = within_sse / within_df if within_df else total_variance * 0.1
    observation_variance = max(float(observation_variance), total_variance * 1e-4, 1e-8)
    median_repeats = max(float(np.median(repeat_counts)), 1.0)
    train_noise = max(observation_variance / median_repeats, total_variance * 1e-5, 1e-9)
    return np.vstack(x_rows), y, train_noise, observation_variance


def _fit_gp_hyperparameters(
    train_x: np.ndarray,
    train_y: np.ndarray,
    train_noise: float,
) -> tuple[np.ndarray, float, float]:
    centered = train_y - train_y.mean()
    between_variance = max(float(np.var(train_y)), 1e-6)
    best: tuple[float, np.ndarray, float, float] | None = None
    for global_scale in (0.5, 0.75, 1.0, 1.5, 2.25, 3.5):
        lengthscale = np.ones(train_x.shape[1], dtype=np.float64)
        for columns in FEATURE_GROUP_INDICES.values():
            lengthscale[list(columns)] = global_scale * np.sqrt(len(columns))
        scaled = (train_x[:, None, :] - train_x[None, :, :]) / lengthscale
        base = np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
        for signal_multiplier in (0.5, 1.0, 2.0):
            outputscale = between_variance * signal_multiplier
            covariance = outputscale * base
            covariance.flat[:: len(train_x) + 1] += train_noise
            try:
                cholesky = np.linalg.cholesky(covariance)
            except np.linalg.LinAlgError:
                continue
            solved = np.linalg.solve(cholesky, centered)
            nll = float(0.5 * (solved @ solved) + np.log(np.diag(cholesky)).sum())
            candidate = (nll, lengthscale.copy(), outputscale, train_noise)
            if best is None or candidate[0] < best[0]:
                best = candidate
    if best is None:
        raise RuntimeError("Gaussian-process hyperparameter search found no positive-definite covariance")
    return best[1], best[2], best[3]


def _gp_point(artifact_path: Path, raw_features: np.ndarray) -> float:
    with np.load(artifact_path, allow_pickle=False) as item:
        train_x = item["train_x"]
        train_y = item["train_y"]
        mean = float(item["mean"])
        point = (raw_features - item["feature_mean"]) / item["feature_scale"]
        cross_scaled = (train_x - point) / item["lengthscale"]
        cross = float(item["outputscale"]) * np.exp(-0.5 * np.sum(cross_scaled * cross_scaled, axis=1))
        return mean + float(cross @ item["alpha"])


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
        "canonical_input_paths": list(CANONICAL_INPUT_PATHS),
        "features": [{"name": item.name, "unit": item.unit, "meaning": item.meaning, "group": item.group} for item in FEATURE_DEFINITIONS],
        "missing_composition": "training_median_from_source_workbook",
        "heat_interpolation": "piecewise_linear",
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    predictors: list[dict[str, object]] = []
    files = [pipeline_path]
    training_counts: dict[str, int] = {}
    for target, model in sorted(runtime.models.items()):
        train_x, train_y, train_noise, observation_noise = _grouped_training(model, target)
        lengthscale, outputscale, train_noise = _fit_gp_hyperparameters(train_x, train_y, train_noise)
        scaled = (train_x[:, None, :] - train_x[None, :, :]) / lengthscale
        covariance = outputscale * np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
        covariance.flat[:: len(train_x) + 1] += train_noise
        cholesky = np.linalg.cholesky(covariance)
        precision = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, np.eye(len(train_x))))
        mean_value = float(train_y.mean())
        alpha = precision @ (train_y - mean_value)
        path = artifacts_dir / f"{target}.npz"
        np.savez(
            path,
            train_x=train_x,
            train_y=train_y,
            feature_mean=model.feature_mean,
            feature_scale=model.feature_scale,
            lengthscale=lengthscale,
            outputscale=np.asarray(outputscale),
            train_noise=np.asarray(train_noise),
            observation_noise=np.asarray(observation_noise),
            mean=np.asarray(mean_value),
            precision=precision,
            alpha=alpha,
        )
        files.append(path)
        training_counts[target] = len(train_y)
        unit = model.unit
        predictors.append({
            "id": f"{target.lower()}-gp", "target": target, "unit": unit,
            "target_kind": "continuous_positive" if target == "lambda" else "continuous",
            "runtime_type": "builtin.exact_gp.v1", "architecture_id": "exact_rbf_grouped_v1",
            "artifact": path.relative_to(destination).as_posix(),
            "predictive_family": "normal", "feature_names": list(FEATURE_NAMES),
            "config": {"training_unit": "parent_condition_mean", "replicate_noise": "pooled_within_parent"},
        })

    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({
        "records": training_counts,
        "source_sha256": data.source_sha256,
        "composition_defaults": data.medians,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    files.append(stats_path)

    smoke_input = {
        "name": "package smoke",
        "composition": data.medians,
        "thickness_mm": 1.4,
        "line_speed_m_min": 103.0,
        "coating": "GI",
        "heat_pattern": [{"time_s": 0, "temperature_c": 25}, {"time_s": 300, "temperature_c": 800}, {"time_s": 360, "temperature_c": 810}, {"time_s": 650, "temperature_c": 120}],
    }
    smoke_input_path = smoke_dir / "input.json"
    smoke_input_path.write_text(json.dumps(smoke_input, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    smoke_candidate = CandidateInput.model_validate(smoke_input)
    smoke_values = runtime.vector_for_candidate(smoke_candidate)
    expected = {target: round(_gp_point(artifacts_dir / f"{target}.npz", smoke_values), 8) for target in runtime.models}
    smoke_expected_path = smoke_dir / "expected.json"
    smoke_expected_path.write_text(json.dumps(expected, indent=2), encoding="utf-8", newline="\n")
    files.extend([smoke_input_path, smoke_expected_path])

    manifest = {
        "schema_version": "model-package/v1", "package_id": PACKAGE_ID, "package_version": PACKAGE_VERSION,
        "task_id": TASK_ID, "input_schema_version": INPUT_SCHEMA_VERSION,
        "feature_pipeline": {"id": FEATURE_PIPELINE_ID, "version": FEATURE_PIPELINE_VERSION, "spec": pipeline_path.relative_to(destination).as_posix(), "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "output_features": list(FEATURE_NAMES), "artifacts": [stats_path.relative_to(destination).as_posix()]},
        "predictors": predictors,
        "provenance": {"training_data_id": f"sha256:{data.source_sha256}", "feature_dataset_id": f"sha256:{digest(stats_path)}", "training_code_revision": TRAINING_CODE_REVISION},
        "artifacts": [artifact(destination, path) for path in files],
        "smoke_test": {"input": smoke_input_path.relative_to(destination).as_posix(), "expected": smoke_expected_path.relative_to(destination).as_posix()},
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/source/process_dashboard_realistic_excel_v2.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("models/packages/annealed-gp-2026-07"))
    args = parser.parse_args()
    build(args.source, args.output)
