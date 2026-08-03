from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.task_contracts import TaskDefinition
from decision_workbench.data.profiles.schema import DatasetInputProfile
from decision_workbench.task_composition.builtin.shared import (
    TABULAR_EXPLORER,
    _application_capability,
    _curve_family,
    _standard_response_curve,
)
from decision_workbench.task_composition.builtin.sources import DATA_ROOT
from decision_workbench.task_composition.descriptors import (
    SampleGalleryMetadata,
    StandardModelAuthoring,
    StarterProject,
    TaskModule,
)
from decision_workbench.task_composition.ports import (
    DataDescriptor,
    DataLoader,
    FeatureRowBuilder,
    PredictionRuntime,
)
from decision_workbench.task_composition.training_inspector import (
    CANONICAL_TRAINING_INSPECTOR,
)

if TYPE_CHECKING:
    from decision_workbench.modeling.packages.verification import VerifiedModelPackage


HEAT_TREATMENT_TASK_ID = "heat-treatment-tradeoff-v1"
CONCRETE_TASK_ID = "concrete-strength-v1"
WEAR_CURVE_TASK_ID = "wear-curve-v1"
BATTERY_DEGRADATION_TASK_ID = "battery-degradation-v1"
SECOM_YIELD_TASK_ID = "secom-yield-risk-v1"
MPEA_LEGACY_TYS_TASK_ID = "mpea-literature-tys-v1"
MPEA_ROOM_TENSILE_TASK_ID = "mpea-room-tensile-v1"
MPEA_HARDNESS_TASK_ID = "mpea-hardness-process-v1"
WELDING_GRAPH_DEPOSITION_EFFICIENCY_TASK_ID = (
    "welding-graph-deposition-efficiency-v1"
)

_TABULAR_PROFILES = {
    HEAT_TREATMENT_TASK_ID: DATA_ROOT / "tabular-profile-heat-treatment-v1.json",
    CONCRETE_TASK_ID: DATA_ROOT / "tabular-profile-concrete-v1.json",
    WEAR_CURVE_TASK_ID: DATA_ROOT / "tabular-profile-wear-curve-v1.json",
    BATTERY_DEGRADATION_TASK_ID: DATA_ROOT
    / "tabular-profile-battery-degradation-v1.json",
    SECOM_YIELD_TASK_ID: DATA_ROOT / "tabular-profile-secom-yield-v1.json",
    MPEA_LEGACY_TYS_TASK_ID: DATA_ROOT / "tabular-profile-mpea-literature-tys-v1.json",
    MPEA_ROOM_TENSILE_TASK_ID: DATA_ROOT / "tabular-profile-mpea-room-tensile-v1.json",
    MPEA_HARDNESS_TASK_ID: DATA_ROOT / "tabular-profile-mpea-hardness-v1.json",
}


def _tabular_training_candidate(
    row: dict[str, Any],
    data: DataDescriptor,
) -> CandidateInput | None:
    from decision_workbench.modeling.tabular.features import candidate_from_observation

    return candidate_from_observation(row, data.profile)


def _tabular_loader(task_id: str) -> DataLoader:
    def load(path: Path, profile: DatasetInputProfile | None = None) -> DataDescriptor:
        from decision_workbench.modeling.tabular.data import load_tabular_data
        from decision_workbench.modeling.tabular.profile import TabularDatasetProfile

        if profile is not None and not isinstance(profile, TabularDatasetProfile):
            raise ValueError("tabular task requires a tabular Dataset Profile")
        selected = profile or _TABULAR_PROFILES[task_id]
        return load_tabular_data(path, selected)

    return load


def _tabular_profile_loader(profile_path: Path) -> DataLoader:
    """Build the generic tabular loader from one reviewed external Profile."""

    def load(
        path: Path,
        profile: DatasetInputProfile | None = None,
    ) -> DataDescriptor:
        from decision_workbench.modeling.tabular.data import load_tabular_data
        from decision_workbench.modeling.tabular.profile import (
            TabularDatasetProfile,
            load_tabular_profile,
        )

        if profile is not None and not isinstance(profile, TabularDatasetProfile):
            raise ValueError("tabular task requires a tabular Dataset Profile")
        return load_tabular_data(
            path,
            profile or load_tabular_profile(profile_path),
            profile_locator=profile_path,
        )

    return load


def _tabular_runtime(
    data: DataDescriptor, package: VerifiedModelPackage
) -> PredictionRuntime:
    from decision_workbench.modeling.tabular.runtime import TabularRegressionRuntime

    return TabularRegressionRuntime(
        data,
        package,
        missing_policy_inputs=data.profile.inputs,
    )


def _tabular_features(task_id: str) -> FeatureRowBuilder:
    def build(row: dict[str, Any], medians: dict[str, float]) -> Any:
        from decision_workbench.modeling.tabular.features import (
            build_tabular_features_from_observation,
        )
        from decision_workbench.modeling.tabular.profile import load_tabular_profile

        return build_tabular_features_from_observation(
            row, medians, load_tabular_profile(_TABULAR_PROFILES[task_id])
        )

    return build


def _tabular_profile_features(profile_path: Path) -> FeatureRowBuilder:
    def build(row: dict[str, Any], medians: dict[str, float]) -> Any:
        from decision_workbench.modeling.tabular.features import (
            build_tabular_features_from_observation,
        )
        from decision_workbench.modeling.tabular.profile import load_tabular_profile

        return build_tabular_features_from_observation(
            row,
            medians,
            load_tabular_profile(profile_path),
        )

    return build


def _tabular_starter(task_id: str, name: str) -> StarterProject:
    def candidates(
        runtime: PredictionRuntime,
        _task_definition: TaskDefinition,
    ) -> list[CandidateInput]:
        data = runtime.data
        eligible = [row for row in data.observations if row["eligible"]]
        from decision_workbench.modeling.tabular.features import candidate_from_observation

        if data.profile.group_column and data.profile.curve_axis_path:
            axis_key = data.profile.curve_axis_path.split(".", 1)[1]
            axis_values = [float(row["features"][axis_key]) for row in eligible]
            comparison_axis = (
                min(axis_values) + (max(axis_values) - min(axis_values)) * 0.1
            )
            by_group: dict[str, list[dict[str, Any]]] = {}
            for row in eligible:
                by_group.setdefault(str(row["parent_key"]), []).append(row)
            comparable = [
                min(
                    rows,
                    key=lambda row: abs(
                        float(row["features"][axis_key]) - comparison_axis
                    ),
                )
                for rows in by_group.values()
            ]
            target = data.profile.outputs[0].key
            comparable.sort(key=lambda row: float(row["outputs"][target]))
            indexes = (
                len(comparable) // 10,
                len(comparable) // 2,
                len(comparable) * 9 // 10,
            )
            selected = [comparable[index] for index in indexes]
        else:
            target = data.profile.outputs[0].key
            comparable = sorted(
                (row for row in eligible if target in row["outputs"]),
                key=lambda row: float(row["outputs"][target]),
            )
            indexes = (
                (len(comparable) // 4, len(comparable) // 2, len(comparable) - 1)
                if task_id == SECOM_YIELD_TASK_ID
                else (
                    len(comparable) // 4,
                    len(comparable) // 2,
                    len(comparable) * 3 // 4,
                )
            )
            selected = [comparable[index] for index in indexes]
        return [
            candidate_from_observation(row, data.profile).model_copy(
                update={"name": label}
            )
            for row, label in zip(
                selected, ("低位条件", "代表条件", "高位条件"), strict=True
            )
        ]

    return StarterProject(f"{task_id}-default", name, candidates, seed_on_upgrade=True)


def external_tabular_task_module(
    *,
    task_id: str,
    label: str,
    source_path: Path,
    profile_path: Path,
    estimator_ids: tuple[str, ...],
    default_estimator_id: str,
    package_path: Path | None,
    project_creation: bool = True,
    actual_measurement: bool = False,
) -> TaskModule:
    """Compose a reviewed data-only Task bundle without loading Python code."""

    env_key = "".join(
        character if character.isalnum() else "_" for character in task_id.upper()
    )
    return TaskModule(
        task_id=task_id,
        package_override_env=f"WORKBENCH_{env_key}_MODEL_PACKAGE",
        source_env=f"WORKBENCH_{env_key}_SOURCE_PATH",
        source_kind=f"external_task:{task_id}",
        default_source=source_path,
        default_package=package_path,
        data_loader=_tabular_profile_loader(profile_path),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_profile_features(profile_path),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            estimator_ids,
            default_estimator_id=default_estimator_id,
        ),
        application=_application_capability(
            actual_measurement=actual_measurement,
            response_curve=True,
            similarity=True,
            project_creation=project_creation,
        ),
        starter_project=_tabular_starter(task_id, label),
        response_curve=_standard_response_curve,
        data_explorer=TABULAR_EXPLORER,
    )


TABULAR_TASK_MODULES = (
    external_tabular_task_module(
        task_id=WELDING_GRAPH_DEPOSITION_EFFICIENCY_TASK_ID,
        label="Graph比較用: 溶着効率proxy",
        source_path=Path(
            "data/fixtures/prediction-graph/"
            "welding_deposition_efficiency_synthetic.csv"
        ),
        profile_path=DATA_ROOT
        / "tabular-profile-welding-graph-deposition-efficiency-v1.json",
        estimator_ids=("ridge.v1",),
        default_estimator_id="ridge.v1",
        package_path=None,
        project_creation=False,
    ),
    TaskModule(
        task_id=HEAT_TREATMENT_TASK_ID,
        package_override_env="DECISION_WORKBENCH_HEAT_TREATMENT_MODEL_PACKAGE",
        source_env="WORKBENCH_HEAT_TREATMENT_SOURCE_PATH",
        source_kind="external_heat_treatment",
        default_source=Path("data/source/external/heat_treatment_tradeoff_samples.csv"),
        data_loader=_tabular_loader(HEAT_TREATMENT_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(HEAT_TREATMENT_TASK_ID),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1", "lightgbm-regression.v1"),
            default_estimator_id="ridge.v1",
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=True,
            similarity=True,
            prediction_space_targets=("hardness_hv", "charpy_j"),
            input_space_target="hardness_hv",
            contour_axes=(
                "process.tempering_temp_c",
                "process.cooling_rate_c_per_s",
                "composition.carbon_pct",
            ),
        ),
        starter_project=replace(
            _tabular_starter(HEAT_TREATMENT_TASK_ID, "熱処理の硬さ・靭性"),
            distribution="legacy_hidden",
        ),
        response_curve=_standard_response_curve,
        data_explorer=TABULAR_EXPLORER,
    ),
    TaskModule(
        task_id=CONCRETE_TASK_ID,
        package_override_env="DECISION_WORKBENCH_CONCRETE_MODEL_PACKAGE",
        source_env="WORKBENCH_CONCRETE_SOURCE_PATH",
        source_kind="external_concrete",
        default_source=Path("data/source/external/concrete_mix_samples.csv"),
        data_loader=_tabular_loader(CONCRETE_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(CONCRETE_TASK_ID),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1", "lightgbm-regression.v1"),
            default_estimator_id="ridge.v1",
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=True,
            similarity=True,
            curve_family=True,
            contour_axes=(
                "process.age_days",
                "composition.water_kg_m3",
                "composition.cement_kg_m3",
            ),
            input_space_target="compressive_strength_mpa",
        ),
        starter_project=replace(
            _tabular_starter(CONCRETE_TASK_ID, "コンクリート配合と強度"),
            distribution="legacy_hidden",
        ),
        response_curve=_standard_response_curve,
        curve_family=_curve_family,
        data_explorer=TABULAR_EXPLORER,
    ),
    TaskModule(
        task_id=WEAR_CURVE_TASK_ID,
        package_override_env="DECISION_WORKBENCH_WEAR_CURVE_MODEL_PACKAGE",
        source_env="WORKBENCH_WEAR_CURVE_SOURCE_PATH",
        source_kind="external_wear_curve",
        default_source=Path("data/source/external/wear_curve_samples.csv"),
        data_loader=_tabular_loader(WEAR_CURVE_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(WEAR_CURVE_TASK_ID),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1", "lightgbm-regression.v1"),
            default_estimator_id="ridge.v1",
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=True,
            similarity=True,
            curve_family=True,
            contour_axes=(
                "process.cutting_distance_m",
                "process.cutting_speed_m_per_min",
                "process.feed_mm_per_rev",
            ),
            input_space_target="wear_vb_um",
        ),
        starter_project=replace(
            _tabular_starter(WEAR_CURVE_TASK_ID, "工具摩耗曲線"),
            distribution="legacy_hidden",
        ),
        response_curve=_standard_response_curve,
        curve_family=_curve_family,
        data_explorer=TABULAR_EXPLORER,
    ),
    TaskModule(
        task_id=BATTERY_DEGRADATION_TASK_ID,
        package_override_env="DECISION_WORKBENCH_BATTERY_DEGRADATION_MODEL_PACKAGE",
        source_env="WORKBENCH_BATTERY_DEGRADATION_SOURCE_PATH",
        source_kind="external_battery_degradation",
        default_source=Path("data/source/external/battery_calce_cs2_cycles.csv"),
        data_loader=_tabular_loader(BATTERY_DEGRADATION_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(BATTERY_DEGRADATION_TASK_ID),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("lightgbm-regression.v1",),
            default_estimator_id="lightgbm-regression.v1",
            default_estimator_options=(
                ("num_boost_round", 200),
                ("predictive_family", "normal"),
                (
                    "monotone_decreasing_features",
                    ("process.cycle_index",),
                ),
            ),
            recipe_policy="specialized_constraints",
            required_estimator_options=(
                (
                    "monotone_decreasing_features",
                    ("process.cycle_index",),
                ),
            ),
            specialization_reason=(
                "容量維持率はcycle_indexに対して単調非増加である科学制約を保つ"
            ),
        ),
        application=_application_capability(
            actual_measurement=True,
            response_curve=True,
            similarity=True,
            curve_family=True,
            contour_axes=("process.cycle_index", "process.discharge_rate_c"),
            input_space_target="capacity_percent",
        ),
        starter_project=replace(
            _tabular_starter(BATTERY_DEGRADATION_TASK_ID, "電池容量劣化"),
            distribution="gallery",
            gallery_metadata=SampleGalleryMetadata(
                question="放電サイクルとレートから、容量維持率の低下をどこまで見通せるか？",
                scenario_summary="4セルの公開実測ログを、cell単位の検証と予測区間つきで確認します。",
                domain="電池劣化",
                data_shape="セル内の時系列（サイクルごとの観測）",
                source_kind="public",
                source_label="University of Maryland CALCE CS2 battery data",
                source_url="https://calce.umd.edu/data",
                license="公開元の利用条件を確認（出版利用時は対応論文を引用）",
                citation="CALCE Battery Research Group, CS2 cell data",
                record_summary="4 cell・3,131 cycle。完全放電サイクルを1行として派生。",
                limitations="4セルのみで、レート差とセル固有差を分離できません。精度保証ではなく、実測系列の支持範囲と不確かさを読む教材です。",
                documentation_path="docs/contracts/reference-data-loop.md",
            ),
        ),
        response_curve=_standard_response_curve,
        curve_family=_curve_family,
        data_explorer=TABULAR_EXPLORER,
    ),
    TaskModule(
        task_id=SECOM_YIELD_TASK_ID,
        package_override_env="DECISION_WORKBENCH_SECOM_YIELD_MODEL_PACKAGE",
        source_env="WORKBENCH_SECOM_YIELD_SOURCE_PATH",
        source_kind="external_secom",
        default_source=Path("data/source/external/secom_stress.csv"),
        data_loader=_tabular_loader(SECOM_YIELD_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(SECOM_YIELD_TASK_ID),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("lightgbm-binary.v1",),
            default_estimator_id="lightgbm-binary.v1",
            default_estimator_options=(("num_boost_round", 100),),
        ),
        application=_application_capability(
            actual_measurement=True,
            response_curve=True,
            similarity=True,
            contour_axes=(
                "process.sensor_059",
                "process.sensor_103",
                "process.sensor_130",
                "process.sensor_102",
            ),
            input_space_target="fail_probability",
        ),
        starter_project=replace(
            _tabular_starter(SECOM_YIELD_TASK_ID, "SECOM工程異常リスク"),
            distribution="legacy_hidden",
        ),
        response_curve=_standard_response_curve,
        data_explorer=TABULAR_EXPLORER,
    ),
    TaskModule(
        task_id=MPEA_LEGACY_TYS_TASK_ID,
        package_override_env="DECISION_WORKBENCH_MPEA_LEGACY_TYS_MODEL_PACKAGE",
        source_env="WORKBENCH_MPEA_LITERATURE_SOURCE_PATH",
        source_kind="external_mpea_literature",
        default_source=Path("data/source/external/mpea_ground_truth_18021833.csv"),
        data_loader=_tabular_loader(MPEA_LEGACY_TYS_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(MPEA_LEGACY_TYS_TASK_ID),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1", "lightgbm-regression.v1"),
            default_estimator_id="ridge.v1",
            default_estimator_options=(("alpha", 1000.0),),
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=False,
            similarity=True,
            project_creation=False,
        ),
    ),
    TaskModule(
        task_id=MPEA_ROOM_TENSILE_TASK_ID,
        package_override_env="DECISION_WORKBENCH_MPEA_ROOM_TENSILE_MODEL_PACKAGE",
        source_env="WORKBENCH_MPEA_LITERATURE_SOURCE_PATH",
        source_kind="external_mpea_literature",
        default_source=Path("data/source/external/mpea_ground_truth_18021833.csv"),
        data_loader=_tabular_loader(MPEA_ROOM_TENSILE_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(MPEA_ROOM_TENSILE_TASK_ID),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1", "lightgbm-regression.v1"),
            default_estimator_id="ridge.v1",
            default_estimator_options=(("alpha", 1000.0),),
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=False,
            similarity=True,
            prediction_space_targets=("TYS", "UTS", "EL"),
            input_space_target="TYS",
        ),
        starter_project=replace(
            _tabular_starter(
                MPEA_ROOM_TENSILE_TASK_ID,
                "MPEA文献の室温引張特性",
            ),
            distribution="gallery",
            gallery_metadata=SampleGalleryMetadata(
                question="組成と熱処理条件から、室温の引張特性をどう比較できるか？",
                scenario_summary="文献由来の組成・工程記録を使い、強さと延性を同じ候補で読みます。",
                domain="多主元素合金（MPEA）",
                data_shape="表形式（組成 + 工程条件 + 複数出力）",
                source_kind="public",
                source_label="Zenodo 18021833 MPEA literature dataset",
                source_url="https://doi.org/10.5281/zenodo.18021833",
                license="CC BY 4.0",
                citation="Dataset DOI: 10.5281/zenodo.18021833; article DOI: 10.1016/j.dib.2026.112540",
                record_summary="100報の文献から396材料記録。欠測や単位揺れを含むため、Profileで安全に正規化します。",
                limitations="文献の後処理観測や論文識別子は予測入力に使いません。保守的なbaselineであり、文献級の精度や実材料の保証ではありません。",
                documentation_path="docs/examples/mpea-literature-data-pipeline.md",
            ),
        ),
        data_explorer=TABULAR_EXPLORER,
    ),
    TaskModule(
        task_id=MPEA_HARDNESS_TASK_ID,
        package_override_env="DECISION_WORKBENCH_MPEA_HARDNESS_MODEL_PACKAGE",
        source_env="WORKBENCH_MPEA_LITERATURE_SOURCE_PATH",
        source_kind="external_mpea_literature",
        default_source=Path("data/source/external/mpea_ground_truth_18021833.csv"),
        data_loader=_tabular_loader(MPEA_HARDNESS_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(MPEA_HARDNESS_TASK_ID),
        training_inspector=CANONICAL_TRAINING_INSPECTOR,
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1", "lightgbm-regression.v1"),
            default_estimator_id="ridge.v1",
            default_estimator_options=(("alpha", 1000.0),),
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=False,
            similarity=True,
            input_space_target="HV",
        ),
        starter_project=replace(
            _tabular_starter(MPEA_HARDNESS_TASK_ID, "MPEA文献の硬さ"),
            distribution="legacy_hidden",
        ),
        data_explorer=TABULAR_EXPLORER,
    ),
)
