from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np

from material_workbench.hot_rolling_feature_pipeline import CANONICAL_INPUT_PATHS, FEATURE_COMPONENTS, FEATURE_NAMES, INPUT_SCHEMA_VERSION, PIPELINE_ID, PIPELINE_VERSION, build_hot_rolling_features
from material_workbench.importer import load_workbook_data
from material_workbench.schemas import HotRollingCandidateInput


PACKAGE_ID = "hot-rolled-gp-2026-07"
PACKAGE_VERSION = "0.2.0-exact-gp-v1"
TRAINING_CODE_REVISION = "0.1.0-exact-gp-v1"
TASK_ID = "hot-rolled-properties-v1"
TARGETS = {"TS": ("TS[MPa]", "MPa"), "YS": ("YS[MPa]", "MPa"), "EL": ("EL[%]", "%")}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {"path": path.relative_to(root).as_posix(), "sha256": _digest(path), "bytes": path.stat().st_size}


def _candidate(row: dict[str, object]) -> HotRollingCandidateInput:
    process = row["features"]
    assert isinstance(process, dict)
    return HotRollingCandidateInput(
        name=str(row["parent_key"]), composition=row["composition"],  # type: ignore[arg-type]
        reheat_temperature_c=process["reheat_temperature_c"], hold_time_min=process["hold_time_min"],
        finish_temperature_c=process["finish_temperature_c"], coiling_temperature_c=process["coiling_temperature_c"],
        cooling_rate_c_s=process["cooling_rate_c_s"], entry_thickness_mm=process["entry_thickness_mm"],
        exit_thickness_mm=process["exit_thickness_mm"], route=process["route"],
    )


def _fit_hyperparameters(x: np.ndarray, y: np.ndarray, train_noise: float) -> tuple[np.ndarray, float]:
    centered = y - y.mean()
    variance = max(float(np.var(y)), 1e-6)
    best: tuple[float, np.ndarray, float] | None = None
    for global_scale in (0.5, 0.75, 1.0, 1.5, 2.25, 3.5):
        lengthscale = np.ones(x.shape[1], dtype=np.float64)
        for columns in FEATURE_COMPONENTS.values():
            lengthscale[list(columns)] = global_scale * np.sqrt(len(columns))
        scaled = (x[:, None, :] - x[None, :, :]) / lengthscale
        base = np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
        for multiplier in (0.5, 1.0, 2.0):
            outputscale = variance * multiplier
            covariance = outputscale * base
            covariance.flat[:: len(x) + 1] += train_noise
            try:
                cholesky = np.linalg.cholesky(covariance)
            except np.linalg.LinAlgError:
                continue
            solved = np.linalg.solve(cholesky, centered)
            score = float(0.5 * (solved @ solved) + np.log(np.diag(cholesky)).sum())
            if best is None or score < best[0]:
                best = (score, lengthscale.copy(), outputscale)
    if best is None:
        raise RuntimeError("No positive-definite hot-rolling GP covariance")
    return best[1], best[2]


def _point(path: Path, raw: np.ndarray) -> float:
    with np.load(path, allow_pickle=False) as item:
        x = item["train_x"]
        query = (raw - item["feature_mean"]) / item["feature_scale"]
        cross = float(item["outputscale"]) * np.exp(-0.5 * np.sum(((x - query) / item["lengthscale"]) ** 2, axis=1))
        mean = float(item["mean"])
        return mean + float(cross @ item["alpha"])


def build(source: Path, destination: Path) -> None:
    data = load_workbook_data(source)
    rows = [row for row in data.observations if row["source"] == "熱延引張" and row["eligible"] and row["features"] and row["composition"]]
    if destination.exists():
        shutil.rmtree(destination)
    artifact_dir, feature_dir, reference_dir, smoke_dir = (destination / name for name in ("model-artifacts", "feature-pipeline", "reference", "smoke"))
    for folder in (artifact_dir, feature_dir, reference_dir, smoke_dir):
        folder.mkdir(parents=True, exist_ok=True)
    pipeline_path = feature_dir / "pipeline.json"
    pipeline_path.write_text(json.dumps({"id": PIPELINE_ID, "version": PIPELINE_VERSION, "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "features": [{"name": name} for name in FEATURE_NAMES]}, indent=2), encoding="utf-8")
    files = [pipeline_path]
    predictors: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    for target, (column, unit) in TARGETS.items():
        target_rows = [row for row in rows if column in row["outputs"]]
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in target_rows:
            grouped.setdefault(str(row["parent_key"]), []).append(row)
        raw_x, y, within, within_df, repeats = [], [], 0.0, 0, []
        for group_rows in grouped.values():
            raw_x.append(build_hot_rolling_features(_candidate(group_rows[0]), data.medians).values)
            values = np.asarray([float(row["outputs"][column]) for row in group_rows])
            y.append(float(values.mean()))
            repeats.append(len(values))
            if len(values) > 1:
                within += float(np.sum((values - values.mean()) ** 2))
                within_df += len(values) - 1
        raw = np.vstack(raw_x)
        feature_mean, feature_scale = raw.mean(axis=0), raw.std(axis=0)
        feature_scale[feature_scale < 1e-9] = 1.0
        x, target_y = (raw - feature_mean) / feature_scale, np.asarray(y)
        target_variance = max(float(np.var(target_y)), 1e-6)
        observation_noise = max(within / within_df if within_df else target_variance * 0.1, target_variance * 1e-4)
        train_noise = max(observation_noise / max(float(np.median(repeats)), 1.0), target_variance * 1e-5)
        lengthscale, outputscale = _fit_hyperparameters(x, target_y, train_noise)
        scaled = (x[:, None, :] - x[None, :, :]) / lengthscale
        covariance = outputscale * np.exp(-0.5 * np.sum(scaled * scaled, axis=2))
        covariance.flat[:: len(x) + 1] += train_noise
        cholesky = np.linalg.cholesky(covariance)
        precision = np.linalg.solve(cholesky.T, np.linalg.solve(cholesky, np.eye(len(x))))
        mean_value = float(target_y.mean())
        alpha = precision @ (target_y - mean_value)
        path = artifact_dir / f"{target}.npz"
        np.savez(path, train_x=x, train_y=target_y, feature_mean=feature_mean, feature_scale=feature_scale, lengthscale=lengthscale, outputscale=np.asarray(outputscale), train_noise=np.asarray(train_noise), observation_noise=np.asarray(observation_noise), mean=np.asarray(mean_value), precision=precision, alpha=alpha)
        files.append(path)
        counts[target] = len(target_y)
        predictors.append({"id": f"{target.lower()}-gp", "target": target, "unit": unit, "target_kind": "continuous", "runtime_type": "builtin.exact_gp.v1", "architecture_id": "exact_rbf_grouped_v1", "artifact": path.relative_to(destination).as_posix(), "predictive_family": "normal", "feature_names": list(FEATURE_NAMES), "config": {"training_unit": "parent_condition_mean", "estimand_context": {"test_direction": "L", "equipment": "HR-LINE-1"}}})
    stats_path = reference_dir / "training_stats.json"
    stats_path.write_text(json.dumps({"records": counts, "source_sha256": data.source_sha256, "composition_defaults": data.medians}, ensure_ascii=False, indent=2), encoding="utf-8")
    files.append(stats_path)
    sample = _candidate(rows[0]).model_copy(update={"name": "hot rolling package smoke"})
    smoke_input = smoke_dir / "input.json"
    smoke_input.write_text(sample.model_dump_json(indent=2), encoding="utf-8")
    raw = build_hot_rolling_features(sample, data.medians).values
    smoke_expected = smoke_dir / "expected.json"
    smoke_expected.write_text(json.dumps({target: round(_point(artifact_dir / f"{target}.npz", raw), 8) for target in TARGETS}, indent=2), encoding="utf-8")
    files.extend([smoke_input, smoke_expected])
    manifest = {"schema_version": "model-package/v1", "package_id": PACKAGE_ID, "package_version": PACKAGE_VERSION, "task_id": TASK_ID, "input_schema_version": INPUT_SCHEMA_VERSION, "feature_pipeline": {"id": PIPELINE_ID, "version": PIPELINE_VERSION, "spec": pipeline_path.relative_to(destination).as_posix(), "canonical_input_paths": list(CANONICAL_INPUT_PATHS), "output_features": list(FEATURE_NAMES), "artifacts": [stats_path.relative_to(destination).as_posix()]}, "predictors": predictors, "provenance": {"training_data_id": f"sha256:{data.source_sha256}", "feature_dataset_id": f"sha256:{_digest(stats_path)}", "training_code_revision": TRAINING_CODE_REVISION}, "artifacts": [_artifact(destination, path) for path in files], "smoke_test": {"input": smoke_input.relative_to(destination).as_posix(), "expected": smoke_expected.relative_to(destination).as_posix()}}
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    build(Path("data/source/process_dashboard_realistic_excel_v2.xlsx"), Path("models/packages/hot-rolled-gp-2026-07"))
