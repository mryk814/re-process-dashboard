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

from .model_packages import VerifiedModelPackage
from .dataset_profile import DatasetInputProfile
from .schemas import Candidate, CandidateInput
from .task_contracts import ApplicationCapability, DataExplorerCapability

ANNEALED_TASK_ID = "annealed-properties-v1"
HOT_ROLLING_TASK_ID = "hot-rolled-properties-v1"
FLANK_WEAR_TASK_ID = "flank-wear-v1"
PRIMARY_DEFAULT_SOURCE = Path("data/source/material_workbench_tutorial_v1.xlsx")


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
    from .importer import load_workbook_data

    return load_workbook_data(path, profile=profile)


def _load_flank_wear(path: Path, profile: DatasetInputProfile | None = None) -> DataDescriptor:
    from .flank_wear import load_flank_wear_data

    return load_flank_wear_data(path, profile=profile)


def _annealed_runtime(data: DataDescriptor, package: Path) -> PredictionRuntime:
    from .runtime import ModelRuntime

    return ModelRuntime(data, package_root=package)  # type: ignore[arg-type]


def _hot_rolling_runtime(data: DataDescriptor, package: Path) -> PredictionRuntime:
    from .hot_rolling import HotRollingRuntime

    return HotRollingRuntime(data, package_root=package)  # type: ignore[arg-type]


def _flank_wear_runtime(data: DataDescriptor, package: Path) -> PredictionRuntime:
    from .flank_wear import FlankWearRuntime

    return FlankWearRuntime(data, package_root=package)  # type: ignore[arg-type]


def _annealed_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from .feature_pipeline import build_feature_bundle_from_observation

    return build_feature_bundle_from_observation(row, medians)


def _hot_rolling_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from .hot_rolling_feature_pipeline import build_hot_rolling_features_from_observation

    return build_hot_rolling_features_from_observation(row, medians)


def _flank_wear_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from .flank_wear_feature_pipeline import build_flank_wear_features_from_observation

    return build_flank_wear_features_from_observation(row, medians)


def _build_annealed(source: Path, output: Path, *, replace: bool) -> None:
    from build_default_model_package import build

    build(source, output, replace=replace)


def _build_hot_rolling(source: Path, output: Path, *, replace: bool) -> None:
    from build_hot_rolling_model_package import build

    build(source, output, replace=replace)


def _build_flank_wear(source: Path, output: Path, *, replace: bool) -> None:
    from build_flank_wear_model_package import build

    build(source, output, replace=replace)


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


_EXPLORER = DataExplorerCapability(quality=True, lineage=True, candidate_creation=True)

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
