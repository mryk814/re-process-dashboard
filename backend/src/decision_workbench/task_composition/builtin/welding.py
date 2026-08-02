from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.task_contracts import TaskDefinition
from decision_workbench.data.profiles.schema import DatasetInputProfile
from decision_workbench.task_composition.builtin.shared import (
    TABULAR_EXPLORER,
    _application_capability,
    _standard_response_curve,
)
from decision_workbench.task_composition.builtin.sources import DATA_ROOT
from decision_workbench.task_composition.descriptors import (
    SampleGalleryMetadata,
    StarterProject,
    TaskModule,
)
from decision_workbench.task_composition.ports import (
    DataDescriptor,
    DataLoader,
    FeatureRowBuilder,
    PredictionRuntime,
    SpecializedPackageBuilder,
)
from decision_workbench.task_composition.training_inspector import (
    CANONICAL_TRAINING_INSPECTOR,
)

if TYPE_CHECKING:
    from decision_workbench.modeling.packages.verification import VerifiedModelPackage


WELDING_STAGE_C_TASK_ID = "welding-stage-c-properties-v1"
WELDING_STAGE_B_TASK_ID = "welding-consumable-stage-b-v1"
WELDING_GRAPH_TENSILE_TASK_ID = "welding-graph-tensile-ts-v1"
WELDING_GRAPH_TOUGHNESS_TASK_ID = "welding-graph-toughness-v1"
WELDING_GRAPH_CORROSION_TASK_ID = "welding-graph-corrosion-v1"
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
    WELDING_GRAPH_TENSILE_TASK_ID: {
        "task_id": WELDING_GRAPH_TENSILE_TASK_ID,
        "profile_path": DATA_ROOT
        / "observation-profile-welding-graph-tensile-ts-v1.json",
        "feature_transform_id": "welding-graph-tensile-ts-transform",
        "feature_transform_version": "1.0.0",
        "support_policy_id": "welding-graph-tensile-group-knn-v1",
        "output_bounds": {"TS": (0.0, None)},
    },
    WELDING_GRAPH_TOUGHNESS_TASK_ID: {
        "task_id": WELDING_GRAPH_TOUGHNESS_TASK_ID,
        "profile_path": DATA_ROOT
        / "observation-profile-welding-graph-toughness-v1.json",
        "feature_transform_id": "welding-graph-toughness-transform",
        "feature_transform_version": "1.0.0",
        "support_policy_id": "welding-graph-toughness-group-knn-v1",
        "output_bounds": {"CHARPY_ENERGY": (0.0, None)},
    },
    WELDING_GRAPH_CORROSION_TASK_ID: {
        "task_id": WELDING_GRAPH_CORROSION_TASK_ID,
        "profile_path": DATA_ROOT
        / "observation-profile-welding-graph-corrosion-v1.json",
        "feature_transform_id": "welding-graph-corrosion-transform",
        "feature_transform_version": "1.0.0",
        "support_policy_id": "welding-graph-corrosion-group-knn-v1",
        "output_bounds": {"CORROSION_RATE": (0.0, None)},
    },
}


def observation_declaration(task_id: str) -> Any:
    from decision_workbench.modeling.observation_training_spec import (
        ObservationRuntimeDeclaration,
    )

    try:
        fields = _OBSERVATION_DECLARATIONS[task_id]
    except KeyError as exc:
        raise ValueError(f"Observation family Taskではありません: {task_id}") from exc
    return ObservationRuntimeDeclaration(**fields)


def _observation_loader(task_id: str) -> DataLoader:
    def load(path: Path, profile: DatasetInputProfile | None = None) -> DataDescriptor:
        from decision_workbench.data.observation_profile import (
            ObservationDatasetProfile,
        )
        from decision_workbench.modeling.observation_regression import (
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
    from decision_workbench.data.stage_b_training import (
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
    from decision_workbench.modeling.tabular.runtime import TabularRegressionRuntime

    return TabularRegressionRuntime(
        data,
        package,
        missing_policy_inputs=data.profile.inputs,
    )


def _observation_runtime(
    data: DataDescriptor,
    package: VerifiedModelPackage,
) -> PredictionRuntime:
    from decision_workbench.modeling.observation_regression import (
        ObservationRegressionRuntime,
    )

    return ObservationRegressionRuntime(data, package)


def _observation_features(task_id: str) -> FeatureRowBuilder:
    def build(row: dict[str, Any], medians: dict[str, float]) -> Any:
        from decision_workbench.modeling.observation_regression import (
            build_observation_features_from_observation,
            resolve_spec,
        )

        return build_observation_features_from_observation(
            row, medians, resolve_spec(observation_declaration(task_id))
        )

    return build


def _welding_stage_b_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from decision_workbench.data.stage_b_training import (
        load_stage_b_profile,
        stage_b_runtime_profile,
    )
    from decision_workbench.modeling.tabular.features import (
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
        from decision_workbench.data.profile_family_registry import load_profile_document
        from decision_workbench.modeling.observation_model_builder import (
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
    from decision_workbench.modeling.observation_regression import (
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
    from decision_workbench.modeling.tabular.features import candidate_from_observation

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
    package_override_env="DECISION_WORKBENCH_WELDING_STAGE_B_MODEL_PACKAGE",
    source_env="WORKBENCH_WELDING_STAGE_B_SOURCE_PATH",
    source_kind="welding_multistage",
    default_source=Path(
        "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
    ),
    data_loader=_load_welding_stage_b,
    runtime_factory=_welding_stage_b_runtime,
    feature_row_builder=_welding_stage_b_features,
    training_inspector=CANONICAL_TRAINING_INSPECTOR,
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
        gallery_metadata=SampleGalleryMetadata(
            question="原料配合と溶接条件から、溶着金属の成分をどこまで予測できるか？",
            scenario_summary="原料・配合・施工をまたぐ多段構造を、Stage Bの成分予測として確認します。",
            domain="溶接材料・多段工程",
            data_shape="配合明細 + 工程条件 + 中間成分の関係データ",
            source_kind="synthetic",
            source_label="Bundled welding consumable multistage synthetic dataset",
            source_url="docs/examples/welding-consumable-sample-dataset.md",
            license="リポジトリ同梱の合成教材データ",
            citation="Evidence Decision Workbench bundled synthetic welding sample",
            record_summary="120配合・300施工。Stage Bは溶着金属成分16出力を扱います。",
            limitations="実測値ではなく、冶金的な妥当性や実製造の保証は確認できません。多段の来歴・能力境界を読むための教材です。",
            documentation_path="docs/examples/welding-consumable-sample-dataset.md",
        ),
    ),
)

WELDING_STAGE_C_TASK_MODULE = TaskModule(
    task_id=WELDING_STAGE_C_TASK_ID,
    package_override_env="DECISION_WORKBENCH_WELDING_STAGE_C_MODEL_PACKAGE",
    source_env="WORKBENCH_WELDING_STAGE_C_SOURCE_PATH",
    source_kind="welding_stage_c",
    default_source=Path(
        "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
    ),
    data_loader=_observation_loader(WELDING_STAGE_C_TASK_ID),
    runtime_factory=_observation_runtime,
    feature_row_builder=_observation_features(WELDING_STAGE_C_TASK_ID),
    training_inspector=CANONICAL_TRAINING_INSPECTOR,
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


def _graph_property_task_module(
    task_id: str,
    label: str,
) -> TaskModule:
    return TaskModule(
        task_id=task_id,
        package_override_env=(
            "DECISION_WORKBENCH_"
            + task_id.upper().replace("-", "_")
            + "_MODEL_PACKAGE"
        ),
        source_env="WORKBENCH_WELDING_STAGE_C_SOURCE_PATH",
        source_kind="welding_graph_synthetic_demonstration",
        default_source=Path(
            "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
        ),
        data_loader=_observation_loader(task_id),
        runtime_factory=_observation_runtime,
        feature_row_builder=_observation_features(task_id),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        specialized_package_builder=_observation_builder(task_id),
        application=_application_capability(
            actual_measurement=False,
            response_curve=False,
            similarity=True,
            project_creation=False,
        ),
    )


WELDING_GRAPH_PROPERTY_TASK_MODULES = (
    _graph_property_task_module(
        WELDING_GRAPH_TENSILE_TASK_ID,
        "Graph比較用: 引張強さ",
    ),
    _graph_property_task_module(
        WELDING_GRAPH_TOUGHNESS_TASK_ID,
        "Graph比較用: 吸収エネルギー",
    ),
    _graph_property_task_module(
        WELDING_GRAPH_CORROSION_TASK_ID,
        "Graph比較用: 腐食速度",
    ),
)
