"""Reproducible, grouped comparison of independent and shared-output exact GPs.

This is an experiment, not a package builder. It never writes to data/source,
the model package catalog, or active-packages.json.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from material_workbench.data.importer import (  # noqa: E402
    load_workbook_data,
    training_context_key,
)
from material_workbench.modeling.runtime import (  # noqa: E402
    TARGETS,
    ModelRuntime,
)
from material_workbench.tasks.task_registry import load_task_contracts  # noqa: E402


TARGET_ORDER = ("TS", "YS", "EL", "lambda")
NOMINAL_LEVELS = (0.50, 0.80, 0.90, 0.95)
LENGTH_MULTIPLIERS = (0.5, 1.0, 2.0)
NOISE_GRID = (0.02, 0.08, 0.20)
COREGIONALIZATION_SHRINKAGE = 0.25
NUMERICAL_VARIANCE_TOLERANCE = 1e-9


@dataclass(frozen=True)
class GPState:
    train_x: np.ndarray
    centered_y: np.ndarray
    target_mean: np.ndarray
    target_scale: np.ndarray
    coregionalization: np.ndarray
    lengthscale: float
    noise: np.ndarray
    cholesky: np.ndarray
    alpha: np.ndarray


def _raw_grouped_rows(
    runtime: ModelRuntime,
) -> tuple[tuple[str, ...], np.ndarray, np.ndarray]:
    by_target: dict[str, dict[str, tuple[np.ndarray, float]]] = {}
    for target in TARGET_ORDER:
        model = runtime.models[target]
        output_column = TARGETS[target][0]
        grouped: dict[str, list[tuple[np.ndarray, float]]] = {}
        for index, row in enumerate(model.rows):
            key = training_context_key(row)
            raw_x = (
                model.x_train[index] * model.feature_scale + model.feature_mean
            )
            grouped.setdefault(key, []).append(
                (raw_x, float(row["outputs"][output_column]))
            )
        by_target[target] = {
            key: (
                np.mean([item[0] for item in values], axis=0),
                float(np.mean([item[1] for item in values])),
            )
            for key, values in grouped.items()
        }
    keys = tuple(
        sorted(set.intersection(*(set(rows) for rows in by_target.values())))
    )
    if len(keys) < 20:
        raise ValueError("shared-output比較には20以上のcomplete parent conditionsが必要です")
    x = np.vstack([by_target[TARGET_ORDER[0]][key][0] for key in keys])
    y = np.asarray(
        [
            [by_target[target][key][1] for target in TARGET_ORDER]
            for key in keys
        ],
        dtype=float,
    )
    for target in TARGET_ORDER[1:]:
        candidate = np.vstack([by_target[target][key][0] for key in keys])
        if not np.allclose(candidate, x, rtol=0, atol=1e-8):
            raise ValueError(
                f"{target}のcomplete cohortでcanonical inputが他targetと一致しません"
            )
    return keys, x, y


def _fold_for_key(key: str, folds: int, seed: int) -> int:
    digest = hashlib.sha256(f"{seed}:{key}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % folds


def _standardize_x(
    train_x: np.ndarray,
    test_x: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-10] = 1.0
    return (train_x - mean) / scale, (test_x - mean) / scale


def _rbf(left: np.ndarray, right: np.ndarray, lengthscale: float) -> np.ndarray:
    squared = np.sum(
        (left[:, None, :] - right[None, :, :]) ** 2,
        axis=2,
    )
    return np.exp(-0.5 * squared / (lengthscale * lengthscale))


def _median_distance(x: np.ndarray) -> float:
    squared = np.sum((x[:, None, :] - x[None, :, :]) ** 2, axis=2)
    distances = np.sqrt(squared[np.triu_indices(len(x), k=1)])
    positive = distances[distances > 1e-12]
    return max(float(np.median(positive)), 0.25)


def _factor_and_score(
    covariance: np.ndarray,
    centered_y: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    cholesky = np.linalg.cholesky(covariance)
    alpha = np.linalg.solve(
        cholesky.T,
        np.linalg.solve(cholesky, centered_y),
    )
    score = float(
        0.5 * centered_y @ alpha
        + np.log(np.diag(cholesky)).sum()
        + 0.5 * len(centered_y) * np.log(2.0 * np.pi)
    )
    return cholesky, alpha, score


def _coregionalization(y: np.ndarray) -> np.ndarray:
    correlation = np.corrcoef(y, rowvar=False)
    if not np.isfinite(correlation).all():
        raise ValueError("target相関行列に有限でない値があります")
    matrix = (
        (1.0 - COREGIONALIZATION_SHRINKAGE) * correlation
        + COREGIONALIZATION_SHRINKAGE * np.eye(y.shape[1])
    )
    eigenvalues = np.linalg.eigvalsh(matrix)
    if float(eigenvalues.min()) <= 0:
        raise ValueError("coregionalization covarianceが正定値ではありません")
    return matrix


def _fit_shared(train_x: np.ndarray, train_y: np.ndarray) -> GPState:
    target_mean = train_y.mean(axis=0)
    target_scale = train_y.std(axis=0)
    target_scale[target_scale < 1e-10] = 1.0
    standardized_y = (train_y - target_mean) / target_scale
    coregionalization = _coregionalization(standardized_y)
    median = _median_distance(train_x)
    vector = standardized_y.T.reshape(-1)
    identity = np.eye(len(train_x))
    candidates: list[tuple[float, float, np.ndarray, np.ndarray, float]] = []
    for multiplier in LENGTH_MULTIPLIERS:
        lengthscale = median * multiplier
        kernel = _rbf(train_x, train_x, lengthscale)
        for noise in NOISE_GRID:
            covariance = np.kron(coregionalization, kernel) + np.kron(
                np.eye(train_y.shape[1]) * noise,
                identity,
            )
            covariance.flat[:: len(covariance) + 1] += 1e-8
            cholesky, alpha, score = _factor_and_score(covariance, vector)
            candidates.append(
                (lengthscale, noise, cholesky, alpha, score)
            )
    lengthscale, noise, cholesky, alpha, _ = min(
        candidates,
        key=lambda item: item[-1],
    )
    return GPState(
        train_x=train_x,
        centered_y=vector,
        target_mean=target_mean,
        target_scale=target_scale,
        coregionalization=coregionalization,
        lengthscale=lengthscale,
        noise=np.full(train_y.shape[1], noise),
        cholesky=cholesky,
        alpha=alpha,
    )


def _fit_independent(train_x: np.ndarray, train_y: np.ndarray) -> tuple[GPState, ...]:
    median = _median_distance(train_x)
    states: list[GPState] = []
    for target_index in range(train_y.shape[1]):
        values = train_y[:, target_index]
        target_mean = float(values.mean())
        target_scale = max(float(values.std()), 1e-10)
        centered = (values - target_mean) / target_scale
        candidates: list[tuple[float, float, np.ndarray, np.ndarray, float]] = []
        for multiplier in LENGTH_MULTIPLIERS:
            lengthscale = median * multiplier
            kernel = _rbf(train_x, train_x, lengthscale)
            for noise in NOISE_GRID:
                covariance = kernel + np.eye(len(train_x)) * (noise + 1e-8)
                cholesky, alpha, score = _factor_and_score(
                    covariance,
                    centered,
                )
                candidates.append(
                    (lengthscale, noise, cholesky, alpha, score)
                )
        lengthscale, noise, cholesky, alpha, _ = min(
            candidates,
            key=lambda item: item[-1],
        )
        states.append(
            GPState(
                train_x=train_x,
                centered_y=centered,
                target_mean=np.asarray([target_mean]),
                target_scale=np.asarray([target_scale]),
                coregionalization=np.ones((1, 1)),
                lengthscale=lengthscale,
                noise=np.asarray([noise]),
                cholesky=cholesky,
                alpha=alpha,
            )
        )
    return tuple(states)


def _checked_variance(value: np.ndarray, label: str) -> np.ndarray:
    minimum = float(np.min(value))
    if minimum < -NUMERICAL_VARIANCE_TOLERANCE:
        raise ValueError(f"{label}で負の予測分散を検出しました: {minimum}")
    if minimum < 0:
        # This is an explicit numerical-tolerance decision and is reported.
        value = value.copy()
        value[value < 0] = 0
    return value


def _predict_shared(
    state: GPState,
    test_x: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    cross_x = _rbf(test_x, state.train_x, state.lengthscale)
    cross = np.kron(state.coregionalization, cross_x)
    mean_standardized = cross @ state.alpha
    solve = np.linalg.solve(state.cholesky, cross.T)
    prior = np.kron(
        state.coregionalization,
        _rbf(test_x, test_x, state.lengthscale),
    )
    posterior = prior - solve.T @ solve + np.kron(
        np.diag(state.noise),
        np.eye(len(test_x)),
    )
    variance = _checked_variance(
        np.diag(posterior),
        "shared-output GP",
    )
    target_count = len(state.target_mean)
    mean = mean_standardized.reshape(target_count, len(test_x)).T
    standard_deviation = np.sqrt(
        variance.reshape(target_count, len(test_x)).T
    )
    return (
        mean * state.target_scale + state.target_mean,
        standard_deviation * state.target_scale,
    )


def _predict_independent(
    states: tuple[GPState, ...],
    test_x: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    means: list[np.ndarray] = []
    standard_deviations: list[np.ndarray] = []
    for state in states:
        cross = _rbf(test_x, state.train_x, state.lengthscale)
        mean = cross @ state.alpha
        solve = np.linalg.solve(state.cholesky, cross.T)
        prior = np.ones(len(test_x))
        variance = _checked_variance(
            prior - np.sum(solve * solve, axis=0) + state.noise[0],
            "single-target GP",
        )
        means.append(mean * state.target_scale[0] + state.target_mean[0])
        standard_deviations.append(
            np.sqrt(variance) * state.target_scale[0]
        )
    return np.column_stack(means), np.column_stack(standard_deviations)


def _metric(
    actual: np.ndarray,
    mean: np.ndarray,
    standard_deviation: np.ndarray,
) -> dict[str, Any]:
    residual = actual - mean
    coverage: dict[str, float] = {}
    for level in NOMINAL_LEVELS:
        z = NormalDist().inv_cdf((1.0 + level) / 2.0)
        coverage[f"{level:.2f}"] = float(
            np.mean(np.abs(residual) <= z * standard_deviation)
        )
    calibration_error = float(
        np.mean(
            [
                abs(coverage[f"{level:.2f}"] - level)
                for level in NOMINAL_LEVELS
            ]
        )
    )
    return {
        "mae": float(np.mean(np.abs(residual))),
        "rmse": float(np.sqrt(np.mean(residual * residual))),
        "coverage": coverage,
        "calibration_error": calibration_error,
    }


def _npz_bytes(arrays: dict[str, np.ndarray]) -> bytes:
    output = io.BytesIO()
    np.savez(output, **arrays)
    return output.getvalue()


def _benchmark(
    train_x: np.ndarray,
    train_y: np.ndarray,
) -> dict[str, dict[str, float | int]]:
    shared = _fit_shared(train_x, train_y)
    independent = _fit_independent(train_x, train_y)
    shared_arrays = {
        "train_x": shared.train_x,
        "target_order": np.asarray(TARGET_ORDER),
        "target_mean": shared.target_mean,
        "target_scale": shared.target_scale,
        "coregionalization": shared.coregionalization,
        "noise": shared.noise,
        "cholesky": shared.cholesky,
        "alpha": shared.alpha,
    }
    independent_arrays: dict[str, np.ndarray] = {
        "train_x": train_x,
        "target_order": np.asarray(TARGET_ORDER),
    }
    for index, state in enumerate(independent):
        independent_arrays[f"target_{index}_mean"] = state.target_mean
        independent_arrays[f"target_{index}_scale"] = state.target_scale
        independent_arrays[f"target_{index}_noise"] = state.noise
        independent_arrays[f"target_{index}_cholesky"] = state.cholesky
        independent_arrays[f"target_{index}_alpha"] = state.alpha
    payloads = {
        "single_target": _npz_bytes(independent_arrays),
        "shared_output": _npz_bytes(shared_arrays),
    }
    result: dict[str, dict[str, float | int]] = {}
    probe = np.repeat(train_x[:1], 100, axis=0)
    for name, payload in payloads.items():
        started = time.perf_counter()
        for _ in range(20):
            with np.load(io.BytesIO(payload), allow_pickle=False) as archive:
                tuple(archive.files)
                for key in archive.files:
                    archive[key]
        load_ms = (time.perf_counter() - started) * 1000 / 20
        started = time.perf_counter()
        if name == "shared_output":
            _predict_shared(shared, probe)
        else:
            _predict_independent(independent, probe)
        inference_ms = (time.perf_counter() - started) * 1000
        result[name] = {
            "artifact_bytes": len(payload),
            "mean_load_ms": load_ms,
            "batch_100_inference_ms": inference_ms,
        }
    return result


def _evaluate_cohort(
    keys: tuple[str, ...],
    raw_x: np.ndarray,
    y: np.ndarray,
    *,
    folds: int,
    seed: int,
) -> dict[str, Any]:
    fold_ids = np.asarray([_fold_for_key(key, folds, seed) for key in keys])
    if len(set(fold_ids.tolist())) != folds:
        raise ValueError("deterministic grouped splitに空foldがあります")
    predictions = {
        "single_target": {
            "mean": np.empty_like(y),
            "sd": np.empty_like(y),
        },
        "shared_output": {
            "mean": np.empty_like(y),
            "sd": np.empty_like(y),
        },
    }
    for fold in range(folds):
        test_mask = fold_ids == fold
        train_mask = ~test_mask
        train_x, test_x = _standardize_x(raw_x[train_mask], raw_x[test_mask])
        independent = _fit_independent(train_x, y[train_mask])
        shared = _fit_shared(train_x, y[train_mask])
        independent_mean, independent_sd = _predict_independent(
            independent,
            test_x,
        )
        shared_mean, shared_sd = _predict_shared(shared, test_x)
        predictions["single_target"]["mean"][test_mask] = independent_mean
        predictions["single_target"]["sd"][test_mask] = independent_sd
        predictions["shared_output"]["mean"][test_mask] = shared_mean
        predictions["shared_output"]["sd"][test_mask] = shared_sd
    metrics: dict[str, dict[str, Any]] = {}
    for approach, prediction in predictions.items():
        metrics[approach] = {
            target: _metric(
                y[:, index],
                prediction["mean"][:, index],
                prediction["sd"][:, index],
            )
            for index, target in enumerate(TARGET_ORDER)
        }
    negative_transfer: dict[str, dict[str, float | bool]] = {}
    for target in TARGET_ORDER:
        single = metrics["single_target"][target]
        shared = metrics["shared_output"][target]
        rmse_change = (
            shared["rmse"] - single["rmse"]
        ) / single["rmse"]
        calibration_change = (
            shared["calibration_error"] - single["calibration_error"]
        )
        negative_transfer[target] = {
            "rmse_relative_change": rmse_change,
            "calibration_error_change": calibration_change,
            "detected": bool(
                rmse_change > 0.03 or calibration_change > 0.03
            ),
        }
    improved_targets = sum(
        item["rmse_relative_change"] <= -0.02
        for item in negative_transfer.values()
    )
    any_material_negative_transfer = any(
        item["detected"] for item in negative_transfer.values()
    )
    mean_single_calibration = float(
        np.mean(
            [
                metrics["single_target"][target]["calibration_error"]
                for target in TARGET_ORDER
            ]
        )
    )
    mean_shared_calibration = float(
        np.mean(
            [
                metrics["shared_output"][target]["calibration_error"]
                for target in TARGET_ORDER
            ]
        )
    )
    adoption = (
        improved_targets >= 3
        and not any_material_negative_transfer
        and mean_shared_calibration <= mean_single_calibration + 0.02
    )
    return {
        "parent_conditions": len(keys),
        "metrics": metrics,
        "negative_transfer": negative_transfer,
        "decision": {
            "adopt": adoption,
            "improved_target_count": improved_targets,
            "material_negative_transfer": any_material_negative_transfer,
            "mean_single_target_calibration_error": mean_single_calibration,
            "mean_shared_output_calibration_error": mean_shared_calibration,
        },
    }


def evaluate(
    source: Path,
    *,
    folds: int = 5,
    seed: int = 20260726,
) -> dict[str, Any]:
    data = load_workbook_data(source)
    runtime = ModelRuntime(data, load_package=False)
    keys, raw_x, y = _raw_grouped_rows(runtime)
    primary = _evaluate_cohort(
        keys,
        raw_x,
        y,
        folds=folds,
        seed=seed,
    )
    task = load_task_contracts()[runtime.task_id].task_definition
    bounds = {
        output.key: (
            output.plausibility_range.min,
            output.plausibility_range.max,
        )
        for output in task.outputs
        if output.plausibility_range is not None
    }
    clean_mask = np.asarray(
        [
            all(
                bounds[target][0] <= row[index] <= bounds[target][1]
                for index, target in enumerate(TARGET_ORDER)
            )
            for row in y
        ],
        dtype=bool,
    )
    clean_keys = tuple(
        key for key, keep in zip(keys, clean_mask, strict=True) if keep
    )
    sensitivity = _evaluate_cohort(
        clean_keys,
        raw_x[clean_mask],
        y[clean_mask],
        folds=folds,
        seed=seed,
    )
    standardized_full_x, _ = _standardize_x(raw_x, raw_x)
    final_adoption = bool(
        primary["decision"]["adopt"]
        and sensitivity["decision"]["adopt"]
    )
    result = {
        "schema_version": "shared-multi-output-gp-evaluation/v1",
        "generated_from": {
            "source": source.as_posix(),
            "source_sha256": data.source_sha256,
            "task_id": runtime.task_id,
            "feature_pipeline_version": runtime.feature_pipeline_version,
        },
        "evaluation_policy": {
            "cohort": "complete parent-condition means shared by all targets",
            "parent_conditions": len(keys),
            "target_order": list(TARGET_ORDER),
            "split": f"{folds}-fold deterministic grouped by parent condition",
            "seed": seed,
            "input_kernel": "standardized isotropic RBF; fold-local grid search",
            "coregionalization": (
                "fold-local empirical target correlation with "
                f"{COREGIONALIZATION_SHRINKAGE:.2f} identity shrinkage"
            ),
            "interval": "normal posterior predictive including fitted noise",
            "demo_data_warning": (
                "Synthetic demo data; this result tests application and "
                "model-contract utility, not a scientific causal claim."
            ),
        },
        "target_correlation": {
            target: {
                other: float(np.corrcoef(y[:, i], y[:, j])[0, 1])
                for j, other in enumerate(TARGET_ORDER)
            }
            for i, target in enumerate(TARGET_ORDER)
        },
        "metrics": primary["metrics"],
        "negative_transfer": primary["negative_transfer"],
        "sensitivity": {
            "policy": (
                "secondary analysis excluding parent conditions with any "
                "target outside TaskDefinition.plausibility_range; rows are "
                "not removed from the primary evaluation"
            ),
            "excluded_parent_conditions": len(keys) - len(clean_keys),
            **sensitivity,
        },
        "benchmark": _benchmark(standardized_full_x, y),
        "decision": {
            "adopt_runtime_and_package": final_adoption,
            "primary": primary["decision"],
            "plausibility_clean_sensitivity": sensitivity["decision"],
            "rule": (
                "adopt only when at least 3/4 targets improve RMSE by >=2%, "
                "no target has >3% RMSE or calibration degradation, and mean "
                "calibration error is not >0.02 worse in both the primary "
                "and plausibility-clean sensitivity cohorts"
            ),
        },
    }
    return result


def _markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Shared multi-output GP 再評価",
        "",
        "> 合成デモデータによるアプリ・モデル契約の評価であり、材料現象の因果的根拠ではない。",
        "",
        f"- source SHA-256: `{report['generated_from']['source_sha256']}`",
        f"- cohort: {report['evaluation_policy']['parent_conditions']} parent conditions",
        f"- split: {report['evaluation_policy']['split']}",
        f"- 最終判断: **{'採用' if report['decision']['adopt_runtime_and_package'] else '見送り'}**",
        "",
        "## Complete cohortのtarget相関",
        "",
        "| Target | TS | YS | EL | lambda |",
        "|---|---:|---:|---:|---:|",
    ]
    for target in TARGET_ORDER:
        lines.append(
            f"| {target} | "
            + " | ".join(
                f"{report['target_correlation'][target][other]:+.3f}"
                for other in TARGET_ORDER
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "強い相関は合成デモ生成過程の特徴であり、共有modelの採用根拠にはしない。",
            "",
        "## Target別比較",
        "",
        "| Target | Model | MAE | RMSE | 90% coverage | Calibration error |",
        "|---|---|---:|---:|---:|---:|",
        ]
    )
    for target in TARGET_ORDER:
        for approach, label in (
            ("single_target", "Single"),
            ("shared_output", "Shared"),
        ):
            metric = report["metrics"][approach][target]
            lines.append(
                f"| {target} | {label} | {metric['mae']:.4f} | "
                f"{metric['rmse']:.4f} | {metric['coverage']['0.90']:.3f} | "
                f"{metric['calibration_error']:.3f} |"
            )
    lines.extend(
        [
            "",
            "## Plausibility-clean sensitivity",
            "",
            (
                f"TaskDefinitionの物理範囲外を含む "
                f"{report['sensitivity']['excluded_parent_conditions']} "
                "parent conditionsを副解析だけから除外した。主解析からは削除していない。"
            ),
            "",
            "| Target | Single RMSE | Shared RMSE | Shared変化 |",
            "|---|---:|---:|---:|",
        ]
    )
    for target in TARGET_ORDER:
        single = report["sensitivity"]["metrics"]["single_target"][target]
        shared = report["sensitivity"]["metrics"]["shared_output"][target]
        change = (shared["rmse"] - single["rmse"]) / single["rmse"]
        lines.append(
            f"| {target} | {single['rmse']:.4f} | "
            f"{shared['rmse']:.4f} | {change:+.1%} |"
        )
    lines.extend(
        [
            "",
            "## 負の転移",
            "",
            "| Target | RMSE変化 | Calibration error変化 | 判定 |",
            "|---|---:|---:|---|",
        ]
    )
    for target in TARGET_ORDER:
        transfer = report["negative_transfer"][target]
        lines.append(
            f"| {target} | {transfer['rmse_relative_change']:+.1%} | "
            f"{transfer['calibration_error_change']:+.3f} | "
            f"{'あり' if transfer['detected'] else 'なし'} |"
        )
    lines.extend(
        [
            "",
            "## 成果物と性能",
            "",
            "| Model | Artifact bytes | Load ms | Batch 100 inference ms |",
            "|---|---:|---:|---:|",
        ]
    )
    for approach, label in (
        ("single_target", "Single"),
        ("shared_output", "Shared"),
    ):
        benchmark = report["benchmark"][approach]
        lines.append(
            f"| {label} | {benchmark['artifact_bytes']:,} | "
            f"{benchmark['mean_load_ms']:.3f} | "
            f"{benchmark['batch_100_inference_ms']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## 判断ルール",
            "",
            report["decision"]["rule"],
            "",
            "Runtime／Model Packageを採用しない判断の場合、adapter、Package、active設定は追加しない。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/source/material_workbench_process_v1.xlsx"),
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=Path("docs/reports/shared-multi-output-gp-evaluation.json"),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=Path("docs/reports/shared-multi-output-gp-evaluation.md"),
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--seed", type=int, default=20260726)
    args = parser.parse_args()
    source_root = (Path.cwd() / "data/source").resolve()
    for output in (args.json_output, args.markdown_output):
        resolved = output.resolve()
        if resolved == source_root or source_root in resolved.parents:
            raise ValueError("評価レポートを読取専用data/sourceへ出力できません")
    report = evaluate(args.source, folds=args.folds, seed=args.seed)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    args.markdown_output.write_text(
        _markdown(report),
        encoding="utf-8",
        newline="\n",
    )
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "json": str(args.json_output),
                "markdown": str(args.markdown_output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
