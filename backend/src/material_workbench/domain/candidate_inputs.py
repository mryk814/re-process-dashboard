"""Shared candidate-input reading and substitution used by decision activities.

Heat-pattern rescaling is driven by the TaskDefinition, not by a hard-coded
field name: the task declares which numeric input inversely scales heat time
through ``ResponseCurveVariableDefinition.time_transform``.
"""
from __future__ import annotations

from material_workbench.contracts.schemas import Candidate, CandidateInputs
from material_workbench.contracts.task_contracts import (
    CompositionTotalDefinition,
    TaskDefinition,
)
from material_workbench.domain.heat_time import line_speed_scaled_times


class CandidateInputError(ValueError):
    """A candidate does not carry the requested input, or it is not numeric."""


def heat_time_driver_path(definition: TaskDefinition) -> str | None:
    """The numeric input whose change rescales heat-pattern elapsed time."""

    return next(
        (
            item.path
            for item in definition.response_curve_variables
            if item.time_transform == "inverse_heat_time" and item.path
        ),
        None,
    )


def input_value(candidate: Candidate, path: str) -> float:
    group, key = path.split(".", 1)
    values = getattr(candidate.inputs, group, None)
    if not isinstance(values, dict) or key not in values:
        raise CandidateInputError(f"候補に対象の入力がありません: {path}")
    value = values[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CandidateInputError(f"数値入力ではありません: {path}")
    return float(value)


def raw_input_value(candidate: Candidate, path: str) -> float | str | None:
    """Read a numeric or categorical input without requiring it to be present."""

    group, key = path.split(".", 1)
    values = getattr(candidate.inputs, group, None)
    if not isinstance(values, dict):
        return None
    value = values.get(key)
    if isinstance(value, bool):
        return None
    return value if isinstance(value, (int, float, str)) else None


def with_input_values(
    candidate: Candidate,
    values: dict[str, float | str],
    definition: TaskDefinition,
) -> Candidate:
    """Substitute inputs, rescaling line-speed-based heat time when declared."""

    inputs = candidate.inputs.model_copy(deep=True)
    driver_path = heat_time_driver_path(definition)
    previous_driver = (
        raw_input_value(candidate, driver_path) if driver_path is not None else None
    )
    for path, value in values.items():
        group, key = path.split(".", 1)
        mapping = getattr(inputs, group, None)
        if not isinstance(mapping, dict):
            raise CandidateInputError(f"候補に対象の入力グループがありません: {path}")
        mapping[key] = value
    if driver_path is not None and driver_path in values:
        group, key = driver_path.split(".", 1)
        next_driver = getattr(inputs, group).get(key)
        if (
            inputs.heat_time_basis == "line_speed"
            and inputs.heat_pattern
            and isinstance(previous_driver, (int, float))
            and isinstance(next_driver, (int, float))
        ):
            scaled = line_speed_scaled_times(
                inputs.heat_pattern, float(previous_driver), float(next_driver)
            )
            inputs.heat_pattern = [
                point.model_copy(update={"time_s": time_s})
                for point, time_s in zip(inputs.heat_pattern, scaled, strict=True)
            ]
    return candidate.model_copy(update={"inputs": CandidateInputs.model_validate(inputs)})


def with_declared_balance(
    candidate: Candidate,
    values: dict[str, float],
    composition_totals: tuple[CompositionTotalDefinition, ...],
    definition: TaskDefinition,
) -> Candidate:
    """Recompute a declared balance component so the composition total holds."""

    adjusted: dict[str, float | str] = dict(values)
    for constraint in composition_totals:
        balance_path = constraint.balance_path
        varied_components = set(values) & set(constraint.component_paths)
        if not balance_path or not varied_components or balance_path in values:
            continue
        other_total = 0.0
        for path in constraint.component_paths:
            if path == balance_path:
                continue
            candidate_value = adjusted.get(path)
            other_total += (
                float(candidate_value)
                if isinstance(candidate_value, (int, float))
                else input_value(candidate, path)
            )
        adjusted[balance_path] = constraint.total - other_total
    return with_input_values(candidate, adjusted, definition)
