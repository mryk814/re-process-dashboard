"""Allow-listed task integration points owned by the application.

This is deliberately an internal registry, not a plugin loader. Adding a task
requires adding one explicit ``TaskModule`` here so startup, model workflow,
package verification, and optional operations all see the same task set.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from material_workbench.modeling.model_packages import VerifiedModelPackage
from material_workbench.data.dataset_profile import DatasetInputProfile
from material_workbench.contracts.schemas import Candidate, CandidateInput
from material_workbench.contracts.task_contracts import ApplicationCapability, DataExplorerCapability

ANNEALED_TASK_ID = "annealed-properties-v1"
HOT_ROLLING_TASK_ID = "hot-rolled-properties-v1"
FLANK_WEAR_TASK_ID = "flank-wear-v1"
HEAT_TREATMENT_TASK_ID = "heat-treatment-tradeoff-v1"
CONCRETE_TASK_ID = "concrete-strength-v1"
WEAR_CURVE_TASK_ID = "wear-curve-v1"
BATTERY_DEGRADATION_TASK_ID = "battery-degradation-v1"
MPEA_LITERATURE_TASK_ID = "mpea-literature-tys-v1"
PRIMARY_DEFAULT_SOURCE = Path("data/source/material_workbench_tutorial_v1.xlsx")
PROCESS_SOURCE = Path("data/source/material_workbench_process_v1.xlsx")
_DATA_ROOT = Path(__file__).parent / "data"
_TABULAR_PROFILES = {
    HEAT_TREATMENT_TASK_ID: _DATA_ROOT / "tabular-profile-heat-treatment-v1.json",
    CONCRETE_TASK_ID: _DATA_ROOT / "tabular-profile-concrete-v1.json",
    WEAR_CURVE_TASK_ID: _DATA_ROOT / "tabular-profile-wear-curve-v1.json",
    BATTERY_DEGRADATION_TASK_ID: _DATA_ROOT / "tabular-profile-battery-degradation-v1.json",
    MPEA_LITERATURE_TASK_ID: _DATA_ROOT / "tabular-profile-mpea-room-tensile-v1.json",
}


@runtime_checkable
class DataDescriptor(Protocol):
    source_path: str
    source_sha256: str
    profile_path: str
    profile_id: str
    observations: list[dict[str, Any]]
    medians: dict[str, float]


@runtime_checkable
class PredictionRuntime(Protocol):
    task_id: str
    data: DataDescriptor
    model_package: VerifiedModelPackage | None
    support_policy_id: str

    @property
    def output_keys(self) -> frozenset[str]: ...

    def predict(self, candidate: Any, **kwargs: Any) -> dict[str, Any]: ...

    def predict_core(self, candidate: Any, **kwargs: Any) -> dict[str, Any]: ...


@runtime_checkable
class SupportProvider(Protocol):
    def evidence(self, candidate: Any) -> tuple[Any, list[dict[str, Any]]]: ...

    def support_summary(self, candidate: Any) -> Any: ...

    def support_by_target(self, candidate: Any) -> dict[str, Any]: ...

    def similarity(self, candidate: Any, limit: int = 6) -> list[dict[str, Any]]: ...


ResponseCurveHandler = Callable[
    [PredictionRuntime, Candidate, str, str, int, tuple[float, float] | None, str | None, float | None],
    dict[str, Any],
]
CurveFamilyHandler = Callable[[PredictionRuntime, Candidate, str, str | None, int, int], dict[str, Any]]
DataLoader = Callable[[Path, DatasetInputProfile | None], DataDescriptor]
RuntimeFactory = Callable[[DataDescriptor, Path], PredictionRuntime]
FeatureRowBuilder = Callable[[dict[str, Any], dict[str, float]], Any]
ModelBuilder = Callable[..., None]


@dataclass(frozen=True)
class StarterProject:
    project_id: str
    name: str
    candidate_factory: Callable[[dict[str, float]], list[CandidateInput]]
    seed_on_upgrade: bool = False


@dataclass(frozen=True)
class TaskModule:
    task_id: str
    package_override_env: str
    source_env: str
    source_kind: str
    default_source: Path
    data_loader: DataLoader
    runtime_factory: RuntimeFactory
    feature_row_builder: FeatureRowBuilder
    model_builder: ModelBuilder
    application: ApplicationCapability = ApplicationCapability()
    data_explorer: DataExplorerCapability | None = None
    starter_project: StarterProject | None = None
    response_curve: ResponseCurveHandler | None = None
    curve_family: CurveFamilyHandler | None = None


def _annealed_starter_candidates(medians: dict[str, float]) -> list[CandidateInput]:
    composition = {key: round(value, 5) for key, value in medians.items()}
    reference_line_speed = 103.0
    reference_times = (0.0, 280.0, 340.0, 650.0)
    variants = (
        ("基準候補", 1.00, 810.0, 103.0),
        ("高強度案", 1.16, 830.0, 96.0),
        ("延性重視案", 0.88, 790.0, 112.0),
    )
    return [
        CandidateInput(
            name=name,
            inputs={
                "composition": {**composition, "C": round(composition["C"] * carbon_factor, 5)},
                "process": {"ls_mpm": line_speed},
                "categorical": {},
                "heat_pattern": [
                    {
                        "time_s": round(time_s * reference_line_speed / line_speed, 6),
                        "temperature_c": temperature_c,
                    }
                    for time_s, temperature_c in zip(
                        reference_times,
                        (25.0, peak - 10.0, peak, 120.0),
                        strict=True,
                    )
                ],
            },
        )
        for name, carbon_factor, peak, line_speed in variants
    ]


def _hot_rolling_starter_candidates(medians: dict[str, float]) -> list[CandidateInput]:
    composition = {key: round(value, 5) for key, value in medians.items()}
    variants = (
        ("基準熱延", 1170, 900, 34, 3.4, 1160, 30),
        ("高温保持", 1200, 910, 36, 3.2, 1180, 42),
        ("延性重視", 1160, 920, 34, 3.8, 1140, 34),
    )
    keys = (
        "soaking_temperature_c", "finish_temperature_c", "entry_thickness_mm",
        "exit_thickness_mm", "hold_temperature_c", "hold_time_min",
    )
    return [CandidateInput(
        name=name,
        inputs={
            "composition": composition,
            "process": dict(zip(keys, values)),
            "categorical": {},
            "heat_pattern": None,
        },
    ) for name, *values in variants]


def _load_workbook(path: Path, profile: DatasetInputProfile | None = None) -> DataDescriptor:
    from material_workbench.data.importer import load_workbook_data

    return load_workbook_data(path, profile=profile)


def _load_flank_wear(path: Path, profile: DatasetInputProfile | None = None) -> DataDescriptor:
    from material_workbench.modeling.flank_wear import load_flank_wear_data

    return load_flank_wear_data(path, profile=profile)


def _tabular_loader(task_id: str) -> DataLoader:
    def load(path: Path, profile: DatasetInputProfile | None = None) -> DataDescriptor:
        from material_workbench.modeling.tabular_regression import (
            TabularDatasetProfile,
            load_tabular_data,
        )

        selected = profile if isinstance(profile, TabularDatasetProfile) else _TABULAR_PROFILES[task_id]
        return load_tabular_data(path, selected)
    return load


def _annealed_runtime(data: DataDescriptor, package: Path) -> PredictionRuntime:
    from material_workbench.modeling.runtime import ModelRuntime

    return ModelRuntime(data, package_root=package)  # type: ignore[arg-type]


def _hot_rolling_runtime(data: DataDescriptor, package: Path) -> PredictionRuntime:
    from material_workbench.modeling.hot_rolling import HotRollingRuntime

    return HotRollingRuntime(data, package_root=package)  # type: ignore[arg-type]


def _flank_wear_runtime(data: DataDescriptor, package: Path) -> PredictionRuntime:
    from material_workbench.modeling.flank_wear import FlankWearRuntime

    return FlankWearRuntime(data, package_root=package)  # type: ignore[arg-type]


def _tabular_runtime(data: DataDescriptor, package: Path) -> PredictionRuntime:
    from material_workbench.modeling.tabular_regression import TabularRegressionRuntime

    return TabularRegressionRuntime(data, package)  # type: ignore[arg-type]


def _annealed_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from material_workbench.modeling.feature_pipeline import build_feature_bundle_from_observation

    return build_feature_bundle_from_observation(row, medians)


def _hot_rolling_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from material_workbench.modeling.hot_rolling_feature_pipeline import build_hot_rolling_features_from_observation

    return build_hot_rolling_features_from_observation(row, medians)


def _flank_wear_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from material_workbench.modeling.flank_wear_feature_pipeline import build_flank_wear_features_from_observation

    return build_flank_wear_features_from_observation(row, medians)


def _tabular_features(task_id: str) -> FeatureRowBuilder:
    def build(row: dict[str, Any], medians: dict[str, float]) -> Any:
        from material_workbench.modeling.tabular_regression import (
            build_tabular_features_from_observation,
            load_tabular_profile,
        )

        return build_tabular_features_from_observation(
            row, medians, load_tabular_profile(_TABULAR_PROFILES[task_id])
        )
    return build


def _build_annealed(source: Path, output: Path, *, replace: bool) -> None:
    from build_default_model_package import build

    build(source, output, replace=replace)


def _build_hot_rolling(source: Path, output: Path, *, replace: bool) -> None:
    from build_hot_rolling_model_package import build

    build(source, output, replace=replace)


def _build_flank_wear(source: Path, output: Path, *, replace: bool) -> None:
    from build_flank_wear_model_package import build

    build(source, output, replace=replace)


def _tabular_builder(task_id: str) -> ModelBuilder:
    def build(source: Path, output: Path, *, replace: bool) -> None:
        from material_workbench.modeling.tabular_model_builder import build as build_package

        build_package(source, _TABULAR_PROFILES[task_id], output, replace=replace)
    return build


def _standard_response_curve(
    runtime: PredictionRuntime,
    candidate: Candidate,
    target: str,
    variable: str,
    points: int,
    axis_range: tuple[float, float] | None,
    _stage_name: str | None,
    _stage_position_m: float | None,
) -> dict[str, Any]:
    return runtime.response_curve_result(candidate, target, variable, points, axis_range)  # type: ignore[attr-defined]


def _annealed_response_curve(
    runtime: PredictionRuntime,
    candidate: Candidate,
    target: str,
    variable: str,
    points: int,
    axis_range: tuple[float, float] | None,
    stage_name: str | None,
    stage_position_m: float | None,
) -> dict[str, Any]:
    if variable.startswith("heat.") and variable != "heat.stage_temperature_c":
        raise ValueError("ヒートパターンは工程名温度またはラインスピードで操作してください")
    return runtime.response_curve_result(  # type: ignore[attr-defined]
        candidate, target, variable, points, axis_range, stage_name, stage_position_m
    )


def _curve_family(
    runtime: PredictionRuntime,
    candidate: Candidate,
    target: str,
    vary: str | None,
    levels: int,
    points: int,
) -> dict[str, Any]:
    return runtime.curve_family_result(candidate, target, vary, levels, points)  # type: ignore[attr-defined]


def _tabular_starter(task_id: str, name: str) -> StarterProject:
    def candidates(_medians: dict[str, float]) -> list[CandidateInput]:
        from material_workbench.modeling.tabular_regression import load_tabular_data

        module = TASK_MODULES[task_id]
        source = resolve_task_source(task_id)
        data = load_tabular_data(source, _TABULAR_PROFILES[task_id])
        eligible = [row for row in data.observations if row["eligible"]]
        from material_workbench.modeling.tabular_regression import candidate_from_observation

        if data.profile.group_column and data.profile.curve_axis_path:
            axis_key = data.profile.curve_axis_path.split(".", 1)[1]
            axis_values = [float(row["features"][axis_key]) for row in eligible]
            comparison_axis = min(axis_values) + (max(axis_values) - min(axis_values)) * 0.1
            by_group: dict[str, list[dict[str, Any]]] = {}
            for row in eligible:
                by_group.setdefault(str(row["parent_key"]), []).append(row)
            comparable = [
                min(rows, key=lambda row: abs(float(row["features"][axis_key]) - comparison_axis))
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
            indexes = (len(comparable) // 4, len(comparable) // 2, len(comparable) * 3 // 4)
            selected = [comparable[index] for index in indexes]
        return [
            candidate_from_observation(row, data.profile).model_copy(
                update={"name": label}
            )
            for row, label in zip(selected, ("低位条件", "代表条件", "高位条件"), strict=True)
        ]
    return StarterProject(f"{task_id}-default", name, candidates, seed_on_upgrade=True)


_EXPLORER = DataExplorerCapability(quality=True, lineage=True, candidate_creation=True)
_TABULAR_EXPLORER = DataExplorerCapability(quality=True, lineage=False, candidate_creation=False)

TASK_MODULES: Mapping[str, TaskModule] = MappingProxyType({
    ANNEALED_TASK_ID: TaskModule(
        task_id=ANNEALED_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_MODEL_PACKAGE",
        source_env="WORKBENCH_SOURCE_PATH",
        source_kind="primary",
        default_source=PRIMARY_DEFAULT_SOURCE,
        data_loader=_load_workbook,
        runtime_factory=_annealed_runtime,
        feature_row_builder=_annealed_features,
        model_builder=_build_annealed,
        application=ApplicationCapability(candidate_excel_import=True, candidate_excel_export=True),
        data_explorer=_EXPLORER,
        starter_project=StarterProject("default", "焼鈍条件の候補検討", _annealed_starter_candidates),
        response_curve=_annealed_response_curve,
    ),
    HOT_ROLLING_TASK_ID: TaskModule(
        task_id=HOT_ROLLING_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE",
        source_env="WORKBENCH_SOURCE_PATH",
        source_kind="primary",
        default_source=PRIMARY_DEFAULT_SOURCE,
        data_loader=_load_workbook,
        runtime_factory=_hot_rolling_runtime,
        feature_row_builder=_hot_rolling_features,
        model_builder=_build_hot_rolling,
        data_explorer=_EXPLORER,
        starter_project=StarterProject("hot-rolling-default", "熱延条件の候補検討", _hot_rolling_starter_candidates),
        response_curve=_standard_response_curve,
    ),
    FLANK_WEAR_TASK_ID: TaskModule(
        task_id=FLANK_WEAR_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_FLANK_WEAR_MODEL_PACKAGE",
        source_env="WORKBENCH_FLANK_WEAR_SOURCE_PATH",
        source_kind="flank_wear",
        default_source=Path("data/source/cutting_tool_flank_wear_synthetic_dataset.xlsx"),
        data_loader=_load_flank_wear,
        runtime_factory=_flank_wear_runtime,
        feature_row_builder=_flank_wear_features,
        model_builder=_build_flank_wear,
        response_curve=_standard_response_curve,
        curve_family=_curve_family,
    ),
    HEAT_TREATMENT_TASK_ID: TaskModule(
        task_id=HEAT_TREATMENT_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_HEAT_TREATMENT_MODEL_PACKAGE",
        source_env="WORKBENCH_HEAT_TREATMENT_SOURCE_PATH",
        source_kind="external_heat_treatment",
        default_source=Path("data/source/external/heat_treatment_tradeoff_samples.csv"),
        data_loader=_tabular_loader(HEAT_TREATMENT_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(HEAT_TREATMENT_TASK_ID),
        model_builder=_tabular_builder(HEAT_TREATMENT_TASK_ID),
        starter_project=_tabular_starter(HEAT_TREATMENT_TASK_ID, "熱処理の硬さ・靭性"),
        response_curve=_standard_response_curve,
        data_explorer=_TABULAR_EXPLORER,
    ),
    CONCRETE_TASK_ID: TaskModule(
        task_id=CONCRETE_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_CONCRETE_MODEL_PACKAGE",
        source_env="WORKBENCH_CONCRETE_SOURCE_PATH",
        source_kind="external_concrete",
        default_source=Path("data/source/external/concrete_mix_samples.csv"),
        data_loader=_tabular_loader(CONCRETE_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(CONCRETE_TASK_ID),
        model_builder=_tabular_builder(CONCRETE_TASK_ID),
        starter_project=_tabular_starter(CONCRETE_TASK_ID, "コンクリート配合と強度"),
        response_curve=_standard_response_curve,
        curve_family=_curve_family,
        data_explorer=_TABULAR_EXPLORER,
    ),
    WEAR_CURVE_TASK_ID: TaskModule(
        task_id=WEAR_CURVE_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_WEAR_CURVE_MODEL_PACKAGE",
        source_env="WORKBENCH_WEAR_CURVE_SOURCE_PATH",
        source_kind="external_wear_curve",
        default_source=Path("data/source/external/wear_curve_samples.csv"),
        data_loader=_tabular_loader(WEAR_CURVE_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(WEAR_CURVE_TASK_ID),
        model_builder=_tabular_builder(WEAR_CURVE_TASK_ID),
        starter_project=_tabular_starter(WEAR_CURVE_TASK_ID, "工具摩耗曲線"),
        response_curve=_standard_response_curve,
        curve_family=_curve_family,
        data_explorer=_TABULAR_EXPLORER,
    ),
    BATTERY_DEGRADATION_TASK_ID: TaskModule(
        task_id=BATTERY_DEGRADATION_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_BATTERY_DEGRADATION_MODEL_PACKAGE",
        source_env="WORKBENCH_BATTERY_DEGRADATION_SOURCE_PATH",
        source_kind="external_battery_degradation",
        default_source=Path("data/source/external/battery_cycle_samples.csv"),
        data_loader=_tabular_loader(BATTERY_DEGRADATION_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(BATTERY_DEGRADATION_TASK_ID),
        model_builder=_tabular_builder(BATTERY_DEGRADATION_TASK_ID),
        starter_project=_tabular_starter(BATTERY_DEGRADATION_TASK_ID, "電池容量劣化"),
        response_curve=_standard_response_curve,
        curve_family=_curve_family,
        data_explorer=_TABULAR_EXPLORER,
    ),
    MPEA_LITERATURE_TASK_ID: TaskModule(
        task_id=MPEA_LITERATURE_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_MPEA_LITERATURE_MODEL_PACKAGE",
        source_env="WORKBENCH_MPEA_LITERATURE_SOURCE_PATH",
        source_kind="external_mpea_literature",
        default_source=Path("data/source/external/mpea_ground_truth_18021833.csv"),
        data_loader=_tabular_loader(MPEA_LITERATURE_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(MPEA_LITERATURE_TASK_ID),
        model_builder=_tabular_builder(MPEA_LITERATURE_TASK_ID),
        starter_project=_tabular_starter(MPEA_LITERATURE_TASK_ID, "MPEA文献の室温引張特性"),
        data_explorer=_TABULAR_EXPLORER,
    ),
})


def registered_task_modules() -> Mapping[str, TaskModule]:
    return TASK_MODULES


def task_module(task_id: str) -> TaskModule:
    try:
        return TASK_MODULES[task_id]
    except KeyError as exc:
        raise ValueError(f"unknown registered task: {task_id}") from exc


def resolve_task_source(task_id: str, source: str | Path | None = None) -> Path:
    module = task_module(task_id)
    selected = Path(source) if source is not None else module.default_source
    # The workflow CLI historically supplied the primary workbook as its global
    # default. A dedicated-source task owns its own default instead.
    if module.source_kind != "primary" and selected == PRIMARY_DEFAULT_SOURCE:
        selected = module.default_source
    if selected.is_absolute() or selected.exists():
        return selected
    repository_source = Path(__file__).resolve().parents[3] / selected
    return repository_source if repository_source.exists() else selected
