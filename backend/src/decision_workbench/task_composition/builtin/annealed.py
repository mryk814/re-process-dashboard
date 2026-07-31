from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
)
from decision_workbench.contracts.task_contracts import TaskDefinition
from decision_workbench.data.profiles.schema import DatasetInputProfile
from decision_workbench.task_composition.builtin.shared import (
    EXPLORER,
    _application_capability,
    _declared_composition_medians,
)
from decision_workbench.task_composition.builtin.sources import PRIMARY_DEFAULT_SOURCE
from decision_workbench.task_composition.descriptors import (
    StandardModelAuthoring,
    StarterProject,
    TaskModule,
)
from decision_workbench.task_composition.ports import DataDescriptor, PredictionRuntime
from decision_workbench.task_composition.training_inspector import (
    CANONICAL_TRAINING_INSPECTOR,
)

if TYPE_CHECKING:
    from decision_workbench.modeling.packages.verification import VerifiedModelPackage


ANNEALED_TASK_ID = "annealed-properties-v1"


def _annealed_training_candidate(
    row: dict[str, Any],
    _data: DataDescriptor,
) -> CandidateInput | None:
    from decision_workbench.modeling.feature_pipeline import (
        candidate_from_observation,
    )

    return candidate_from_observation(row)


def _annealed_starter_candidates(
    runtime: PredictionRuntime,
    task_definition: TaskDefinition,
) -> list[CandidateInput]:
    from decision_workbench.modeling.feature_pipeline import (
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
        raise ValueError(
            "焼鈍条件の初期候補には、引張強さを持つ独立条件が3件以上必要です"
        )
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

    composition = _declared_composition_medians(runtime.data.medians, task_definition)
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


def _load_workbook(
    path: Path, profile: DatasetInputProfile | None = None
) -> DataDescriptor:
    from decision_workbench.data.importer import load_workbook_data

    if profile is not None and not isinstance(profile, DatasetInputProfile):
        raise ValueError("workbook task requires a workbook Dataset Profile")
    return load_workbook_data(path, profile=profile)


def _annealed_runtime(
    data: DataDescriptor, package: VerifiedModelPackage
) -> PredictionRuntime:
    from decision_workbench.modeling.runtime import ModelRuntime

    return ModelRuntime(data, package_root=package)


def _annealed_features(row: dict[str, Any], medians: dict[str, float]) -> Any:
    from decision_workbench.modeling.feature_pipeline import (
        build_feature_bundle_from_observation,
    )

    return build_feature_bundle_from_observation(row, medians)


def _build_annealed(
    source: Path,
    output: Path,
    *,
    replace: bool,
    package_id: str,
    package_version: str,
    profile_path: Path | None = None,
) -> None:
    from build_default_model_package import build

    from decision_workbench.data.profile_family_registry import load_profile_document

    build(
        source,
        output,
        replace=replace,
        package_id=package_id,
        package_version=package_version,
        profile=load_profile_document(profile_path) if profile_path else None,
    )


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
        raise ValueError(
            "ヒートパターンは工程名温度またはラインスピードで操作してください"
        )
    return runtime.response_curve_result(  # type: ignore[attr-defined]
        candidate, target, variable, points, axis_range, stage_name, stage_position_m
    )


ANNEALED_TASK_MODULE = TaskModule(
    task_id=ANNEALED_TASK_ID,
    package_override_env="DECISION_WORKBENCH_MODEL_PACKAGE",
    source_env="WORKBENCH_SOURCE_PATH",
    source_kind="primary",
    default_source=PRIMARY_DEFAULT_SOURCE,
    data_loader=_load_workbook,
    runtime_factory=_annealed_runtime,
    feature_row_builder=_annealed_features,
    training_inspector=CANONICAL_TRAINING_INSPECTOR,
    specialized_package_builder=_build_annealed,
    standard_model_authoring=StandardModelAuthoring(
        _annealed_training_candidate,
        ("exact-gp-rbf.v1",),
        frozenset({"lambda"}),
    ),
    application=_application_capability(
        actual_measurement=True,
        response_curve=True,
        similarity=True,
        prediction_space_targets=("TS", "YS", "EL", "lambda"),
        input_space_target="TS",
        contour_axes=("composition.C", "composition.Mn", "process.ls_mpm"),
        candidate_excel_import=True,
        candidate_excel_export=True,
    ),
    data_explorer=EXPLORER,
    starter_project=StarterProject(
        "default",
        "焼鈍条件の候補検討",
        _annealed_starter_candidates,
        seed_on_upgrade=True,
        legacy_candidate_factory=_legacy_annealed_starter_candidates,
        distribution="quickstart",
    ),
    response_curve=_annealed_response_curve,
    default_data_projection=True,
)
