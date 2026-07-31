"""Allow-listed distance strategies for proposal and batch geometry."""
from __future__ import annotations

import math
from typing import Any

from material_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from material_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
)


GENERIC_DISTANCE_ID = "scalar_axis_rms"
COMPOSITION_DISTANCE_ID = "group_weighted_bounded_clr_rms"
COMPOSITION_DISTANCE_VERSION = "1.0.0"
DEFAULT_COMPOSITION_DISTANCE_PARAMETERS: dict[str, float | str | bool] = {
    "zero_replacement": 1e-6,
    "composition_transform": "clr_rms_over_one_plus_clr_rms",
    "composition_weight": 1.0,
    "process_weight": 1.0,
    "categorical_weight": 1.0,
    "heat_weight": 1.0,
}


def _candidate_value(
    candidate: Candidate | CandidateInput,
    path: str,
) -> float | str | None:
    parts = path.split(".")
    if (
        len(parts) == 3
        and parts[0] == "heat_pattern"
        and parts[1].isdigit()
        and parts[2] in {"time_s", "temperature_c"}
    ):
        points = candidate.inputs.heat_pattern or ()
        index = int(parts[1])
        return getattr(points[index], parts[2]) if index < len(points) else None
    if len(parts) != 2:
        return None
    return getattr(candidate.inputs, parts[0], {}).get(parts[1])


def _point_value(
    value: dict[str, Any] | Candidate | CandidateInput,
    path: str,
) -> float | str | None:
    if isinstance(value, (Candidate, CandidateInput)):
        return _candidate_value(value, path)
    if path in value.get("inputs", {}):
        return value["inputs"][path]
    try:
        candidate = CandidateInput.model_validate(value["candidate"])
    except (KeyError, ValueError):
        return None
    return _candidate_value(candidate, path)


def _numeric_axis_distance(
    left: dict[str, Any] | Candidate | CandidateInput,
    right: dict[str, Any] | Candidate | CandidateInput,
    domain: Any,
) -> float | None:
    left_value = _point_value(left, domain.path)
    right_value = _point_value(right, domain.path)
    if left_value is None or right_value is None:
        return None
    if domain.range is not None:
        span = domain.range.max - domain.range.min
    else:
        span = max(domain.values) - min(domain.values)
    return abs(float(left_value) - float(right_value)) / max(float(span), 1e-12)


def scalar_axis_rms_distance(
    left: dict[str, Any] | Candidate | CandidateInput,
    right: dict[str, Any] | Candidate | CandidateInput,
    design_space: DesignSpaceDefinition,
) -> float:
    """The legacy generic metric: every declared scalar axis has equal weight."""

    components = [
        value
        for domain in (*design_space.numeric_domains, *design_space.heat_pattern_domains)
        if (value := _numeric_axis_distance(left, right, domain)) is not None
    ]
    for domain in design_space.categorical_domains:
        left_value = _point_value(left, domain.path)
        right_value = _point_value(right, domain.path)
        if left_value is not None and right_value is not None:
            components.append(0.0 if left_value == right_value else 1.0)
    if not components:
        return 0.0
    return math.sqrt(sum(value * value for value in components) / len(components))


def _bounded_clr_rms_distance(
    left_values: list[float],
    right_values: list[float],
    *,
    zero_replacement: float,
) -> float:
    left_positive = [max(float(value), zero_replacement) for value in left_values]
    right_positive = [max(float(value), zero_replacement) for value in right_values]
    left_total = sum(left_positive)
    right_total = sum(right_positive)
    left_log = [math.log(value / left_total) for value in left_positive]
    right_log = [math.log(value / right_total) for value in right_positive]
    left_mean = sum(left_log) / len(left_log)
    right_mean = sum(right_log) / len(right_log)
    raw = math.sqrt(
        sum(
            ((left - left_mean) - (right - right_mean)) ** 2
            for left, right in zip(left_log, right_log, strict=True)
        )
        / len(left_log)
    )
    # Keep the public threshold scale bounded while preserving CLR-RMS ordering.
    return raw / (1.0 + raw)


def group_weighted_bounded_clr_rms_distance(
    left: dict[str, Any] | Candidate | CandidateInput,
    right: dict[str, Any] | Candidate | CandidateInput,
    design_space: DesignSpaceDefinition,
    *,
    zero_replacement: float = 1e-6,
    composition_weight: float = 1.0,
    process_weight: float = 1.0,
    categorical_weight: float = 1.0,
    heat_weight: float = 1.0,
) -> float:
    """Group-balanced metric with simplex-aware composition geometry.

    Composition is represented once as a closed log-ratio group rather than as
    many independently weighted scalar axes. Other groups retain bounded
    Design-Space normalization.
    """

    grouped: list[tuple[float, float]] = []
    constrained_composition_paths: set[str] = set()
    for constraint in design_space.composition_constraints:
        left_values = [_point_value(left, path) for path in constraint.component_paths]
        right_values = [_point_value(right, path) for path in constraint.component_paths]
        if any(value is None for value in (*left_values, *right_values)):
            continue
        constrained_composition_paths.update(constraint.component_paths)
        grouped.append(
            (
                _bounded_clr_rms_distance(
                    [float(value) for value in left_values if value is not None],
                    [float(value) for value in right_values if value is not None],
                    zero_replacement=zero_replacement,
                ),
                composition_weight,
            )
        )

    if not design_space.composition_constraints:
        declared_composition_paths = tuple(
            domain.path
            for domain in design_space.numeric_domains
            if domain.path.startswith("composition.")
        )
        if len(declared_composition_paths) >= 2:
            left_values = [
                _point_value(left, path) for path in declared_composition_paths
            ]
            right_values = [
                _point_value(right, path) for path in declared_composition_paths
            ]
            if all(value is not None for value in (*left_values, *right_values)):
                constrained_composition_paths.update(declared_composition_paths)
                grouped.append(
                    (
                        _bounded_clr_rms_distance(
                            [float(value) for value in left_values if value is not None],
                            [float(value) for value in right_values if value is not None],
                            zero_replacement=zero_replacement,
                        ),
                        composition_weight,
                    )
                )

    numeric_by_group: dict[str, list[float]] = {
        "composition": [],
        "process": [],
        "heat_pattern": [],
    }
    for domain in (*design_space.numeric_domains, *design_space.heat_pattern_domains):
        if domain.path in constrained_composition_paths:
            continue
        distance = _numeric_axis_distance(left, right, domain)
        if distance is None:
            continue
        group = domain.path.split(".", 1)[0]
        numeric_by_group.setdefault(group, []).append(distance)
    weights = {
        "composition": composition_weight,
        "process": process_weight,
        "heat_pattern": heat_weight,
    }
    for group, values in numeric_by_group.items():
        if values:
            grouped.append(
                (
                    math.sqrt(sum(value * value for value in values) / len(values)),
                    weights.get(group, 1.0),
                )
            )

    categories = []
    for domain in design_space.categorical_domains:
        left_value = _point_value(left, domain.path)
        right_value = _point_value(right, domain.path)
        if left_value is not None and right_value is not None:
            categories.append(0.0 if left_value == right_value else 1.0)
    if categories:
        grouped.append(
            (
                math.sqrt(sum(value * value for value in categories) / len(categories)),
                categorical_weight,
            )
        )
    active = [(value, weight) for value, weight in grouped if weight > 0]
    if not active:
        return 0.0
    return math.sqrt(
        sum(weight * value * value for value, weight in active)
        / sum(weight for _, weight in active)
    )


def proposal_distance(
    distance_id: str,
    left: dict[str, Any] | Candidate | CandidateInput,
    right: dict[str, Any] | Candidate | CandidateInput,
    design_space: DesignSpaceDefinition,
    *,
    distance_version: str = "1.0.0",
    parameters: dict[str, float | str | bool] | None = None,
) -> float:
    if distance_version != COMPOSITION_DISTANCE_VERSION:
        raise ValueError(
            f"未登録のProposal Distanceです: {distance_id}@{distance_version}"
        )
    if distance_id == GENERIC_DISTANCE_ID:
        return scalar_axis_rms_distance(left, right, design_space)
    if distance_id == COMPOSITION_DISTANCE_ID:
        raw = parameters or {}
        transform = raw.get(
            "composition_transform",
            "clr_rms_over_one_plus_clr_rms",
        )
        if transform != "clr_rms_over_one_plus_clr_rms":
            raise ValueError(
                f"未登録のcomposition distance変換です: {transform}"
            )
        return group_weighted_bounded_clr_rms_distance(
            left,
            right,
            design_space,
            zero_replacement=float(raw.get("zero_replacement", 1e-6)),
            composition_weight=float(raw.get("composition_weight", 1.0)),
            process_weight=float(raw.get("process_weight", 1.0)),
            categorical_weight=float(raw.get("categorical_weight", 1.0)),
            heat_weight=float(raw.get("heat_weight", 1.0)),
        )
    raise ValueError(f"未登録のProposal Distanceです: {distance_id}@{distance_version}")
