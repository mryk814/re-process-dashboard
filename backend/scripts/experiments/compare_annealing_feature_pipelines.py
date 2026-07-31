from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from decision_workbench.data.importer import load_workbook_data, training_context_key
from decision_workbench.modeling.feature_pipeline import (
    FEATURE_DEFINITIONS,
    FEATURE_PIPELINE_VERSION,
    V2_FEATURE_NAMES,
    V2_FEATURE_PIPELINE_VERSION,
    build_feature_bundle,
    build_feature_bundle_v2,
    candidate_from_observation,
)
from decision_workbench.modeling.numeric_canonicalization import (
    canonicalize_report_float,
)
from decision_workbench.modeling.runtime import TARGETS


SCHEMA_VERSION = "annealing-feature-pipeline-comparison/v1"
KERNEL_RIDGE_ALPHA = 0.1
LINEAR_RIDGE_ALPHA = 10.0
FOLD_COUNT = 5
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _fold(parent_key: str) -> int:
    digest = hashlib.sha256(parent_key.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % FOLD_COUNT


def _standardize(
    train_x: np.ndarray,
    test_x: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-12] = 1.0
    return (train_x - mean) / scale, (test_x - mean) / scale


def _predict_fixed_rbf(
    train_x: np.ndarray,
    train_y: np.ndarray,
    test_x: np.ndarray,
) -> np.ndarray:
    x, query = _standardize(train_x, test_x)
    target_mean = float(train_y.mean())
    train_distance = np.mean((x[:, None, :] - x[None, :, :]) ** 2, axis=2)
    query_distance = np.mean((query[:, None, :] - x[None, :, :]) ** 2, axis=2)
    kernel = np.exp(-0.5 * train_distance)
    cross = np.exp(-0.5 * query_distance)
    weights = np.linalg.solve(
        kernel + KERNEL_RIDGE_ALPHA * np.eye(len(train_x)),
        train_y - target_mean,
    )
    return target_mean + cross @ weights


def _linear_ridge_coefficients(
    train_x: np.ndarray,
    train_y: np.ndarray,
) -> np.ndarray:
    x, _query = _standardize(train_x, train_x[:1])
    target_mean = float(train_y.mean())
    coefficients = np.linalg.solve(
        x.T @ x + LINEAR_RIDGE_ALPHA * np.eye(x.shape[1]),
        x.T @ (train_y - target_mean),
    )
    return coefficients


def _cv(
    parent_keys: list[str],
    features: dict[str, np.ndarray],
    targets: dict[str, dict[str, float]],
    *,
    coefficient_index: int | None = None,
) -> tuple[dict[str, dict[str, float | int]], dict[str, list[float]]]:
    metrics: dict[str, dict[str, float | int]] = {}
    fold_coefficients: dict[str, list[float]] = defaultdict(list)
    for target, values_by_parent in targets.items():
        keys = [key for key in parent_keys if key in values_by_parent]
        predictions: list[float] = []
        observed: list[float] = []
        for fold in range(FOLD_COUNT):
            train = [key for key in keys if _fold(key) != fold]
            test = [key for key in keys if _fold(key) == fold]
            if not train or not test:
                continue
            train_x = np.vstack([features[key] for key in train])
            train_y = np.asarray([values_by_parent[key] for key in train])
            predicted = _predict_fixed_rbf(
                train_x, train_y, np.vstack([features[key] for key in test])
            )
            predictions.extend(predicted.tolist())
            observed.extend(values_by_parent[key] for key in test)
            if coefficient_index is not None:
                coefficients = _linear_ridge_coefficients(train_x, train_y)
                fold_coefficients[target].append(float(coefficients[coefficient_index]))
        residual = np.asarray(predictions) - np.asarray(observed)
        metrics[target] = {
            "parent_conditions": len(observed),
            "mae": canonicalize_report_float(
                np.mean(np.abs(residual)),
                label=f"{target} annealing comparison MAE",
            ),
            "rmse": canonicalize_report_float(
                np.sqrt(np.mean(residual * residual)),
                label=f"{target} annealing comparison RMSE",
            ),
        }
    return metrics, fold_coefficients


def _correlations(parent_keys: list[str], v4: dict[str, np.ndarray], line_speeds: dict[str, float]) -> list[dict[str, Any]]:
    ls = np.asarray([line_speeds[key] for key in parent_keys])
    matrix = np.vstack([v4[key] for key in parent_keys])
    correlations: list[dict[str, Any]] = []
    for index, definition in enumerate(FEATURE_DEFINITIONS):
        if definition.group != "heat_pattern" or float(np.std(matrix[:, index])) < 1e-12:
            continue
        value = float(np.corrcoef(ls, matrix[:, index])[0, 1])
        pearson_r = canonicalize_report_float(
            value,
            label=f"{definition.name} line-speed correlation",
        )
        correlations.append({
            "feature": definition.name,
            "unit": definition.unit,
            "pearson_r": pearson_r,
            "absolute_r": canonicalize_report_float(
                abs(value),
                label=f"{definition.name} absolute line-speed correlation",
            ),
        })
    return sorted(correlations, key=lambda item: (-item["absolute_r"], item["feature"]))


def compare(source: Path) -> dict[str, Any]:
    data = load_workbook_data(source)
    try:
        source_label = source.resolve().relative_to(REPOSITORY_ROOT).as_posix()
    except ValueError:
        source_label = source.as_posix()
    candidates: dict[str, Any] = {}
    outputs: dict[str, dict[str, list[float]]] = {
        target: defaultdict(list) for target in TARGETS
    }
    for row in data.observations:
        if row.get("task_id") != "annealed-properties-v1" or not row.get("eligible"):
            continue
        candidate = candidate_from_observation(row)
        if candidate is None:
            continue
        parent = training_context_key(row)
        candidates.setdefault(parent, candidate)
        for target, (column, _unit) in TARGETS.items():
            value = row["outputs"].get(column)
            if isinstance(value, (int, float)):
                outputs[target][parent].append(float(value))

    v2: dict[str, np.ndarray] = {}
    v4: dict[str, np.ndarray] = {}
    line_speeds: dict[str, float] = {}
    for parent, candidate in candidates.items():
        v4[parent] = build_feature_bundle(candidate, data.medians).values
        speed = candidate.inputs.process.get("ls_mpm")
        if isinstance(speed, (int, float)):
            v2[parent] = build_feature_bundle_v2(candidate, data.medians).values
            line_speeds[parent] = float(speed)
    comparison_parents = sorted(set(v2) & set(v4))
    target_means = {
        target: {
            parent: float(np.mean(values))
            for parent, values in parent_values.items()
            if parent in comparison_parents
        }
        for target, parent_values in outputs.items()
    }
    v2_metrics, ls_fold_coefficients = _cv(
        comparison_parents,
        v2,
        target_means,
        coefficient_index=V2_FEATURE_NAMES.index("ls_mpm"),
    )
    v4_metrics, _unused = _cv(comparison_parents, v4, target_means)
    stability = {}
    for target, values in ls_fold_coefficients.items():
        array = np.asarray(values)
        nonzero = array[np.abs(array) > 1e-12]
        agreement = (
            max(float(np.mean(nonzero > 0)), float(np.mean(nonzero < 0)))
            if len(nonzero)
            else 0.0
        )
        stability[target] = {
            "standardized_ls_coefficients": [
                canonicalize_report_float(
                    value,
                    label=f"{target} standardized line-speed coefficient",
                )
                for value in values
            ],
            "sign_agreement": canonicalize_report_float(
                agreement,
                label=f"{target} line-speed coefficient sign agreement",
            ),
            "mean": canonicalize_report_float(
                np.mean(array),
                label=f"{target} mean line-speed coefficient",
            ),
            "std": canonicalize_report_float(
                np.std(array),
                label=f"{target} line-speed coefficient standard deviation",
            ),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "source": source_label,
        "source_sha256": data.source_sha256,
        "task_id": "annealed-properties-v1",
        "comparison_policy": {
            "training_unit": "parent_condition_mean",
            "split": "deterministic-parent-condition-5-fold",
            "model": "fixed-standardized-rbf-kernel-ridge",
            "kernel": "exp(-0.5 * mean(standardized squared distance))",
            "kernel_ridge_alpha": KERNEL_RIDGE_ALPHA,
            "ls_stability_model": "standardized-linear-ridge",
            "linear_ridge_alpha": LINEAR_RIDGE_ALPHA,
            "same_parent_folds_for_both_pipelines": True,
        },
        "pipelines": {
            "v2": {
                "version": V2_FEATURE_PIPELINE_VERSION,
                "features": len(V2_FEATURE_NAMES),
                "includes_raw_line_speed": True,
            },
            "v4": {
                "version": FEATURE_PIPELINE_VERSION,
                "features": len(FEATURE_DEFINITIONS),
                "includes_raw_line_speed": False,
            },
        },
        "parents": {
            "v4_eligible": len(v4),
            "v2_eligible": len(v2),
            "comparison": len(comparison_parents),
        },
        "target_metrics": {
            target: {"v2": v2_metrics[target], "v4": v4_metrics[target]}
            for target in TARGETS
            if target in v2_metrics and target in v4_metrics
        },
        "line_speed_heat_feature_correlations": _correlations(
            comparison_parents, v4, line_speeds
        ),
        "line_speed_coefficient_stability": stability,
        "interpretation_limit": (
            "The source is synthetic demo data. CV differences, correlations, and "
            "coefficients evaluate implementation redundancy and numerical behavior; "
            "they are not evidence of metallurgical causality or production accuracy."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/source/material_workbench_process_v1.xlsx"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare(args.source)
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8", newline="\n")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
