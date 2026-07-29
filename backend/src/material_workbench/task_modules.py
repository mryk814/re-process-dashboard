"""Allow-listed task integration points owned by the application.

This is deliberately an internal registry, not a plugin loader. Adding a task
requires adding one explicit ``TaskModule`` here so startup, model workflow,
package verification, and optional operations all see the same task set.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

from material_workbench.modeling.model_packages import VerifiedModelPackage
from material_workbench.data.dataset_profile import DatasetInputProfile
from material_workbench.contracts.schemas import Candidate, CandidateInput
from material_workbench.contracts.task_contracts import (
    ApplicationCapability,
    BasicWorkbenchSurfaceDefinition,
    DataExplorerCapability,
    TaskDefinition,
    ResponseContourSurfaceDefinition,
)
from material_workbench.contracts.chain_uncertainty_contracts import StageSampleResult

ANNEALED_TASK_ID = "annealed-properties-v1"
HOT_ROLLING_TASK_ID = "hot-rolled-properties-v1"
FLANK_WEAR_TASK_ID = "flank-wear-v1"
HEAT_TREATMENT_TASK_ID = "heat-treatment-tradeoff-v1"
CONCRETE_TASK_ID = "concrete-strength-v1"
WEAR_CURVE_TASK_ID = "wear-curve-v1"
BATTERY_DEGRADATION_TASK_ID = "battery-degradation-v1"
SECOM_YIELD_TASK_ID = "secom-yield-risk-v1"
MPEA_LEGACY_TYS_TASK_ID = "mpea-literature-tys-v1"
MPEA_ROOM_TENSILE_TASK_ID = "mpea-room-tensile-v1"
MPEA_HARDNESS_TASK_ID = "mpea-hardness-process-v1"
WELDING_STAGE_C_TASK_ID = "welding-stage-c-properties-v1"
WELDING_STAGE_B_TASK_ID = "welding-consumable-stage-b-v1"
PRIMARY_DEFAULT_SOURCE = Path("data/source/material_workbench_tutorial_v2.xlsx")
PROCESS_SOURCE = Path("data/source/material_workbench_process_v1.xlsx")
_DATA_ROOT = Path(__file__).parent / "data"
_WELDING_STAGE_B_PROFILE = _DATA_ROOT / "welding-stage-b-profile-v1.json"


# Observation-family Taskごとのdata-only宣言。runtime本体はこれで駆動する。
# 特徴量の並び、target→family、per-target特徴量はProfileとTaskDefinitionから導出するので
# ここには書かない。output_boundsはTaskDefinitionのplausibility_range（表示・検証用）とは別に、
# Chain samplingが使う実行時のexact allow-listとして宣言する。
_OBSERVATION_DECLARATIONS: dict[str, dict[str, Any]] = {
    WELDING_STAGE_C_TASK_ID: {
        "task_id": WELDING_STAGE_C_TASK_ID,
        "profile_path": _DATA_ROOT / "observation-profile-welding-consumable-stage-c-v1.json",
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


_TABULAR_PROFILES = {
    HEAT_TREATMENT_TASK_ID: _DATA_ROOT / "tabular-profile-heat-treatment-v1.json",
    CONCRETE_TASK_ID: _DATA_ROOT / "tabular-profile-concrete-v1.json",
    WEAR_CURVE_TASK_ID: _DATA_ROOT / "tabular-profile-wear-curve-v1.json",
    BATTERY_DEGRADATION_TASK_ID: _DATA_ROOT / "tabular-profile-battery-degradation-v1.json",
    SECOM_YIELD_TASK_ID: _DATA_ROOT / "tabular-profile-secom-yield-v1.json",
    MPEA_LEGACY_TYS_TASK_ID: _DATA_ROOT / "tabular-profile-mpea-literature-tys-v1.json",
    MPEA_ROOM_TENSILE_TASK_ID: _DATA_ROOT / "tabular-profile-mpea-room-tensile-v1.json",
    MPEA_HARDNESS_TASK_ID: _DATA_ROOT / "tabular-profile-mpea-hardness-v1.json",
}


@runtime_checkable
class DataDescriptor(Protocol):
    """The common surface every Dataset Profile family's loader must return.

    Profile documents stay family-specific on purpose. What must not be
    family-specific is this boundary: model builders, the training-data
    Inspector and quality aggregation read only what is declared here.
    """

    source_path: str
    source_sha256: str
    profile_path: str
    profile_id: str
    observations: list[dict[str, Any]]
    medians: dict[str, float]


@runtime_checkable
class QualitySurface(Protocol):
    """The quality面 the Data Explorer reads.

    This used to be an undeclared structural dependency: ``DataExplorationService``
    read these three attributes while ``DataDescriptor`` said nothing about them,
    so a loader could silently fail to provide them. A Task may only declare
    ``DataExplorerCapability(quality=True)`` if its descriptor satisfies this.
    """

    quality: list[dict[str, Any]]
    detected_quality: list[dict[str, Any]]
    technical_columns: dict[tuple[str, str], str]


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
class BatchPredictionRuntime(Protocol):
    """Optional ordered preview surface for proposal-pool evaluation."""

    @property
    def supports_batch_prediction(self) -> bool: ...

    def predict_batch(
        self,
        candidates: Sequence[Any],
        **kwargs: Any,
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class StageSampleRuntime(Protocol):
    """Optional Chain-only sampling surface; point prediction stays separate."""

    chain_sampling_method: str
    chain_sample_bounds: Mapping[str, tuple[float | None, float | None]]

    def sample_core(
        self,
        candidate: Any,
        *,
        sample_count: int,
        seed: int,
    ) -> StageSampleResult: ...


@runtime_checkable
class SupportProvider(Protocol):
    def evidence(self, candidate: Any) -> tuple[Any, list[dict[str, Any]]]: ...

    def support_summary(self, candidate: Any) -> Any: ...

    def support_by_target(self, candidate: Any) -> dict[str, Any]: ...

    def similarity(
        self, candidate: Any, limit: int = 6, target: str | None = None
    ) -> list[dict[str, Any]]: ...


@runtime_checkable
class TrainingRangeProvider(Protocol):
    """Package-bound marginal training extent for a response-curve axis.

    This is intentionally separate from ``SupportProvider``: an axis min/max is
    not multivariate support, and it must never fall back to TaskDefinition's
    allowed input range.
    """

    def training_range_for(
        self,
        target: str,
        variable: str,
        *,
        stage_name: str | None = None,
        stage_position_m: float | None = None,
    ) -> tuple[float, float]: ...


ResponseCurveHandler = Callable[
    [PredictionRuntime, Candidate, str, str, int, tuple[float, float] | None, str | None, float | None],
    dict[str, Any],
]
CurveFamilyHandler = Callable[[PredictionRuntime, Candidate, str, str | None, int, int], dict[str, Any]]
DataLoader = Callable[[Path, DatasetInputProfile | None], DataDescriptor]
RuntimeFactory = Callable[[DataDescriptor, VerifiedModelPackage], PredictionRuntime]
FeatureRowBuilder = Callable[[dict[str, Any], dict[str, float]], Any]
ModelBuilder = Callable[..., None]
TrainingCandidateBuilder = Callable[
    [dict[str, Any], DataDescriptor],
    CandidateInput | None,
]


@dataclass(frozen=True)
class StandardModelAuthoring:
    """Ordinary fixed-feature estimators supported after Task feature compilation."""

    candidate_builder: TrainingCandidateBuilder
    estimator_ids: tuple[str, ...]
    positive_targets: frozenset[str] = frozenset()


@dataclass(frozen=True)
class StarterProject:
    project_id: str
    name: str
    candidate_factory: Callable[
        [PredictionRuntime, TaskDefinition],
        list[CandidateInput],
    ]
    seed_on_upgrade: bool = False
    legacy_candidate_factory: Callable[
        [PredictionRuntime, TaskDefinition],
        list[CandidateInput],
    ] | None = None


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
    application: ApplicationCapability
    standard_model_authoring: StandardModelAuthoring | None = None
    data_explorer: DataExplorerCapability | None = None
    starter_project: StarterProject | None = None
    response_curve: ResponseCurveHandler | None = None
    curve_family: CurveFamilyHandler | None = None


def _declared_composition_medians(
    medians: dict[str, float],
    task_definition: TaskDefinition,
) -> dict[str, float]:
    composition_group = next(
        group
        for group in task_definition.input_groups
        if group.key == "composition"
    )
    declared = {
        field.path.removeprefix("composition.")
        for field in composition_group.fields
    }
    return {
        key: round(value, 5)
        for key, value in medians.items()
        if key in declared
    }


def _annealed_training_candidate(
    row: dict[str, Any],
    _data: DataDescriptor,
) -> CandidateInput | None:
    from material_workbench.modeling.feature_pipeline import (
        candidate_from_observation,
    )

    return candidate_from_observation(row)


def _hot_rolling_training_candidate(
    row: dict[str, Any],
    _data: DataDescriptor,
) -> CandidateInput | None:
    from material_workbench.modeling.hot_rolling_feature_pipeline import (
        candidate_from_observation,
    )

    return candidate_from_observation(row)


def _tabular_training_candidate(
    row: dict[str, Any],
    data: DataDescriptor,
) -> CandidateInput | None:
    from material_workbench.modeling.tabular_regression import (
        candidate_from_observation,
    )

    return candidate_from_observation(row, data.profile)  # type: ignore[attr-defined]


def _annealed_starter_candidates(
    runtime: PredictionRuntime,
    task_definition: TaskDefinition,
) -> list[CandidateInput]:
    from material_workbench.modeling.feature_pipeline import (
        candidate_from_observation,
    )

    declared_composition = set(
        _declared_composition_medians(runtime.data.medians, task_definition)
    )
    by_parent: dict[str, dict[str, Any]] = {}
    for row in runtime.data.observations:
        if row.get("eligible") and candidate_from_observation(row) is not None:
            by_parent.setdefault(str(row["parent_key"]), row)
    comparable = sorted(
        (
            row
            for row in by_parent.values()
            if isinstance(row.get("outputs", {}).get("TS[MPa]"), (int, float))
        ),
        key=lambda row: float(row["outputs"]["TS[MPa]"]),
    )
    if len(comparable) < 3:
        raise ValueError("焼鈍条件の初期候補には、引張強さを持つ独立条件が3件以上必要です")
    selected = (
        comparable[(len(comparable) - 1) // 2],
        comparable[-1],
        comparable[0],
    )
    labels = ("基準候補", "高強度案", "延性重視案")
    return [
        candidate.model_copy(
            update={
                "name": label,
                "inputs": candidate.inputs.model_copy(
                    update={
                        "composition": {
                            key: value
                            for key, value in candidate.inputs.composition.items()
                            if key in declared_composition
                        }
                    }
                ),
            }
        )
        for row, label in zip(selected, labels, strict=True)
        if (candidate := candidate_from_observation(row)) is not None
    ]


def _legacy_annealed_starter_candidates(
    runtime: PredictionRuntime,
    task_definition: TaskDefinition,
) -> list[CandidateInput]:
    """Exact pre-supported-starter payloads, used only to identify untouched demo data."""

    composition = _declared_composition_medians(
        runtime.data.medians, task_definition
    )
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
                "composition": {
                    **composition,
                    "C": round(composition["C"] * carbon_factor, 5),
                },
                "process": {"ls_mpm": line_speed},
                "categorical": {},
                "heat_pattern": [
                    {
                        "time_s": round(
                            time_s * reference_line_speed / line_speed, 6
                        ),
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


def _observation_loader(task_id: str) -> DataLoader:
    def load(path: Path, profile: DatasetInputProfile | None = None) -> DataDescriptor:
        from material_workbench.data.observation_profile import ObservationDatasetProfile
        from material_workbench.modeling.observation_regression import load_observation_data

        selected = profile if isinstance(profile, ObservationDatasetProfile) else None
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

    selected = (
        profile
        if isinstance(profile, StageBWorkbookProfile)
        else load_stage_b_profile(_WELDING_STAGE_B_PROFILE)
    )
    return build_stage_b_training_data(
        path,
        selected,
        profile_locator=(
            f"catalog:{selected.id}"
            if isinstance(profile, StageBWorkbookProfile)
            else _WELDING_STAGE_B_PROFILE
        ),
    ).data


def _annealed_runtime(data: DataDescriptor, package: VerifiedModelPackage) -> PredictionRuntime:
    from material_workbench.modeling.runtime import ModelRuntime

    return ModelRuntime(data, package_root=package)  # type: ignore[arg-type]


def _hot_rolling_runtime(data: DataDescriptor, package: VerifiedModelPackage) -> PredictionRuntime:
    from material_workbench.modeling.hot_rolling import HotRollingRuntime

    return HotRollingRuntime(data, package_root=package)  # type: ignore[arg-type]


def _flank_wear_runtime(data: DataDescriptor, package: VerifiedModelPackage) -> PredictionRuntime:
    from material_workbench.modeling.flank_wear import FlankWearRuntime

    return FlankWearRuntime(data, package_root=package)  # type: ignore[arg-type]


def _tabular_runtime(data: DataDescriptor, package: VerifiedModelPackage) -> PredictionRuntime:
    from material_workbench.modeling.tabular_regression import TabularRegressionRuntime

    return TabularRegressionRuntime(data, package)  # type: ignore[arg-type]


def _observation_runtime(
    data: DataDescriptor,
    package: VerifiedModelPackage,
) -> PredictionRuntime:
    from material_workbench.modeling.observation_regression import ObservationRegressionRuntime

    return ObservationRegressionRuntime(data, package)  # type: ignore[arg-type]


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


def _welding_stage_b_features(
    row: dict[str, Any], medians: dict[str, float]
) -> Any:
    from material_workbench.data.stage_b_training import (
        load_stage_b_profile,
        stage_b_runtime_profile,
    )
    from material_workbench.modeling.tabular_regression import (
        build_tabular_features_from_observation,
    )

    profile = stage_b_runtime_profile(
        load_stage_b_profile(_WELDING_STAGE_B_PROFILE)
    )
    return build_tabular_features_from_observation(row, medians, profile)


def _build_annealed(
    source: Path,
    output: Path,
    *,
    replace: bool,
    package_id: str,
    package_version: str,
) -> None:
    from build_default_model_package import build

    build(
        source,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
    )


def _build_hot_rolling(
    source: Path,
    output: Path,
    *,
    replace: bool,
    package_id: str,
    package_version: str,
) -> None:
    from build_hot_rolling_model_package import build

    build(
        source,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
    )


def _build_flank_wear(
    source: Path,
    output: Path,
    *,
    replace: bool,
    package_id: str,
    package_version: str,
) -> None:
    from build_flank_wear_model_package import build

    build(
        source,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
    )


def _tabular_builder(task_id: str) -> ModelBuilder:
    def build(
        source: Path,
        output: Path,
        *,
        replace: bool,
        package_id: str,
        package_version: str,
    ) -> None:
        from material_workbench.modeling.tabular_model_builder import build as build_package

        build_package(
            source,
            _TABULAR_PROFILES[task_id],
            output,
            replace=replace,
            package_id=package_id,
            package_version=package_version,
        )
    return build


def _observation_builder(task_id: str) -> ModelBuilder:
    def build(
        source: Path,
        output: Path,
        *,
        replace: bool,
        package_id: str,
        package_version: str,
    ) -> None:
        from material_workbench.modeling.observation_model_builder import build as build_package

        build_package(
            source,
            output,
            declaration=observation_declaration(task_id),
            replace=replace,
            package_id=package_id,
            package_version=package_version,
        )
    return build


def _build_welding_stage_b(
    source: Path,
    output: Path,
    *,
    replace: bool,
    package_id: str,
    package_version: str,
) -> None:
    from build_welding_stage_b_assets import build_package

    build_package(
        source,
        _WELDING_STAGE_B_PROFILE,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
    )


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
    def candidates(
        runtime: PredictionRuntime,
        _task_definition: TaskDefinition,
    ) -> list[CandidateInput]:
        data = runtime.data
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
            indexes = (
                (len(comparable) // 4, len(comparable) // 2, len(comparable) - 1)
                if data.profile.model_family == "lightgbm_binary"
                else (len(comparable) // 4, len(comparable) // 2, len(comparable) * 3 // 4)
            )
            selected = [comparable[index] for index in indexes]
        return [
            candidate_from_observation(row, data.profile).model_copy(
                update={"name": label}
            )
            for row, label in zip(selected, ("低位条件", "代表条件", "高位条件"), strict=True)
        ]
    return StarterProject(f"{task_id}-default", name, candidates, seed_on_upgrade=True)


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
    from material_workbench.modeling.tabular_regression import (
        candidate_from_observation,
    )

    data = runtime.data
    rows = [row for row in data.observations if row["eligible"]]
    selected = [rows[len(rows) // 4], rows[len(rows) // 2], rows[len(rows) * 3 // 4]]
    return [
        candidate_from_observation(row, data.profile).model_copy(
            update={"name": label}
        )
        for row, label in zip(
            selected, ("低位施工", "代表施工", "高位施工"), strict=True
        )
    ]


_EXPLORER = DataExplorerCapability(quality=True, lineage=True, candidate_creation=True)
_TABULAR_EXPLORER = DataExplorerCapability(quality=True, lineage=False, candidate_creation=False)


def _application_capability(
    *,
    actual_measurement: bool,
    response_curve: bool,
    similarity: bool,
    curve_family: bool = False,
    contour_axes: tuple[str, ...] = (),
    candidate_excel_import: bool = False,
    candidate_excel_export: bool = False,
    sparse_blend_transform_id: str | None = None,
    project_creation: bool = True,
) -> ApplicationCapability:
    surfaces: list[
        BasicWorkbenchSurfaceDefinition | ResponseContourSurfaceDefinition
    ] = []

    def basic(kind: str) -> None:
        surfaces.append(
            BasicWorkbenchSurfaceDefinition(
                kind=kind,  # type: ignore[arg-type]
                order=len(surfaces) * 10,
            )
        )

    if sparse_blend_transform_id is not None:
        basic("blend_tools")
    if actual_measurement:
        basic("actual_measurement")
    if curve_family:
        basic("curve_family")
    if response_curve:
        basic("response_curve")
    if contour_axes:
        surfaces.append(
            ResponseContourSurfaceDefinition(
                kind="response_contour",
                order=len(surfaces) * 10,
                axis_paths=contour_axes,
            )
        )
    if similarity:
        basic("similarity")
    basic("feature_engineering")
    return ApplicationCapability(
        workbench_surfaces=tuple(surfaces),
        project_creation=project_creation,
        candidate_excel_import=candidate_excel_import,
        candidate_excel_export=candidate_excel_export,
        sparse_blend=sparse_blend_transform_id is not None,
        sparse_blend_transform_id=sparse_blend_transform_id,
    )


TASK_MODULES: Mapping[str, TaskModule] = MappingProxyType({
    WELDING_STAGE_B_TASK_ID: TaskModule(
        task_id=WELDING_STAGE_B_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_WELDING_STAGE_B_MODEL_PACKAGE",
        source_env="WORKBENCH_WELDING_STAGE_B_SOURCE_PATH",
        source_kind="welding_multistage",
        default_source=Path(
            "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
        ),
        data_loader=_load_welding_stage_b,
        runtime_factory=_tabular_runtime,
        feature_row_builder=_welding_stage_b_features,
        model_builder=_build_welding_stage_b,
        application=_application_capability(
            actual_measurement=True,
            response_curve=False,
            similarity=True,
            sparse_blend_transform_id="welding-stage-a-v1",
        ),
        starter_project=StarterProject(
            "welding-stage-b-default",
            "溶接材料 Stage B",
            _welding_stage_b_starter,
            seed_on_upgrade=True,
        ),
    ),
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
        standard_model_authoring=StandardModelAuthoring(
            _annealed_training_candidate,
            ("exact-gp-rbf.v1",),
            frozenset({"lambda"}),
        ),
        application=_application_capability(
            actual_measurement=True,
            response_curve=True,
            similarity=True,
            contour_axes=("composition.C", "composition.Mn", "process.ls_mpm"),
            candidate_excel_import=True,
            candidate_excel_export=True,
        ),
        data_explorer=_EXPLORER,
        starter_project=StarterProject(
            "default",
            "焼鈍条件の候補検討",
            _annealed_starter_candidates,
            seed_on_upgrade=True,
            legacy_candidate_factory=_legacy_annealed_starter_candidates,
        ),
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
        ),
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
        application=_application_capability(
            actual_measurement=True,
            response_curve=True,
            similarity=True,
            curve_family=True,
        ),
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
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1",),
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=True,
            similarity=True,
            contour_axes=(
                "process.tempering_temp_c",
                "process.cooling_rate_c_per_s",
                "composition.carbon_pct",
            ),
        ),
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
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1",),
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
        ),
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
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1",),
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
        ),
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
        default_source=Path("data/source/external/battery_calce_cs2_cycles.csv"),
        data_loader=_tabular_loader(BATTERY_DEGRADATION_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(BATTERY_DEGRADATION_TASK_ID),
        model_builder=_tabular_builder(BATTERY_DEGRADATION_TASK_ID),
        application=_application_capability(
            actual_measurement=True,
            response_curve=True,
            similarity=True,
            curve_family=True,
            contour_axes=("process.cycle_index", "process.discharge_rate_c"),
        ),
        starter_project=_tabular_starter(BATTERY_DEGRADATION_TASK_ID, "電池容量劣化"),
        response_curve=_standard_response_curve,
        curve_family=_curve_family,
        data_explorer=_TABULAR_EXPLORER,
    ),
    SECOM_YIELD_TASK_ID: TaskModule(
        task_id=SECOM_YIELD_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_SECOM_YIELD_MODEL_PACKAGE",
        source_env="WORKBENCH_SECOM_YIELD_SOURCE_PATH",
        source_kind="external_secom",
        default_source=Path("data/source/external/secom_stress.csv"),
        data_loader=_tabular_loader(SECOM_YIELD_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(SECOM_YIELD_TASK_ID),
        model_builder=_tabular_builder(SECOM_YIELD_TASK_ID),
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
        ),
        starter_project=_tabular_starter(SECOM_YIELD_TASK_ID, "SECOM工程異常リスク"),
        response_curve=_standard_response_curve,
        data_explorer=_TABULAR_EXPLORER,
    ),
    MPEA_LEGACY_TYS_TASK_ID: TaskModule(
        task_id=MPEA_LEGACY_TYS_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_MPEA_LEGACY_TYS_MODEL_PACKAGE",
        source_env="WORKBENCH_MPEA_LITERATURE_SOURCE_PATH",
        source_kind="external_mpea_literature",
        default_source=Path("data/source/external/mpea_ground_truth_18021833.csv"),
        data_loader=_tabular_loader(MPEA_LEGACY_TYS_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(MPEA_LEGACY_TYS_TASK_ID),
        model_builder=_tabular_builder(MPEA_LEGACY_TYS_TASK_ID),
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1",),
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=False,
            similarity=True,
            project_creation=False,
        ),
    ),
    MPEA_ROOM_TENSILE_TASK_ID: TaskModule(
        task_id=MPEA_ROOM_TENSILE_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_MPEA_ROOM_TENSILE_MODEL_PACKAGE",
        source_env="WORKBENCH_MPEA_LITERATURE_SOURCE_PATH",
        source_kind="external_mpea_literature",
        default_source=Path("data/source/external/mpea_ground_truth_18021833.csv"),
        data_loader=_tabular_loader(MPEA_ROOM_TENSILE_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(MPEA_ROOM_TENSILE_TASK_ID),
        model_builder=_tabular_builder(MPEA_ROOM_TENSILE_TASK_ID),
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1",),
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=False,
            similarity=True,
        ),
        starter_project=_tabular_starter(MPEA_ROOM_TENSILE_TASK_ID, "MPEA文献の室温引張特性"),
        data_explorer=_TABULAR_EXPLORER,
    ),
    MPEA_HARDNESS_TASK_ID: TaskModule(
        task_id=MPEA_HARDNESS_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_MPEA_HARDNESS_MODEL_PACKAGE",
        source_env="WORKBENCH_MPEA_LITERATURE_SOURCE_PATH",
        source_kind="external_mpea_literature",
        default_source=Path("data/source/external/mpea_ground_truth_18021833.csv"),
        data_loader=_tabular_loader(MPEA_HARDNESS_TASK_ID),
        runtime_factory=_tabular_runtime,
        feature_row_builder=_tabular_features(MPEA_HARDNESS_TASK_ID),
        model_builder=_tabular_builder(MPEA_HARDNESS_TASK_ID),
        standard_model_authoring=StandardModelAuthoring(
            _tabular_training_candidate,
            ("ridge.v1",),
        ),
        application=_application_capability(
            actual_measurement=False,
            response_curve=False,
            similarity=True,
        ),
        starter_project=_tabular_starter(MPEA_HARDNESS_TASK_ID, "MPEA文献の硬さ"),
        data_explorer=_TABULAR_EXPLORER,
    ),
    WELDING_STAGE_C_TASK_ID: TaskModule(
        task_id=WELDING_STAGE_C_TASK_ID,
        package_override_env="MATERIAL_WORKBENCH_WELDING_STAGE_C_MODEL_PACKAGE",
        source_env="WORKBENCH_WELDING_STAGE_C_SOURCE_PATH",
        source_kind="welding_stage_c",
        default_source=Path("data/source/welding_consumable_multistage_synthetic_dataset.xlsx"),
        data_loader=_observation_loader(WELDING_STAGE_C_TASK_ID),
        runtime_factory=_observation_runtime,
        feature_row_builder=_observation_features(WELDING_STAGE_C_TASK_ID),
        model_builder=_observation_builder(WELDING_STAGE_C_TASK_ID),
        application=_application_capability(
            actual_measurement=False,
            response_curve=True,
            similarity=True,
        ),
        starter_project=StarterProject(
            "welding-stage-c-default",
            "溶着金属成分から特性を予測",
            _welding_stage_c_starter,
        ),
        response_curve=_standard_response_curve,
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
