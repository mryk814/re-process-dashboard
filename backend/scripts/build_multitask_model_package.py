from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.feature_contracts import feature_index_families
from material_workbench.feature_pipeline import CANONICAL_INPUT_PATHS, FEATURE_DEFINITIONS, FEATURE_NAMES, FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION
from material_workbench.importer import load_workbook_data
from material_workbench.model_lifecycle import QualityReport, TargetQualityMetric, canonical_training_dataset, canonical_training_dataset_digest, dataset_profile_digest, runtime_capability_digest, staged_package_destination, task_input_contract_digest
from material_workbench.model_package_verify import verify_model_package
from material_workbench.runtime import INPUT_SCHEMA_VERSION, TARGETS, TASK_ID, ModelRuntime
from material_workbench.schemas import CandidateInput
from material_workbench.task_registry import load_task_contracts


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": digest(path), "bytes": path.stat().st_size}


PACKAGE_ID = "annealed-mtgp-2026-07"
PACKAGE_VERSION = "0.1.0-multitask-v1"
TRAINING_CODE_REVISION = "0.1.0-multitask-v1"
Z90 = 1.6448536269514722
FEATURE_GROUP_INDICES = feature_index_families(
    FEATURE_DEFINITIONS,
    {
        "composition": ("composition",),
        "process": ("process", "categorical"),
        "metallurgy": ("metallurgy",),
        "heat_pattern": ("heat_pattern",),
    },
)


def _grouped_target(runtime: ModelRuntime, target: str) -> tuple[list[str], np.ndarray, np.ndarray, float, float]:
    """Parent-condition means of raw features and outputs, plus per-task training noise."""
    column = TARGETS[target][0]
    model = runtime.models[target]
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in model.rows:
        grouped.setdefault(str(row["parent_key"]), []).append(row)
    parent_keys = sorted(grouped)
    x_rows: list[np.ndarray] = []
    y_rows: list[float] = []
    within_sse = 0.0
    within_df = 0
    repeat_counts: list[int] = []
    for key in parent_keys:
        rows = grouped[key]
        vectors = [runtime._vector_for_observation(row) for row in rows]
        assert all(vector is not None for vector in vectors)
        values = np.asarray([float(row["outputs"][column]) for row in rows])
        x_rows.append(np.vstack(vectors).mean(axis=0))
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
    return parent_keys, np.vstack(x_rows), y, train_noise, observation_variance


def _task_correlation(parent_keys: list[list[str]], centered: list[np.ndarray]) -> np.ndarray:
    """Pearson correlation of parent-condition mean outputs over shared parents."""
    task_count = len(parent_keys)
    correlation = np.eye(task_count)
    lookup = [dict(zip(keys, values)) for keys, values in zip(parent_keys, centered)]
    for left in range(task_count):
        for right in range(left + 1, task_count):
            shared = sorted(set(lookup[left]) & set(lookup[right]))
            if len(shared) < 3:
                continue
            a = np.asarray([lookup[left][key] for key in shared])
            b = np.asarray([lookup[right][key] for key in shared])
            if a.std() <= 0 or b.std() <= 0:
                continue
            value = float(np.corrcoef(a, b)[0, 1])
            if np.isfinite(value):
                correlation[left, right] = correlation[right, left] = value
    return correlation


def _shrunk_correlation(correlation: np.ndarray, shrink: float) -> np.ndarray:
    """Shrink toward independence, then repair to a valid unit-diagonal PSD matrix."""
    task_count = len(correlation)
    mixed = (1.0 - shrink) * correlation + shrink * np.eye(task_count)
    eigenvalues, eigenvectors = np.linalg.eigh(mixed)
    repaired = eigenvectors @ np.diag(np.clip(eigenvalues, 1e-6, None)) @ eigenvectors.T
    scale = np.sqrt(np.diag(repaired))
    return repaired / np.outer(scale, scale)


def _icm_covariance(train_x: np.ndarray, train_task: np.ndarray, lengthscale: np.ndarray, task_covariance: np.ndarray, train_noise: np.ndarray) -> np.ndarray:
    scaled = (train_x[:, None, :] - train_x[None, :, :]) / lengthscale
    base = np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
    covariance = task_covariance[np.ix_(train_task, train_task)] * base
    covariance.flat[:: len(train_x) + 1] += train_noise[train_task]
    return covariance


def _fit_icm_hyperparameters(
    train_x: np.ndarray,
    train_task: np.ndarray,
    centered_y: np.ndarray,
    task_std: np.ndarray,
    correlation: np.ndarray,
    train_noise: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    best: tuple[float, np.ndarray, np.ndarray, float] | None = None
    for global_scale in (0.5, 0.75, 1.0, 1.5, 2.25, 3.5):
        lengthscale = np.ones(train_x.shape[1], dtype=np.float64)
        for columns in FEATURE_GROUP_INDICES.values():
            lengthscale[list(columns)] = global_scale * np.sqrt(len(columns))
        for shrink in (0.25, 0.5, 0.75, 1.0):
            structure = _shrunk_correlation(correlation, shrink)
            for signal_multiplier in (0.5, 1.0, 2.0):
                task_covariance = signal_multiplier * np.outer(task_std, task_std) * structure
                covariance = _icm_covariance(train_x, train_task, lengthscale, task_covariance, train_noise)
                try:
                    cholesky = np.linalg.cholesky(covariance)
                except np.linalg.LinAlgError:
                    continue
                solved = np.linalg.solve(cholesky, centered_y)
                nll = float(0.5 * (solved @ solved) + np.log(np.diag(cholesky)).sum())
                if best is None or nll < best[0]:
                    best = (nll, lengthscale.copy(), task_covariance, shrink)
    if best is None:
        raise RuntimeError("multi-task GP hyperparameter search found no positive-definite covariance")
    return best[1], best[2], best[3]


def _parent_block_loo_quality(
    targets: list[str],
    parent_keys_joint: list[str],
    train_task: np.ndarray,
    precision: np.ndarray,
    alpha: np.ndarray,
) -> list[TargetQualityMetric]:
    """Leave every row of one parent condition out at once, across all tasks."""
    blocks: dict[str, list[int]] = {}
    for index, key in enumerate(parent_keys_joint):
        blocks.setdefault(key, []).append(index)
    residuals: dict[int, list[float]] = {index: [] for index in range(len(targets))}
    variances: dict[int, list[float]] = {index: [] for index in range(len(targets))}
    for indexes in blocks.values():
        block = np.ix_(indexes, indexes)
        block_precision = precision[block]
        block_covariance = np.linalg.inv(block_precision)
        block_residuals = block_covariance @ alpha[indexes]
        for position, row_index in enumerate(indexes):
            task = int(train_task[row_index])
            residuals[task].append(float(block_residuals[position]))
            variances[task].append(float(block_covariance[position, position]))
    metrics: list[TargetQualityMetric] = []
    for task_index, target in enumerate(targets):
        r = np.asarray(residuals[task_index])
        v = np.asarray(variances[task_index])
        if np.any(v <= 0):
            raise ValueError("multi-task GP block LOO produced a non-positive conditional variance")
        metrics.append(TargetQualityMetric(
            target=target,
            parent_conditions=len(r),
            mae=float(np.mean(np.abs(r))),
            rmse=float(np.sqrt(np.mean(r * r))),
            interval_coverage_90=float(np.mean(np.abs(r) <= Z90 * np.sqrt(v))),
        ))
    return metrics


def _mtgp_point(artifact_path: Path, raw_features: np.ndarray, task_index: int) -> float:
    with np.load(artifact_path, allow_pickle=False) as item:
        point = (raw_features - item["feature_mean"]) / item["feature_scale"]
        scaled = (item["train_x"] - point) / item["lengthscale"]
        base = np.exp(-0.5 * np.sum(scaled * scaled, axis=1))
        cross = item["task_covariance"][item["train_task"], task_index] * base
        return float(item["mean"][task_index]) + float(cross @ item["alpha"])


def _build(source: Path, destination: Path) -> None:
    data = load_workbook_data(source)
    runtime = ModelRuntime(data, load_package=False)
    artifacts_dir = destination / "model-artifacts"
    feature_dir = destination / "feature-pipeline"
    reference_dir = destination / "reference"
    smoke_dir = destination / "smoke"
    report_dir = destination / "reports"
    for folder in (artifacts_dir, feature_dir, reference_dir, smoke_dir, report_dir):
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

    targets = [target for target in TARGETS if target in runtime.models]
    if len(targets) < 2:
        raise RuntimeError("multi-task GP requires at least two trainable targets")
    grouped = {target: _grouped_target(runtime, target) for target in targets}

    raw_stack = np.vstack([grouped[target][1] for target in targets])
    feature_mean = raw_stack.mean(axis=0)
    feature_scale = raw_stack.std(axis=0)
    feature_scale[feature_scale < 1e-9] = 1.0

    task_mean = np.asarray([grouped[target][2].mean() for target in targets])
    task_std = np.asarray([max(float(grouped[target][2].std()), 1e-3) for target in targets])
    train_noise = np.asarray([grouped[target][3] for target in targets])
    observation_noise = np.asarray([grouped[target][4] for target in targets])

    parent_keys_joint: list[str] = []
    train_x_parts: list[np.ndarray] = []
    train_task_parts: list[np.ndarray] = []
    train_y_parts: list[np.ndarray] = []
    for task_index, target in enumerate(targets):
        keys, raw_x, y, _, _ = grouped[target]
        parent_keys_joint.extend(keys)
        train_x_parts.append((raw_x - feature_mean) / feature_scale)
        train_task_parts.append(np.full(len(y), task_index, dtype=np.int64))
        train_y_parts.append(y)
    train_x = np.vstack(train_x_parts)
    train_task = np.concatenate(train_task_parts)
    train_y = np.concatenate(train_y_parts)
    centered_y = train_y - task_mean[train_task]

    correlation = _task_correlation(
        [grouped[target][0] for target in targets],
        [grouped[target][2] - grouped[target][2].mean() for target in targets],
    )
    lengthscale, task_covariance, shrink = _fit_icm_hyperparameters(
        train_x, train_task, centered_y, task_std, correlation, train_noise,
    )

    covariance = _icm_covariance(train_x, train_task, lengthscale, task_covariance, train_noise)
    cholesky = np.linalg.cholesky(covariance)
    precision = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, np.eye(len(train_x))))
    alpha = precision @ centered_y

    artifact_path = artifacts_dir / "multitask.npz"
    np.savez(
        artifact_path,
        train_x=train_x,
        train_task=train_task,
        train_y=train_y,
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        lengthscale=lengthscale,
        task_covariance=task_covariance,
        mean=task_mean,
        train_noise=train_noise,
        observation_noise=observation_noise,
        precision=precision,
        alpha=alpha,
    )

    predictors = [{
        "id": f"{target.lower()}-mtgp", "target": target, "unit": TARGETS[target][1],
        "target_kind": "continuous_positive" if target == "lambda" else "continuous",
        "runtime_type": "builtin.multitask_gp.v1", "architecture_id": "icm_rbf_v1",
        "artifact": artifact_path.relative_to(destination).as_posix(),
        "predictive_family": "normal", "feature_names": list(FEATURE_NAMES),
        "config": {
            "task_index": task_index,
            "training_unit": "parent_condition_mean",
            "replicate_noise": "pooled_within_parent",
            "task_correlation_shrinkage": shrink,
        },
    } for task_index, target in enumerate(targets)]

    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({
        "records": {target: int(len(grouped[target][2])) for target in targets},
        "source_sha256": data.source_sha256,
        "composition_defaults": data.medians,
        "task_correlation": {
            f"{left}/{right}": round(float(correlation[i, j]), 6)
            for i, left in enumerate(targets)
            for j, right in enumerate(targets)
            if i < j
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")

    quality_path = report_dir / "quality-report.json"
    quality = QualityReport(
        schema_version="model-quality-report/v1",
        split="leave-one-parent-condition-out",
        targets=tuple(_parent_block_loo_quality(targets, parent_keys_joint, train_task, precision, alpha)),
    )
    quality_path.write_text(quality.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")

    smoke_input = {
        "name": "package smoke",
        "inputs": {
            "composition": data.medians,
            "process": {"thickness_mm": 1.4, "line_speed_m_min": 103.0},
            "categorical": {"coating": "GI"},
            "heat_pattern": [{"time_s": 0, "temperature_c": 25}, {"time_s": 300, "temperature_c": 800}, {"time_s": 360, "temperature_c": 810}, {"time_s": 650, "temperature_c": 120}],
        },
    }
    smoke_input_path = smoke_dir / "input.json"
    smoke_input_path.write_text(json.dumps(smoke_input, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    smoke_candidate = CandidateInput.model_validate(smoke_input)
    smoke_values = runtime.vector_for_candidate(smoke_candidate)
    expected = {target: round(_mtgp_point(artifact_path, smoke_values, task_index), 8) for task_index, target in enumerate(targets)}
    smoke_expected_path = smoke_dir / "expected.json"
    smoke_expected_path.write_text(json.dumps(expected, indent=2), encoding="utf-8", newline="\n")

    files = [pipeline_path, artifact_path, stats_path, quality_path, smoke_input_path, smoke_expected_path]
    contract = load_task_contracts()[TASK_ID]
    canonical_dataset = canonical_training_dataset(TASK_ID, data, contract)
    manifest = {
        "schema_version": "model-package/v1", "package_id": PACKAGE_ID, "package_version": PACKAGE_VERSION,
        "task_id": TASK_ID, "input_schema_version": INPUT_SCHEMA_VERSION,
        "input_contract_digest": task_input_contract_digest(contract.task_definition),
        "runtime_capability_digest": runtime_capability_digest(contract.runtime_capability),
        "feature_pipeline": {"id": FEATURE_PIPELINE_ID, "version": FEATURE_PIPELINE_VERSION, "spec": pipeline_path.relative_to(destination).as_posix(), "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "output_features": list(FEATURE_NAMES), "artifacts": [stats_path.relative_to(destination).as_posix()]},
        "predictors": predictors,
        "provenance": {"training_data_id": f"sha256:{data.source_sha256}", "feature_dataset_id": canonical_training_dataset_digest(canonical_dataset), "training_code_revision": TRAINING_CODE_REVISION, "dataset_profile_id": dataset_profile_digest()},
        "artifacts": [artifact(destination, path) for path in files],
        "smoke_test": {"input": smoke_input_path.relative_to(destination).as_posix(), "expected": smoke_expected_path.relative_to(destination).as_posix()},
        "quality_report": quality_path.relative_to(destination).as_posix(),
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def build(source: Path, destination: Path, *, replace: bool = False) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(source, staging)
        verify_model_package(staging, task_id=TASK_ID, source=source)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("data/source/process_dashboard_realistic_excel_v2.xlsx"))
    parser.add_argument("--output", type=Path, default=Path("models/packages/annealed-mtgp-2026-07"))
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build(args.source, args.output, replace=args.replace)
