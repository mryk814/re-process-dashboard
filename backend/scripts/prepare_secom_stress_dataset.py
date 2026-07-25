"""Normalize the official UCI SECOM files and emit a bounded diagnostic."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path


PUBLISHED_FEATURE_COUNT = 591


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(raw_root: Path, output: Path, report: Path) -> None:
    data_path = raw_root / "secom.data"
    labels_path = raw_root / "secom_labels.data"
    feature_rows = [
        [None if value == "NaN" else float(value) for value in line.split()]
        for line in data_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    label_rows = [
        line.split(maxsplit=1)
        for line in labels_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(feature_rows) != len(label_rows):
        raise ValueError("SECOM feature and label row counts differ")
    feature_count = len(feature_rows[0])
    if any(len(row) != feature_count for row in feature_rows):
        raise ValueError("SECOM rows do not have a consistent feature width")

    output.parent.mkdir(parents=True, exist_ok=True)
    feature_names = [f"sensor_{index:03d}" for index in range(feature_count)]
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("sample_id", "measured_at", *feature_names, "is_fail", "yield_label"))
        for index, (features, (label, measured_at)) in enumerate(
            zip(feature_rows, label_rows, strict=True),
            start=1,
        ):
            writer.writerow((
                f"SECOM-{index:04d}",
                measured_at.strip('"'),
                *("" if value is None else f"{value:.12g}" for value in features),
                1 if int(label) == 1 else 0,
                "fail" if int(label) == 1 else "pass",
            ))

    missing_counts = [
        sum(row[index] is None for row in feature_rows)
        for index in range(feature_count)
    ]
    finite_unique = [
        len({row[index] for row in feature_rows if row[index] is not None})
        for index in range(feature_count)
    ]
    labels = [int(label) for label, _ in label_rows]
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({
        "schema_version": "secom-stress-diagnostic/v1",
        "source": {
            "provider": "UCI Machine Learning Repository",
            "dataset": "SECOM",
            "doi": "10.24432/C54305",
            "license": "CC BY 4.0",
            "landing_page": "https://archive.ics.uci.edu/dataset/179/secom",
            "files": {
                data_path.name: _sha256(data_path),
                labels_path.name: _sha256(labels_path),
            },
        },
        "shape": {
            "rows": len(feature_rows),
            "sensor_features": feature_count,
            "published_sensor_features": PUBLISHED_FEATURE_COUNT,
            "published_width_matches_file": feature_count == PUBLISHED_FEATURE_COUNT,
            "missing_cells": sum(missing_counts),
            "missing_fraction": sum(missing_counts) / (len(feature_rows) * feature_count),
            "all_missing_features": sum(count == len(feature_rows) for count in missing_counts),
            "constant_after_missing_features": sum(unique <= 1 for unique in finite_unique),
            "features_over_90_percent_missing": sum(
                count / len(feature_rows) >= 0.9 for count in missing_counts
            ),
        },
        "derived_csv_sha256": _sha256(output),
        "target": {
            "pass": labels.count(-1),
            "fail": labels.count(1),
            "fail_fraction": labels.count(1) / len(labels),
        },
        "recommended_preprocessing_contract": {
            "split_before_fit": True,
            "split_strategy": "stratified; timestamp retained for temporal sensitivity analysis",
            "drop_all_missing_and_constant": True,
            "imputation": "training-fold median only",
            "scaling": "training-fold standardization only",
            "feature_selection": "target-informed selection inside each training fold; UI representative sensors selected only by completeness and variability",
            "primary_metric": "balanced error rate",
        },
        "product_boundary": {
            "dataset_import": "supported",
            "quality_inspection": "supported through this diagnostic",
            "candidate_editor": "12 representative sensors selected by nested stratified-fold stability; never a flat 590-field form",
            "prediction_runtime": "binary classification with calibrated fail probability",
            "project_creation": "enabled as a real-data demonstration task",
        },
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/source/external/secom_stress.csv"),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("docs/reports/secom-stress-diagnostic.json"),
    )
    args = parser.parse_args()
    build(args.raw_root, args.output, args.report)
    print(f"Wrote {args.output} and {args.report}")


if __name__ == "__main__":
    main()
