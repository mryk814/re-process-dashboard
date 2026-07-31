from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from material_workbench.contracts.candidate_project_contracts import CandidateInput
from material_workbench.contracts.task_contracts import TaskDefinition
from material_workbench.data.profiles.schema import DatasetInputProfile
from material_workbench.task_composition.builtin.shared import (
    TABULAR_EXPLORER,
    _application_capability,
    _standard_response_curve,
)
from material_workbench.task_composition.builtin.sources import DATA_ROOT
from material_workbench.task_composition.descriptors import StarterProject, TaskModule
from material_workbench.task_composition.ports import (
    DataDescriptor,
    DataLoader,
    FeatureRowBuilder,
    PredictionRuntime,
    SpecializedPackageBuilder,
)

if TYPE_CHECKING:
    from material_workbench.modeling.model_packages import VerifiedModelPackage


WELDING_STAGE_C_TASK_ID = "welding-stage-c-properties-v1"
WELDING_STAGE_B_TASK_ID = "welding-consumable-stage-b-v1"
_WELDING_STAGE_B_PROFILE = DATA_ROOT / "welding-stage-b-profile-v1.json"
_OBSERVATION_DECLARATIONS: dict[str, dict[str, Any]] = {
    WELDING_STAGE_C_TASK_ID: {
        "task_id": WELDING_STAGE_C_TASK_ID,
        "profile_path": DATA_ROOT
        / "observation-profile-welding-consumable-stage-c-v1.json",
        "feature_transform_id": "welding-stage-c-observation-transform",
        "feature_transform_version": "1.0.0",
        "support_policy_id": "stage-c-family-group-knn-v1",
        "output_bounds": {
            "TS": (0.0, None),
            "YS": (0.0, None),
            "EL": (0.0, 100.0),
            "RA": (0.0, 100.0),
            "CHARPY_ENERGY": (0.0, None),
            "BRITTLE_FRACTURE": (0.0, 100.0),
            "CORROSION_RATE": (0.0, None),
        },
    },
}


def observation_declaration(task_id: str) -> Any:
    from material_workbench.modeling.observation_training_spec import (
        ObservationRuntimeDeclaration,
    )

    try:
        fields = _OBSERVATION_DECLARATIONS[task_id]
    except KeyError as exc:
        raise ValueError(f"Observation family Taskではありません: {task_id}") from exc
    return ObservationRuntimeDeclaration(**fields)


def _observation_loader(task_id: str) -> DataLoader:
    def load(path: Path, profile: DatasetInputProfile | None = None) -> DataDescriptor:
        from material_workbench.data.observation_profile import (
            ObservationDatasetProfile,
        )
        from material_workbench.modeling.observation_regression import (
            load_observation_data,
        )

        if profile is not None and not isinstance(profile, ObservationDatasetProfile):
            raise ValueError("observation task requires an observation Dataset Profile")
        selected = profile
        return load_observation_data(path, observation_declaration(task_id), selected)

    return load


def _load_welding_stage_b(
    path: Path, profile: DatasetInputProfile | None = None
) -> DataDescriptor:
    from material_workbench.data.stage_b_training import (
        StageBWorkbookProfile,
        build_stage_b_training_data,
        load_stage_b_profile,
    )

    if profile is not None and not isinstance(profile, StageBWorkbookProfile):
        raise ValueError("Stage B task requires a Stage B Dataset Profile")
    selected = profile or load_stage_b_profile(_WELDING_STAGE_B_PROFILE)
    return build_stage_b_training_data(
        path,
        selected,
        profile_locator=(
            f"catalog:{selected.id}"
            if isinstance(profile, StageBWorkbookProfile)
            else _WELDING_STAGE_B_PROFILE
        ),
    ).data


def _welding_stage_b_runtime(
    data: DataDescriptor,
    package: VerifiedModelPackage,
) -> PredictionRuntime:
    from material_workbench.modeling.tabular.runtime import TabularRegressionRuntime

    return TabularRegressionRuntime(data, package)


def _observation_runtime(
    data: DataDescriptor,
    package: VerifiedModelPackage,
) -> PredictionRuntime:
    from material_workbench.modeling.observation_regression import (
        ObservationRegressionRuntime,
    )

    return ObservationRegressionRuntime(data, package)


def _observation_features(task_id: str) -> FeatureRowBuilder:
    def build(row: dict[str, Any], medians: dict[str, float]) -> Any:
        from material_workbench.modeling.observation_regression import (
            build_observation_features_from_observation,
            resolve_spec,
        )

        return build_observation_features_from_observation(
            row, medians, resolve_spec(observation_declaration(task_id))
        )

    return build


def _welding_stage_b_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from material_workbench.data.stage_b_training import (
        load_stage_b_profile,
        stage_b_runtime_profile,
    )
    from material_workbench.modeling.tabular.features import (
        build_tabular_features_from_observation,
    )

    profile = stage_b_runtime_profile(load_stage_b_profile(_WELDING_STAGE_B_PROFILE))
    return build_tabular_features_from_observation(row, medians, profile)


def _observation_builder(task_id: str) -> SpecializedPackageBuilder:
    def build(
        source: Path,
        output: Path,
        *,
        replace: bool,
        package_id: str,
        package_version: str,
        profile_path: Path | None = None,
    ) -> None:
        from material_workbench.data.profile_family_registry import load_profile_document
        from material_workbench.modeling.observation_model_builder import (
            build as build_package,
        )

        build_package(
            source,
            output,
            declaration=observation_declaration(task_id),
            replace=replace,
            package_id=package_id,
            package_version=package_version,
            profile=(
                load_profile_document(profile_path)
                if profile_path is not None
                else None
            ),
        )

    return build


def _build_welding_stage_b(
    source: Path,
    output: Path,
    *,
    replace: bool,
    package_id: str,
    package_version: str,
    profile_path: Path | None = None,
) -> None:
    from build_welding_stage_b_assets import build_package

    build_package(
        source,
        profile_path or _WELDING_STAGE_B_PROFILE,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
    )


def _welding_stage_c_starter(
    runtime: PredictionRuntime,
    _task_definition: TaskDefinition,
) -> list[CandidateInput]:
    from material_workbench.modeling.observation_regression import (
        resolve_spec,
        stage_c_starter_candidates,
    )

    return stage_c_starter_candidates(
        runtime.data.medians,
        resolve_spec(observation_declaration(WELDING_STAGE_C_TASK_ID)),
    )


def _welding_stage_b_starter(
    runtime: PredictionRuntime,
    _task_definition: TaskDefinition,
) -> list[CandidateInput]:
    from material_workbench.modeling.tabular.features import candidate_from_observation

    data = runtime.data
    rows = [row for row in data.observations if row["eligible"]]
    selected = [rows[len(rows) // 4], rows[len(rows) // 2], rows[len(rows) * 3 // 4]]
    return [
        candidate_from_observation(row, data.profile).model_copy(update={"name": label})
        for row, label in zip(
            selected, ("低位施工", "代表施工", "高位施工"), strict=True
        )
    ]


WELDING_STAGE_B_TASK_MODULE = TaskModule(
    task_id=WELDING_STAGE_B_TASK_ID,
    package_override_env="MATERIAL_WORKBENCH_WELDING_STAGE_B_MODEL_PACKAGE",
    source_env="WORKBENCH_WELDING_STAGE_B_SOURCE_PATH",
    source_kind="welding_multistage",
    default_source=Path(
        "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
    ),
    data_loader=_load_welding_stage_b,
    runtime_factory=_welding_stage_b_runtime,
    feature_row_builder=_welding_stage_b_features,
    specialized_package_builder=_build_welding_stage_b,
    application=_application_capability(
        actual_measurement=True,
        response_curve=False,
        similarity=True,
        prediction_space_targets=("C", "Mn", "Si", "Ni", "Cr", "Mo"),
        input_space_target="C",
        sparse_blend_transform_id="welding-stage-a-v1",
    ),
    starter_project=StarterProject(
        "welding-stage-b-default",
        "溶接材料 Stage B",
        _welding_stage_b_starter,
        seed_on_upgrade=True,
        distribution="gallery",
    ),
)

WELDING_STAGE_C_TASK_MODULE = TaskModule(
    task_id=WELDING_STAGE_C_TASK_ID,
    package_override_env="MATERIAL_WORKBENCH_WELDING_STAGE_C_MODEL_PACKAGE",
    source_env="WORKBENCH_WELDING_STAGE_C_SOURCE_PATH",
    source_kind="welding_stage_c",
    default_source=Path(
        "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
    ),
    data_loader=_observation_loader(WELDING_STAGE_C_TASK_ID),
    runtime_factory=_observation_runtime,
    feature_row_builder=_observation_features(WELDING_STAGE_C_TASK_ID),
    specialized_package_builder=_observation_builder(WELDING_STAGE_C_TASK_ID),
    application=_application_capability(
        actual_measurement=False,
        response_curve=True,
        similarity=True,
        prediction_space_targets=(
            "TS",
            "YS",
            "EL",
            "RA",
            "CHARPY_ENERGY",
            "CORROSION_RATE",
        ),
        prediction_space_evidence_context="parent_condition",
        input_space_target="TS",
        input_space_evidence_context="parent_condition",
    ),
    starter_project=StarterProject(
        "welding-stage-c-default",
        "溶着金属成分から特性を予測",
        _welding_stage_c_starter,
        distribution="legacy_hidden",
    ),
    response_curve=_standard_response_curve,
    data_explorer=TABULAR_EXPLORER,
)
