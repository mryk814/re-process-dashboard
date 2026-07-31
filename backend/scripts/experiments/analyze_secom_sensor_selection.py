"""Reproduce the nested-fold sensor selection used by the SECOM UI task."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import lightgbm as lgb
import numpy as np
from scipy.stats import rankdata


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parameters(seed: int) -> dict[str, object]:
    return {
        "objective": "binary",
        "metric": "binary_logloss",
        "learning_rate": 0.03,
        "num_leaves": 15,
        "max_depth": 5,
        "min_data_in_leaf": 25,
        "lambda_l2": 2.0,
        "verbosity": -1,
        "deterministic": True,
        "force_col_wise": True,
        "num_threads": 1,
        "seed": seed,
    }


def analyze(source: Path, profile_path: Path, output: Path) -> None:
    with source.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    sensor_columns = [
        name for name in rows[0]
        if name.startswith("sensor_")
    ]
    features = np.asarray([
        [
            np.nan if row[column] == "" else float(row[column])
            for column in sensor_columns
        ]
        for row in rows
    ])
    target = np.asarray([float(row["is_fail"]) for row in rows])
    fold_assignment = np.empty(len(target), dtype=int)
    for label in (0.0, 1.0):
        indexes = np.flatnonzero(target == label)
        fold_assignment[indexes] = np.arange(len(indexes)) % 5

    frequency = {column: 0 for column in sensor_columns}
    total_gain = {column: 0.0 for column in sensor_columns}
    oof_probability = np.empty(len(target))
    selections: list[dict[str, object]] = []
    for fold in range(5):
        train = fold_assignment != fold
        test = ~train
        usable = np.asarray([
            np.isfinite(features[train, index]).any()
            and len(np.unique(features[train, index][np.isfinite(features[train, index])])) > 1
            for index in range(len(sensor_columns))
        ])
        fold_columns = [
            column for column, keep in zip(sensor_columns, usable, strict=True)
            if keep
        ]
        fold_features = features[:, usable]
        scout = lgb.train(
            _parameters(20260725 + fold),
            lgb.Dataset(fold_features[train], label=target[train]),
            num_boost_round=180,
        )
        gain = scout.feature_importance(importance_type="gain")
        selected_indexes = np.argsort(gain)[-12:][::-1]
        selected = [fold_columns[index] for index in selected_indexes]
        for column, index in zip(selected, selected_indexes, strict=True):
            frequency[column] += 1
            total_gain[column] += float(gain[index])
        fitted = lgb.train(
            _parameters(20260730 + fold),
            lgb.Dataset(
                fold_features[train][:, selected_indexes],
                label=target[train],
            ),
            num_boost_round=180,
        )
        oof_probability[test] = fitted.predict(
            fold_features[test][:, selected_indexes]
        )
        selections.append({"fold": fold, "sensors": selected})

    consensus = sorted(
        sensor_columns,
        key=lambda column: (frequency[column], total_gain[column]),
        reverse=True,
    )[:12]
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    configured = [
        item["column"] for item in profile["inputs"]
    ]
    if configured != consensus:
        raise ValueError(
            f"SECOM profile sensors differ from nested-fold consensus: {configured} != {consensus}"
        )

    positive = target == 1
    positive_count = int(positive.sum())
    negative_count = len(target) - positive_count
    auc = float(
        (
            rankdata(oof_probability)[positive].sum()
            - positive_count * (positive_count + 1) / 2
        )
        / (positive_count * negative_count)
    )
    predicted = oof_probability >= 0.5
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "schema_version": "secom-sensor-selection/v1",
        "source_sha256": _sha256(source),
        "method": {
            "outer_split": "5-fold stratified",
            "scout_model": "LightGBM binary, 180 rounds",
            "within_fold_selection": "top 12 by gain after dropping all-missing and constant training columns",
            "consensus": "frequency across folds, then summed gain",
        },
        "fold_selections": selections,
        "consensus_sensors": [
            {
                "column": column,
                "fold_frequency": frequency[column],
                "summed_gain": total_gain[column],
            }
            for column in consensus
        ],
        "nested_oof_diagnostic": {
            "rows": len(target),
            "roc_auc": auc,
            "brier_score": float(np.mean((oof_probability - target) ** 2)),
            "balanced_accuracy_at_0_5": float(
                (
                    np.mean(predicted[positive])
                    + np.mean(~predicted[~positive])
                )
                / 2
            ),
        },
        "interpretation_limit": (
            "Selection stability supports a compact UI; anonymous sensors and "
            "observational association do not support causal interpretation."
        ),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/source/external/secom_stress.csv"),
    )
    parser.add_argument(
        "--profile",
        type=Path,
        default=Path(
            "backend/src/decision_workbench/data/tabular-profile-secom-yield-v1.json"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/reports/secom-sensor-selection.json"),
    )
    args = parser.parse_args()
    analyze(args.source, args.profile, args.output)
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
