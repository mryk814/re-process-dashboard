from __future__ import annotations

from typing import Any, Literal

from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.task_contracts import (
    ApplicationCapability,
    BasicWorkbenchSurfaceDefinition,
    DataExplorerCapability,
    InputSpaceSurfaceDefinition,
    PredictionSpaceSurfaceDefinition,
    ResponseContourSurfaceDefinition,
    TaskDefinition,
)
from decision_workbench.task_composition.ports import (
    NumericSamplingPolicy,
    PredictionRuntime,
)

EXPLORER = DataExplorerCapability(
    quality=True,
    lineage=True,
    candidate_creation=True,
)
TABULAR_EXPLORER = DataExplorerCapability(
    quality=True,
    lineage=False,
    candidate_creation=False,
)


def _declared_composition_medians(
    medians: dict[str, float],
    task_definition: TaskDefinition,
) -> dict[str, float]:
    composition_group = next(
        group for group in task_definition.input_groups if group.key == "composition"
    )
    declared = {
        field.path.removeprefix("composition.") for field in composition_group.fields
    }
    return {key: round(value, 5) for key, value in medians.items() if key in declared}


def _standard_response_curve(
    runtime: PredictionRuntime,
    candidate: Candidate,
    target: str,
    variable: str,
    points: int,
    axis_range: tuple[float, float] | None,
    _stage_name: str | None,
    _stage_position_m: float | None,
    sampling_policy: NumericSamplingPolicy,
) -> dict[str, Any]:
    return runtime.response_curve_result(
        candidate,
        target,
        variable,
        points,
        axis_range,
        sampling_policy=sampling_policy,
    )


def _curve_family(
    runtime: PredictionRuntime,
    candidate: Candidate,
    target: str,
    vary: str | None,
    levels: int,
    points: int,
    sampling_policy: NumericSamplingPolicy,
) -> dict[str, Any]:
    return runtime.curve_family_result(
        candidate,
        target,
        vary,
        levels,
        points,
        sampling_policy=sampling_policy,
    )


def _application_capability(
    *,
    actual_measurement: bool,
    response_curve: bool,
    similarity: bool,
    prediction_space_targets: tuple[str, ...] = (),
    prediction_space_evidence_context: Literal[
        "training_context", "parent_condition"
    ] = "training_context",
    input_space_target: str | None = None,
    input_space_evidence_context: Literal[
        "training_context", "parent_condition"
    ] = "training_context",
    curve_family: bool = False,
    contour_axes: tuple[str, ...] = (),
    candidate_excel_import: bool = False,
    candidate_excel_export: bool = False,
    sparse_blend_transform_id: str | None = None,
    project_creation: bool = True,
) -> ApplicationCapability:
    surfaces: list[
        BasicWorkbenchSurfaceDefinition
        | InputSpaceSurfaceDefinition
        | PredictionSpaceSurfaceDefinition
        | ResponseContourSurfaceDefinition
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
    if prediction_space_targets:
        surfaces.append(
            PredictionSpaceSurfaceDefinition(
                kind="prediction_space",
                order=len(surfaces) * 10,
                target_keys=prediction_space_targets,
                evidence_context=prediction_space_evidence_context,
            )
        )
    if contour_axes:
        surfaces.append(
            ResponseContourSurfaceDefinition(
                kind="response_contour",
                order=len(surfaces) * 10,
                axis_paths=contour_axes,
            )
        )
    if input_space_target is not None:
        surfaces.append(
            InputSpaceSurfaceDefinition(
                kind="input_space",
                order=len(surfaces) * 10,
                distance_target_key=input_space_target,
                evidence_context=input_space_evidence_context,
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
