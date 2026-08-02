"""Generate the reviewed synthetic assets used by the multi-output Graph fixture.

This script never reads or writes ``data/source``.  The property Tasks retain the
existing Stage C workbook as their runtime source; only Task/Profile projections
are generated.  Workability rows are an explicit deterministic demonstration:

    deposition_efficiency_pct =
        82 - 7 * abs(heat_input_kj_per_mm - 1.35)
           - 0.45 * abs(voltage_v - 28)
           - 0.30 * abs(gas_flow_l_per_min - 18)
           - 0.55 * abs(wire_feed_speed_m_per_min - 8)

The value is clipped to [55, 88].  It is not a measured response and must not be
used as a production model or interpreted causally.
"""
from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TASK_ROOT = ROOT / "backend/src/decision_workbench/tasks/task_definitions"
PROFILE_ROOT = ROOT / "backend/src/decision_workbench/data"
FIXTURE_ROOT = ROOT / "data/fixtures/prediction-graph"

PROPERTY_VARIANTS = {
    "welding-graph-tensile-ts-v1": ("tensile", "TS", "引張強さ"),
    "welding-graph-toughness-v1": ("charpy", "CHARPY_ENERGY", "吸収エネルギー"),
    "welding-graph-corrosion-v1": ("corrosion", "CORROSION_RATE", "腐食速度"),
}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _property_assets() -> None:
    task_fixture = json.loads(
        (TASK_ROOT / "welding-stage-c-properties-v1.json").read_text(encoding="utf-8")
    )
    profile = json.loads(
        (
            PROFILE_ROOT
            / "observation-profile-welding-consumable-stage-c-v1.json"
        ).read_text(encoding="utf-8")
    )
    for task_id, (family_id, target, label) in PROPERTY_VARIANTS.items():
        fixture = deepcopy(task_fixture)
        definition = fixture["task_definition"]
        definition["id"] = task_id
        definition["label"] = f"Graph比較用: {label}"
        definition["outputs"] = [
            output for output in definition["outputs"] if output["key"] == target
        ]
        allowed_inputs = {
            item["path"]
            for family in profile["families"]
            if family["id"] == family_id
            for item in family["inputs"]
        }
        definition["input_groups"] = [
            {
                **group,
                "fields": [
                    field
                    for field in group["fields"]
                    if field["path"] in allowed_inputs
                ],
            }
            for group in definition["input_groups"]
            if any(field["path"] in allowed_inputs for field in group["fields"])
        ]
        definition["display_decimals"] = {
            key: value
            for key, value in definition["display_decimals"].items()
            if key.startswith("output.") and key == f"output.{target}"
            or not key.startswith("output.")
            and key in allowed_inputs
        }
        definition["fixed_context"] = [
            {
                "path": "context.fixture",
                "order": 0,
                "label": "用途",
                "value": "Prediction Graph split-package synthetic demonstration",
            }
        ]
        definition["response_curve_variables"] = [
            item
            for item in definition["response_curve_variables"]
            if item.get("path") in allowed_inputs
        ]
        fixture["canonical_candidate"]["task_id"] = task_id
        for group_key in ("composition", "process", "categorical"):
            fixture["canonical_candidate"][group_key] = {
                key: value
                for key, value in fixture["canonical_candidate"][group_key].items()
                if f"{group_key}.{key}" in allowed_inputs
            }
        capability = fixture["runtime_capability"]
        capability["task_id"] = task_id
        capability["targets"] = [
            item for item in capability["targets"] if item["target"] == target
        ]
        capability["operations"]["response_curve"] = False
        _write_json(TASK_ROOT / f"{task_id}.json", fixture)

        projected_profile = deepcopy(profile)
        projected_profile["id"] = f"{task_id}-observations"
        projected_profile["task_id"] = task_id
        projected_profile["families"] = [
            {
                **family,
                "outputs": [
                    output
                    for output in family["outputs"]
                    if output["key"] == target
                ],
            }
            for family in projected_profile["families"]
            if family["id"] == family_id
        ]
        _write_json(
            PROFILE_ROOT / f"observation-profile-{task_id}.json",
            projected_profile,
        )


def _workability_assets() -> None:
    task_id = "welding-graph-deposition-efficiency-v1"
    fixture = {
        "task_definition": {
            "schema_version": "task-definition/v1",
            "id": task_id,
            "label": "Graph比較用: 溶着効率proxy",
            "canonical_candidate_schema_version": "canonical-candidate/v1",
            "input_groups": [
                {
                    "key": "process",
                    "order": 0,
                    "label": "合成proxy入力",
                    "fields": [
                        {
                            "path": "process.heat_input_kj_per_mm",
                            "kind": "number",
                            "order": 0,
                            "label": "入熱",
                            "unit": "kJ/mm",
                            "required": True,
                            "editable": True,
                            "default_range": {"min": 0.8, "max": 2.0},
                            "allowed_range": {"min": 0.1, "max": 10},
                            "training_range": {"min": 0.8, "max": 2.0},
                        },
                        {
                            "path": "process.voltage_v",
                            "kind": "number",
                            "order": 1,
                            "label": "電圧",
                            "unit": "V",
                            "required": True,
                            "editable": True,
                            "default_range": {"min": 22, "max": 34},
                            "allowed_range": {"min": 1, "max": 100},
                            "training_range": {"min": 22, "max": 34},
                        },
                        {
                            "path": "process.gas_flow_l_per_min",
                            "kind": "number",
                            "order": 2,
                            "label": "ガス流量",
                            "unit": "L/min",
                            "required": True,
                            "editable": True,
                            "default_range": {"min": 12, "max": 24},
                            "allowed_range": {"min": 0.1, "max": 100},
                            "training_range": {"min": 12, "max": 24},
                        },
                        {
                            "path": "process.wire_feed_speed_m_per_min",
                            "kind": "number",
                            "order": 3,
                            "label": "ワイヤ送給速度",
                            "unit": "m/min",
                            "required": True,
                            "editable": True,
                            "default_range": {"min": 5, "max": 11},
                            "allowed_range": {"min": 0.1, "max": 30},
                            "training_range": {"min": 5, "max": 11},
                        },
                    ],
                }
            ],
            "outputs": [
                {
                    "key": "deposition_efficiency_pct",
                    "label": "溶着効率proxy",
                    "unit": "%",
                    "goal_direction": "at_least",
                    "measurement_keys": ["deposition_efficiency_pct"],
                    "plausibility_range": {"min": 0, "max": 100},
                    "preferred_display_range": {"min": 55, "max": 88},
                }
            ],
            "display_decimals": {
                "process.heat_input_kj_per_mm": 2,
                "process.voltage_v": 1,
                "process.gas_flow_l_per_min": 1,
                "process.wire_feed_speed_m_per_min": 1,
                "output.deposition_efficiency_pct": 1,
            },
            "fixed_context": [
                {
                    "path": "context.evidence",
                    "order": 0,
                    "label": "証拠境界",
                    "value": "決定論的な合成demonstration。実測・因果・production品質を主張しない",
                }
            ],
            "constraints": [],
            "response_curve_variables": [],
        },
        "canonical_candidate": {
            "schema_version": "canonical-candidate/v1",
            "task_id": task_id,
            "composition": {},
            "process": {
                "heat_input_kj_per_mm": 1.35,
                "voltage_v": 28.0,
                "gas_flow_l_per_min": 18.0,
                "wire_feed_speed_m_per_min": 8.0,
            },
            "heat_pattern": None,
            "categorical": {},
            "provenance": {"source_kind": "direct", "source_ref": None},
        },
        "runtime_capability": {
            "schema_version": "runtime-capability/v1",
            "task_id": task_id,
            "model_package_schema_version": "model-package/v1",
            "targets": [
                {
                    "target": "deposition_efficiency_pct",
                    "point_statistics": ["mean"],
                    "standard_deviation": False,
                    "quantiles": True,
                    "samples": False,
                    "parametric_distribution": False,
                    "uncertainty_components": False,
                    "support": True,
                    "warnings": True,
                    "goal_probability": "unavailable",
                }
            ],
            "joint_samples": False,
            "operations": {
                "preview": True,
                "detailed_prediction": True,
                "response_curve": True,
                "similarity": True,
                "snapshot": True,
                "actual_measurement": False,
            },
        },
    }
    _write_json(TASK_ROOT / f"{task_id}.json", fixture)
    _write_json(
        PROFILE_ROOT / "tabular-profile-welding-graph-deposition-efficiency-v1.json",
        {
            "schema_version": "tabular-dataset-profile/v1",
            "profile_id": "welding-graph-deposition-efficiency-synthetic-v1",
            "name": "Prediction Graph用の決定論的な溶着効率proxy",
            "task_id": task_id,
            "package_id": "welding-graph-deposition-efficiency-ridge-v2",
            "id_column": "row_id",
            "group_column": None,
            "inputs": [
                {
                    "path": "process.heat_input_kj_per_mm",
                    "column": "heat_input_kj_per_mm",
                    "kind": "number",
                    "unit": "kJ/mm",
                },
                {
                    "path": "process.voltage_v",
                    "column": "voltage_v",
                    "kind": "number",
                    "unit": "V",
                },
                {
                    "path": "process.gas_flow_l_per_min",
                    "column": "gas_flow_l_per_min",
                    "kind": "number",
                    "unit": "L/min",
                },
                {
                    "path": "process.wire_feed_speed_m_per_min",
                    "column": "wire_feed_speed_m_per_min",
                    "kind": "number",
                    "unit": "m/min",
                },
            ],
            "outputs": [
                {
                    "key": "deposition_efficiency_pct",
                    "column": "deposition_efficiency_pct",
                    "unit": "%",
                    "lower_bound": 0,
                    "upper_bound": 100,
                }
            ],
        },
    )
    FIXTURE_ROOT.mkdir(parents=True, exist_ok=True)
    with (FIXTURE_ROOT / "welding_deposition_efficiency_synthetic.csv").open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "row_id",
                "heat_input_kj_per_mm",
                "voltage_v",
                "gas_flow_l_per_min",
                "wire_feed_speed_m_per_min",
                "deposition_efficiency_pct",
            ]
        )
        row_id = 0
        for heat_input in (0.8, 1.1, 1.35, 1.6, 2.0):
            for voltage in (22.0, 26.0, 28.0, 30.0, 34.0):
                for gas_flow in (12.0, 16.0, 18.0, 20.0, 24.0):
                    for wire_feed in (5.0, 7.0, 8.0, 9.0, 11.0):
                        value = max(
                            55.0,
                            min(
                                88.0,
                                82.0
                                - 7.0 * abs(heat_input - 1.35)
                                - 0.45 * abs(voltage - 28.0)
                                - 0.30 * abs(gas_flow - 18.0)
                                - 0.55 * abs(wire_feed - 8.0),
                            ),
                        )
                        writer.writerow(
                            [
                                row_id,
                                heat_input,
                                voltage,
                                gas_flow,
                                wire_feed,
                                round(value, 4),
                            ]
                        )
                        row_id += 1


if __name__ == "__main__":
    _property_assets()
    _workability_assets()
