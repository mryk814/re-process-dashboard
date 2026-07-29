from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from material_workbench.data.stage_b_training import (
    build_stage_b_training_data,
    load_stage_b_profile,
)
from material_workbench.modeling.model_lifecycle import staged_package_destination
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.modeling.tabular_model_builder import (
    build_tabular_package_from_data,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
DEFAULT_PROFILE = (
    ROOT / "backend/src/material_workbench/data/welding-stage-b-profile-v1.json"
)
DEFAULT_TASK = (
    ROOT
    / "backend/src/material_workbench/tasks/task_definitions"
    / "welding-consumable-stage-b-v1.json"
)
DEFAULT_PACKAGE = ROOT / "models/packages/welding-consumable-stage-b-ridge-v1"


def _range(values: list[float], *, allowed_floor: float | None = None) -> dict[str, dict[str, float]]:
    array = np.asarray(values, dtype=float)
    low, high = float(array.min()), float(array.max())
    span = max(high - low, abs(high) * 0.05, 1e-6)
    training_low = low if low < high else low - span
    training_high = high if low < high else high + span
    if allowed_floor is not None:
        training_low = max(allowed_floor, training_low)
    allowed_low = low - span * 0.1
    if allowed_floor is not None:
        allowed_low = max(allowed_floor, allowed_low)
    return {
        "default_range": {
            "min": (
                float(np.quantile(array, 0.05))
                if low < high
                else training_low
            ),
            "max": (
                float(np.quantile(array, 0.95))
                if low < high
                else training_high
            ),
        },
        "allowed_range": {
            "min": min(allowed_low, training_low),
            "max": max(high + span * 0.1, training_high),
        },
        "training_range": {"min": training_low, "max": training_high},
    }


def build_task_definition(source: Path, profile_path: Path, destination: Path) -> None:
    source_profile = load_stage_b_profile(profile_path)
    training = build_stage_b_training_data(source, source_profile)
    profile = training.data.profile
    rows = [row for row in training.data.observations if row["eligible"]]
    groups: dict[str, list[dict[str, object]]] = {
        "composition": [],
        "process": [],
        "categorical": [],
    }
    display_decimals: dict[str, int] = {}
    fixture_composition: dict[str, float] = {}
    fixture_process: dict[str, float] = {}
    fixture_categorical: dict[str, str] = {}
    for field in profile.inputs:
        group, key = field.path.split(".", 1)
        if field.kind == "number":
            values = [
                float(row["composition"][key] if group == "composition" else row["features"][key])
                for row in rows
            ]
            ranges = _range(values, allowed_floor=0)
            groups[group].append({
                "path": field.path,
                "kind": "number",
                "order": len(groups[group]),
                "label": key,
                "unit": field.unit,
                "required": True,
                "editable": True,
                **ranges,
            })
            display_decimals[field.path] = 5 if group == "composition" else 3
            value = float(np.median(values))
            (fixture_composition if group == "composition" else fixture_process)[key] = value
        else:
            groups[group].append({
                "path": field.path,
                "kind": "categorical",
                "order": len(groups[group]),
                "label": key,
                "unit": None,
                "required": True,
                "editable": True,
                "choices": list(field.choices),
            })
            fixture_categorical[key] = field.choices[0]

    output_definitions = []
    for output in profile.outputs:
        values = [
            float(row["outputs"][output.key])
            for row in rows
            if output.key in row["outputs"]
        ]
        output_range = _range(values, allowed_floor=0)
        output_definitions.append({
            "key": output.key,
            "label": f"溶着金属 {output.key}",
            "unit": output.unit,
            "goal_direction": "target",
            "measurement_keys": [output.key],
            "plausibility_range": output_range["allowed_range"],
            "preferred_display_range": output_range["training_range"],
        })
        display_decimals[f"output.{output.key}"] = 5

    task = {
        "task_definition": {
            "schema_version": "task-definition/v1",
            "id": source_profile.task_id,
            "label": "溶接材料 Stage B：溶着金属成分",
            "canonical_candidate_schema_version": "canonical-candidate/v1",
            "input_groups": [
                {
                    "key": key,
                    "order": order,
                    "label": label,
                    "fields": groups[key],
                }
                for order, (key, label) in enumerate((
                    ("composition", "材料成分（whole wire）"),
                    ("process", "溶接条件・原料補助特徴"),
                    ("categorical", "溶接context"),
                ))
            ],
            "outputs": output_definitions,
            "display_decimals": display_decimals,
            "fixed_context": [
                {
                    "path": "context.training_grain",
                    "order": 0,
                    "label": "学習単位",
                    "value": "溶着金属成分300観測。relation行は学習行にしません",
                },
                {
                    "path": "context.split",
                    "order": 1,
                    "label": "評価分割",
                    "value": (
                        f"溶接施工_key** grouped {source_profile.folds}-fold"
                    ),
                },
            ],
            "constraints": [],
            "composition_totals": [],
            "response_curve_variables": [],
            "curve_axis_path": None,
        },
        "canonical_candidate": {
            "schema_version": "canonical-candidate/v1",
            "task_id": source_profile.task_id,
            "composition": fixture_composition,
            "process": fixture_process,
            "categorical": fixture_categorical,
            "heat_pattern": None,
            "provenance": {"source_kind": "direct", "source_ref": None},
        },
        "runtime_capability": {
            "schema_version": "runtime-capability/v1",
            "task_id": source_profile.task_id,
            "model_package_schema_version": "model-package/v1",
            "targets": [
                {
                    "target": output.key,
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
                for output in profile.outputs
            ],
            "joint_samples": False,
            "operations": {
                "preview": True,
                "detailed_prediction": True,
                "response_curve": False,
                "similarity": True,
                "snapshot": True,
                "actual_measurement": True,
            },
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def build_package(
    source: Path,
    profile_path: Path,
    destination: Path,
    *,
    replace: bool = False,
    package_id: str | None = None,
    package_version: str = "1.0.0",
) -> None:
    source_profile = load_stage_b_profile(profile_path)
    training = build_stage_b_training_data(source, source_profile)
    contract = {
        "profile_digest": training.profile_digest,
        "transform_digest": training.transform_digest,
        "cohort_digests": training.cohort_digests,
        "fold_digests": training.fold_digests,
        "fold_assignments": training.fold_assignments,
        "folds": training.folds,
        "missing_by_target": training.missing_by_target,
    }
    with staged_package_destination(destination, replace=replace) as staging:
        build_tabular_package_from_data(
            training.data,
            profile_path,
            staging,
            # Package IDはディレクトリ名に合わせる。Profileの既定値のままだと、
            # 契約が変わって新しい版を作っても同じIDのPackageが2つ残る。
            package_id=package_id or destination.name,
            package_version=package_version,
            training_contract=contract,
        )
        verify_model_package(
            staging,
            task_id=source_profile.task_id,
            source=source,
            profile=source_profile,
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--task-output", type=Path, default=DEFAULT_TASK)
    parser.add_argument("--package-output", type=Path, default=DEFAULT_PACKAGE)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build_task_definition(args.source, args.profile, args.task_output)
    build_package(
        args.source,
        args.profile,
        args.package_output,
        replace=args.replace,
    )


if __name__ == "__main__":
    main()
