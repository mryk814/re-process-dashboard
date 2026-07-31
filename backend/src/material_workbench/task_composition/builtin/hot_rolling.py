from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from material_workbench.contracts.schemas import CandidateInput
from material_workbench.contracts.task_contracts import TaskDefinition
from material_workbench.data.profiles.schema import DatasetInputProfile
from material_workbench.task_composition.builtin.shared import (
    EXPLORER,
    _application_capability,
    _declared_composition_medians,
    _standard_response_curve,
)
from material_workbench.task_composition.builtin.sources import PRIMARY_DEFAULT_SOURCE
from material_workbench.task_composition.descriptors import (
    StandardModelAuthoring,
    StarterProject,
    TaskModule,
)
from material_workbench.task_composition.ports import DataDescriptor, PredictionRuntime

if TYPE_CHECKING:
    from material_workbench.modeling.model_packages import VerifiedModelPackage


HOT_ROLLING_TASK_ID = "hot-rolled-properties-v1"


def _hot_rolling_training_candidate(
    row: dict[str, Any],
    _data: DataDescriptor,
) -> CandidateInput | None:
    from material_workbench.modeling.hot_rolling_feature_pipeline import (
        candidate_from_observation,
    )

    return candidate_from_observation(row)


def _hot_rolling_starter_candidates(
    runtime: PredictionRuntime,
    task_definition: TaskDefinition,
) -> list[CandidateInput]:
    composition = _declared_composition_medians(runtime.data.medians, task_definition)
    variants = (
        ("基準熱延", 1170, 900, 34, 3.4, 1160, 30),
        ("高温保持", 1200, 910, 36, 3.2, 1180, 42),
        ("延性重視", 1160, 920, 34, 3.8, 1140, 34),
    )
    keys = (
        "soaking_temperature_c",
        "finish_temperature_c",
        "entry_thickness_mm",
        "exit_thickness_mm",
        "hold_temperature_c",
        "hold_time_min",
    )
    return [
        CandidateInput(
            name=name,
            inputs={
                "composition": composition,
                "process": dict(zip(keys, values)),
                "categorical": {},
                "heat_pattern": None,
            },
        )
        for name, *values in variants
    ]


def _load_workbook(
    path: Path, profile: DatasetInputProfile | None = None
) -> DataDescriptor:
    from material_workbench.data.importer import load_workbook_data

    if profile is not None and not isinstance(profile, DatasetInputProfile):
        raise ValueError("workbook task requires a workbook Dataset Profile")
    return load_workbook_data(path, profile=profile)


def _hot_rolling_runtime(
    data: DataDescriptor, package: VerifiedModelPackage
) -> PredictionRuntime:
    from material_workbench.modeling.hot_rolling import HotRollingRuntime

    return HotRollingRuntime(data, package_root=package)


def _hot_rolling_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from material_workbench.modeling.hot_rolling_feature_pipeline import (
        build_hot_rolling_features_from_observation,
    )

    return build_hot_rolling_features_from_observation(row, medians)


def _build_hot_rolling(
    source: Path,
    output: Path,
    *,
    replace: bool,
    package_id: str,
    package_version: str,
    profile_path: Path | None = None,
) -> None:
    from build_hot_rolling_model_package import build

    from material_workbench.data.profile_family_registry import load_profile_document

    build(
        source,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
        profile=load_profile_document(profile_path) if profile_path else None,
    )


HOT_ROLLING_TASK_MODULE = TaskModule(
    task_id=HOT_ROLLING_TASK_ID,
    package_override_env="MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE",
    source_env="WORKBENCH_SOURCE_PATH",
    source_kind="primary",
    default_source=PRIMARY_DEFAULT_SOURCE,
    data_loader=_load_workbook,
    runtime_factory=_hot_rolling_runtime,
    feature_row_builder=_hot_rolling_features,
    specialized_package_builder=_build_hot_rolling,
    standard_model_authoring=StandardModelAuthoring(
        _hot_rolling_training_candidate,
        ("exact-gp-rbf.v1",),
    ),
    application=_application_capability(
        actual_measurement=True,
        response_curve=True,
        similarity=True,
        contour_axes=(
            "process.finish_temperature_c",
            "process.exit_thickness_mm",
            "process.hold_temperature_c",
            "process.hold_time_min",
        ),
        input_space_target="TS",
    ),
    data_explorer=EXPLORER,
    starter_project=StarterProject(
        "hot-rolling-default",
        "熱延条件の候補検討",
        _hot_rolling_starter_candidates,
        distribution="legacy_hidden",
    ),
    response_curve=_standard_response_curve,
)
