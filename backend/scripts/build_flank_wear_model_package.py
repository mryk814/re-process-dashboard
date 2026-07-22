"""Build the flank-wear model package: exact GP on log1p(VB) per target.

学習単位は摩耗測定行（run×切削距離）。runごとに最大4点へ間引いて学習し、
品質はleave-one-run-out（親条件=摩耗試験）で評価する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.flank_wear import FEATURE_GROUP_INDICES, load_flank_wear_data
from material_workbench.flank_wear_feature_pipeline import (
    CANONICAL_INPUT_PATHS,
    FEATURE_DEFINITIONS,
    FEATURE_NAMES,
    INPUT_SCHEMA_VERSION,
    PIPELINE_ID,
    PIPELINE_VERSION,
    TASK_ID,
    build_flank_wear_features,
    build_flank_wear_features_from_observation,
    candidate_from_observation,
)
from material_workbench.model_lifecycle import (
    QualityReport,
    TargetQualityMetric,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    dataset_profile_digest,
    runtime_capability_digest,
    staged_package_destination,
    task_input_contract_digest,
)
from material_workbench.model_package_verify import verify_model_package
from material_workbench.task_registry import load_task_contracts


PACKAGE_ID = "flank-wear-gp-2026-07"
PACKAGE_VERSION = "0.1.0"
TRAINING_CODE_REVISION = "0.1.0-log1p-gp"
MAX_ROWS_PER_RUN = 4
Z90 = 1.6448536269514722


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": _digest(path), "bytes": path.stat().st_size}


def _fit_hyperparameters(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float, float]:
    centered = y - y.mean()
    variance = max(float(np.var(y)), 1e-6)
    best: tuple[float, np.ndarray, float, float] | None = None
    for global_scale in (0.75, 1.0, 1.5, 2.25, 3.5, 5.0):
        lengthscale = np.ones(x.shape[1], dtype=np.float64)
        for columns in FEATURE_GROUP_INDICES.values():
            lengthscale[list(columns)] = global_scale * np.sqrt(len(columns))
        scaled = (x[:, None, :] - x[None, :, :]) / lengthscale
        base = np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
        for multiplier in (0.5, 1.0, 2.0):
            for noise_fraction in (0.02, 0.05, 0.1, 0.2, 0.4):
                covariance = variance * multiplier * base
                covariance.flat[:: len(x) + 1] += variance * noise_fraction
                try:
                    cholesky = np.linalg.cholesky(covariance)
                except np.linalg.LinAlgError:
                    continue
                solved = np.linalg.solve(cholesky, centered)
                score = float(0.5 * (solved @ solved) + np.log(np.diag(cholesky)).sum())
                if best is None or score < best[0]:
                    best = (score, lengthscale.copy(), variance * multiplier, variance * noise_fraction)
    if best is None:
        raise RuntimeError("No positive-definite flank-wear GP covariance")
    return best[1], best[2], best[3]


def _leave_one_run_out_quality(
    target: str,
    y_log: np.ndarray,
    y_raw: np.ndarray,
    alpha: np.ndarray,
    precision: np.ndarray,
    groups: list[str],
) -> TargetQualityMetric:
    indices_by_run: dict[str, list[int]] = defaultdict(list)
    for index, run in enumerate(groups):
        indices_by_run[run].append(index)
    absolute_errors: list[float] = []
    squared_errors: list[float] = []
    covered = 0
    for run_indices in indices_by_run.values():
        block = np.ix_(run_indices, run_indices)
        conditional_covariance = np.linalg.inv(precision[block])
        residual_log = conditional_covariance @ alpha[run_indices]
        conditional_std = np.sqrt(np.clip(np.diag(conditional_covariance), 1e-12, None))
        covered += int(np.sum(np.abs(residual_log) <= Z90 * conditional_std))
        predicted = np.clip(np.expm1(y_log[run_indices] - residual_log), 0.0, None)
        errors = predicted - y_raw[run_indices]
        absolute_errors.extend(np.abs(errors).tolist())
        squared_errors.extend((errors ** 2).tolist())
    return TargetQualityMetric(
        target=target,
        parent_conditions=len(indices_by_run),
        mae=float(np.mean(absolute_errors)),
        rmse=float(np.sqrt(np.mean(squared_errors))),
        interval_coverage_90=float(covered / len(y_log)),
    )


def _point(path: Path, raw: np.ndarray) -> float:
    with np.load(path, allow_pickle=False) as item:
        x = item["train_x"]
        query = (raw - item["feature_mean"]) / item["feature_scale"]
        cross = float(item["outputscale"]) * np.exp(-0.5 * np.sum(((x - query) / item["lengthscale"]) ** 2, axis=1))
        latent = float(item["mean"]) + float(cross @ item["alpha"])
        return max(float(np.expm1(latent)), 0.0)


def _build(source: Path, destination: Path) -> None:
    data = load_flank_wear_data(source)
    contract = load_task_contracts()[TASK_ID]
    rows = [
        row for row in data.observations
        if row["eligible"] and row["features"] and row["composition"]
    ]
    if not rows:
        raise ValueError("no eligible flank-wear observations")
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["parent_key"])].append(row)
    training_rows: list[dict[str, object]] = []
    for run_key in sorted(grouped):
        run_rows = sorted(grouped[run_key], key=lambda item: float(item["features"]["cutting_distance_m"]))
        if len(run_rows) <= MAX_ROWS_PER_RUN:
            training_rows.extend(run_rows)
        else:
            picks = np.unique(np.round(np.linspace(0, len(run_rows) - 1, MAX_ROWS_PER_RUN)).astype(int))
            training_rows.extend(run_rows[index] for index in picks)

    artifact_dir, feature_dir, reference_dir, smoke_dir, report_dir = (
        destination / name for name in ("model-artifacts", "feature-pipeline", "reference", "smoke", "reports")
    )
    for folder in (artifact_dir, feature_dir, reference_dir, smoke_dir, report_dir):
        folder.mkdir(parents=True, exist_ok=True)
    pipeline_path = feature_dir / "pipeline.json"
    pipeline_path.write_text(json.dumps({
        "id": PIPELINE_ID,
        "version": PIPELINE_VERSION,
        "canonical_input_paths": list(CANONICAL_INPUT_PATHS),
        "features": [{"name": item.name, "unit": item.unit, "meaning": item.meaning, "group": item.group} for item in FEATURE_DEFINITIONS],
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    files = [pipeline_path]

    bundles = [build_flank_wear_features_from_observation(row, data.medians) for row in training_rows]
    if any(bundle is None for bundle in bundles):
        raise ValueError("eligible flank-wear observation did not convert to a candidate")
    raw_x = np.vstack([bundle.values for bundle in bundles if bundle is not None])
    feature_mean, feature_scale = raw_x.mean(axis=0), raw_x.std(axis=0)
    feature_scale[feature_scale < 1e-9] = 1.0
    x = (raw_x - feature_mean) / feature_scale
    groups = [str(row["parent_key"]) for row in training_rows]

    predictors: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    quality_metrics: list[TargetQualityMetric] = []
    for target, column in data.measurement_labels.items():
        y_raw = np.asarray([float(row["outputs"][column]) for row in training_rows])
        y_log = np.log1p(y_raw)
        lengthscale, outputscale, train_noise = _fit_hyperparameters(x, y_log)
        scaled = (x[:, None, :] - x[None, :, :]) / lengthscale
        covariance = outputscale * np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
        covariance.flat[:: len(x) + 1] += train_noise
        cholesky = np.linalg.cholesky(covariance)
        precision = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, np.eye(len(x))))
        mean_value = float(y_log.mean())
        alpha = precision @ (y_log - mean_value)
        quality_metrics.append(_leave_one_run_out_quality(target, y_log, y_raw, alpha, precision, groups))
        path = artifact_dir / f"{target}.npz"
        np.savez(
            path,
            train_x=x, train_y=y_log, feature_mean=feature_mean, feature_scale=feature_scale,
            lengthscale=lengthscale, outputscale=np.asarray(outputscale),
            train_noise=np.asarray(train_noise), observation_noise=np.asarray(train_noise),
            mean=np.asarray(mean_value), precision=precision, alpha=alpha,
        )
        files.append(path)
        counts[target] = len(y_log)
        predictors.append({
            "id": f"{target.lower()}-gp",
            "target": target,
            "unit": "µm",
            "target_kind": "continuous_positive",
            "runtime_type": "builtin.exact_gp.v1",
            "architecture_id": "exact_rbf_grouped_v1",
            "artifact": path.relative_to(destination).as_posix(),
            "predictive_family": "lognormal",
            "feature_names": list(FEATURE_NAMES),
            "config": {
                "latent_transform": "log1p",
                "training_unit": "wear_measurement_row",
                "rows_per_run_limit": MAX_ROWS_PER_RUN,
                "estimand_context": {"machining_method": "外径旋削"},
            },
        })

    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({
        "records": counts,
        "runs": len(grouped),
        "source_sha256": data.source_sha256,
        "composition_defaults": data.medians,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    files.append(stats_path)

    quality_path = report_dir / "quality-report.json"
    quality = QualityReport(
        schema_version="model-quality-report/v1",
        split="leave-one-parent-condition-out",
        targets=tuple(quality_metrics),
    )
    quality_path.write_text(quality.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    files.append(quality_path)

    sample_candidate = candidate_from_observation(training_rows[0])
    if sample_candidate is None:
        raise ValueError("smoke observation did not convert to a flank-wear candidate")
    sample = sample_candidate.model_copy(update={"name": "flank wear package smoke"})
    smoke_input = smoke_dir / "input.json"
    smoke_input.write_text(sample.model_dump_json(indent=2), encoding="utf-8", newline="\n")
    raw = build_flank_wear_features(sample, data.medians).values
    smoke_expected = smoke_dir / "expected.json"
    smoke_expected.write_text(json.dumps({
        target: round(_point(artifact_dir / f"{target}.npz", raw), 8) for target in data.measurement_labels
    }, indent=2), encoding="utf-8", newline="\n")
    files.extend([smoke_input, smoke_expected])

    canonical_dataset = canonical_training_dataset(TASK_ID, data, contract)
    manifest = {
        "schema_version": "model-package/v1",
        "package_id": PACKAGE_ID,
        "package_version": PACKAGE_VERSION,
        "task_id": TASK_ID,
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "runtime_capability_digest": runtime_capability_digest(contract.runtime_capability),
        "feature_pipeline": {
            "id": PIPELINE_ID,
            "version": PIPELINE_VERSION,
            "spec": pipeline_path.relative_to(destination).as_posix(),
            "canonical_input_paths": list(CANONICAL_INPUT_PATHS),
            "output_features": list(FEATURE_NAMES),
            "artifacts": [stats_path.relative_to(destination).as_posix()],
        },
        "predictors": predictors,
        "provenance": {
            "training_data_id": f"sha256:{data.source_sha256}",
            "feature_dataset_id": canonical_training_dataset_digest(canonical_dataset),
            "training_code_revision": TRAINING_CODE_REVISION,
            "dataset_profile_id": dataset_profile_digest(Path(data.profile_path)),
        },
        "artifacts": [_artifact(destination, path) for path in files],
        "smoke_test": {
            "input": smoke_input.relative_to(destination).as_posix(),
            "expected": smoke_expected.relative_to(destination).as_posix(),
        },
        "quality_report": quality_path.relative_to(destination).as_posix(),
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def build(source: Path, destination: Path, *, replace: bool = False) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(source, staging)
        verify_model_package(staging, task_id=TASK_ID, source=source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/source/cutting_tool_flank_wear_synthetic_dataset.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("models/packages/flank-wear-gp-2026-07"))
    parser.add_argument("--replace", action="store_true")
    arguments = parser.parse_args()
    build(arguments.source, arguments.output, replace=arguments.replace)
