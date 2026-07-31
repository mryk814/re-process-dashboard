from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from material_workbench.data.profiles.schema import DatasetInputProfile
from material_workbench.task_composition.builtin.shared import (
    _application_capability,
    _curve_family,
    _standard_response_curve,
)
from material_workbench.task_composition.descriptors import TaskModule
from material_workbench.task_composition.ports import DataDescriptor, PredictionRuntime

if TYPE_CHECKING:
    from material_workbench.modeling.model_package_verification import VerifiedModelPackage


FLANK_WEAR_TASK_ID = "flank-wear-v1"


def _load_flank_wear(
    path: Path, profile: DatasetInputProfile | None = None
) -> DataDescriptor:
    from material_workbench.modeling.flank_wear import load_flank_wear_data

    if profile is not None and not isinstance(profile, DatasetInputProfile):
        raise ValueError("flank-wear task requires a workbook Dataset Profile")
    return load_flank_wear_data(path, profile=profile)


def _flank_wear_runtime(
    data: DataDescriptor, package: VerifiedModelPackage
) -> PredictionRuntime:
    from material_workbench.modeling.flank_wear import FlankWearRuntime

    return FlankWearRuntime(data, package_root=package)


def _flank_wear_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from material_workbench.modeling.flank_wear_feature_pipeline import (
        build_flank_wear_features_from_observation,
    )

    return build_flank_wear_features_from_observation(row, medians)


def _build_flank_wear(
    source: Path,
    output: Path,
    *,
    replace: bool,
    package_id: str,
    package_version: str,
    profile_path: Path | None = None,
) -> None:
    from build_flank_wear_model_package import build

    from material_workbench.data.profile_family_registry import load_profile_document

    build(
        source,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
        profile=load_profile_document(profile_path) if profile_path else None,
    )


FLANK_WEAR_TASK_MODULE = TaskModule(
    task_id=FLANK_WEAR_TASK_ID,
    package_override_env="MATERIAL_WORKBENCH_FLANK_WEAR_MODEL_PACKAGE",
    source_env="WORKBENCH_FLANK_WEAR_SOURCE_PATH",
    source_kind="flank_wear",
    default_source=Path("data/source/cutting_tool_flank_wear_synthetic_dataset.xlsx"),
    data_loader=_load_flank_wear,
    runtime_factory=_flank_wear_runtime,
    feature_row_builder=_flank_wear_features,
    specialized_package_builder=_build_flank_wear,
    application=_application_capability(
        actual_measurement=True,
        response_curve=True,
        similarity=True,
        prediction_space_targets=("VB_mean", "VB_max"),
        prediction_space_evidence_context="parent_condition",
        input_space_target="VB_mean",
        input_space_evidence_context="parent_condition",
        curve_family=True,
    ),
    response_curve=_standard_response_curve,
    curve_family=_curve_family,
)
