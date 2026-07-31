from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_calce_derived_dataset_and_report_are_consistent() -> None:
    source = ROOT / "data/source/external/battery_calce_cs2_cycles.csv"
    report = json.loads(
        (ROOT / "docs/reports/battery-calce-cs2-derivation.json").read_text(encoding="utf-8")
    )
    with source.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert len(rows) == report["rows"] == 3131
    assert _sha256(source) == report["derived_csv_sha256"]
    assert {row["cell_id"] for row in rows} == {"CS2_33", "CS2_34", "CS2_35", "CS2_36"}
    assert {float(row["discharge_rate_c"]) for row in rows} == {0.5, 1.0}
    assert all(float(row["capacity_ah"]) > 0 for row in rows)


def test_secom_stress_fixture_preserves_missingness_and_class_imbalance() -> None:
    source = ROOT / "data/source/external/secom_stress.csv"
    report = json.loads(
        (ROOT / "docs/reports/secom-stress-diagnostic.json").read_text(encoding="utf-8")
    )
    with source.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        rows = list(reader)

    assert len(rows) == report["shape"]["rows"] == 1567
    assert len(header) == 594  # sample id, timestamp, 590 sensors, numeric target, label
    assert _sha256(source) == report["derived_csv_sha256"]
    assert report["shape"]["published_sensor_features"] == 591
    assert report["shape"]["sensor_features"] == 590
    assert report["shape"]["published_width_matches_file"] is False
    assert report["shape"]["missing_cells"] == 41951
    assert report["shape"]["constant_after_missing_features"] == 116
    assert report["target"] == {
        "pass": 1463,
        "fail": 104,
        "fail_fraction": 104 / 1567,
    }
    assert report["product_boundary"]["project_creation"].startswith("enabled")
    assert report["product_boundary"]["prediction_runtime"].startswith("binary classification")


def test_secom_sensor_selection_is_nested_and_matches_the_profile() -> None:
    report = json.loads(
        (ROOT / "docs/reports/secom-sensor-selection.json").read_text(encoding="utf-8")
    )
    profile = json.loads(
        (
            ROOT
            / "backend/src/decision_workbench/data/tabular-profile-secom-yield-v1.json"
        ).read_text(encoding="utf-8")
    )
    selected = [item["column"] for item in profile["inputs"]]

    assert selected == [
        item["column"] for item in report["consensus_sensors"]
    ]
    assert len(selected) == 12
    assert report["method"]["outer_split"] == "5-fold stratified"
    assert 0.5 < report["nested_oof_diagnostic"]["roc_auc"] < 1
