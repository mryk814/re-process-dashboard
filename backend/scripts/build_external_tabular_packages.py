"""Rebuild the bundled external CSV task packages."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.modeling.tabular_model_builder import build


ROOT = Path(__file__).resolve().parents[2]
JOBS = {
    "heat-treatment-tradeoff-v1": (
        ROOT / "data/source/external/heat_treatment_tradeoff_samples.csv",
        ROOT / "backend/src/material_workbench/data/tabular-profile-heat-treatment-v1.json",
        ROOT / "models/packages/heat-treatment-ridge-external-v1",
    ),
    "concrete-strength-v1": (
        ROOT / "data/source/external/concrete_mix_samples.csv",
        ROOT / "backend/src/material_workbench/data/tabular-profile-concrete-v1.json",
        ROOT / "models/packages/concrete-strength-ridge-external-v1",
    ),
    "wear-curve-v1": (
        ROOT / "data/source/external/wear_curve_samples.csv",
        ROOT / "backend/src/material_workbench/data/tabular-profile-wear-curve-v1.json",
        ROOT / "models/packages/wear-curve-ridge-external-v1",
    ),
    "battery-degradation-v1": (
        ROOT / "data/source/external/battery_calce_cs2_cycles.csv",
        ROOT / "backend/src/material_workbench/data/tabular-profile-battery-degradation-v1.json",
        ROOT / "models/packages/battery-degradation-lightgbm-calce-v1",
    ),
    "secom-yield-risk-v1": (
        ROOT / "data/source/external/secom_stress.csv",
        ROOT / "backend/src/material_workbench/data/tabular-profile-secom-yield-v1.json",
        ROOT / "models/packages/secom-yield-lightgbm-calibrated-v1",
    ),
    "mpea-literature-tys-v1": (
        ROOT / "data/source/external/mpea_ground_truth_18021833.csv",
        ROOT / "backend/src/material_workbench/data/tabular-profile-mpea-literature-tys-v1.json",
        ROOT / "models/packages/mpea-literature-tys-ridge-v1",
    ),
    "mpea-room-tensile-v1": (
        ROOT / "data/source/external/mpea_ground_truth_18021833.csv",
        ROOT / "backend/src/material_workbench/data/tabular-profile-mpea-room-tensile-v1.json",
        ROOT / "models/packages/mpea-room-tensile-ridge-v2",
    ),
    "mpea-hardness-process-v1": (
        ROOT / "data/source/external/mpea_ground_truth_18021833.csv",
        ROOT / "backend/src/material_workbench/data/tabular-profile-mpea-hardness-v1.json",
        ROOT / "models/packages/mpea-hardness-ridge-v2",
    ),
}

PACKAGE_VERSIONS = {
    "mpea-room-tensile-v1": ("mpea-room-tensile-ridge-v2", "2.0.0"),
    "mpea-hardness-process-v1": ("mpea-hardness-ridge-v2", "2.0.0"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("task", nargs="*", choices=tuple(JOBS))
    args = parser.parse_args()
    selected = args.task or list(JOBS)
    for task_id in selected:
        source, profile, destination = JOBS[task_id]
        package_id, package_version = PACKAGE_VERSIONS.get(
            task_id,
            (None, "1.0.0"),
        )
        print(f"building {task_id} from {source.name}")
        build(
            source,
            profile,
            destination,
            replace=True,
            package_id=package_id,
            package_version=package_version,
        )
        print(f"verified {destination}")


if __name__ == "__main__":
    main()
