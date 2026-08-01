from __future__ import annotations

import math

import numpy as np

from decision_workbench.contracts.task_contracts import InputFieldDefinition
from decision_workbench.task_composition.ports import NumericSamplingPolicy


def anchored_curve_grid(
    start: float,
    end: float,
    points: int,
    *,
    current: float | None = None,
    policy: NumericSamplingPolicy | None = None,
) -> list[float]:
    """Build a fixed-size sweep grid that includes the candidate's current value."""

    if policy is not None:
        selected_current = current if policy.include_current else None
        if policy.field is not None and policy.field.kind == "number":
            return numeric_domain_grid(
                start,
                end,
                points,
                field=policy.field,
                current=selected_current,
            )
        return _linear_anchored_grid(
            start,
            end,
            points,
            current=selected_current,
        )
    return _linear_anchored_grid(start, end, points, current=current)


def _linear_anchored_grid(
    start: float,
    end: float,
    points: int,
    *,
    current: float | None,
) -> list[float]:
    values = [float(value) for value in np.linspace(start, end, points)]
    if (
        current is None
        or not math.isfinite(current)
        or not start < current < end
        or points < 3
    ):
        return values

    nearest = min(range(1, points - 1), key=lambda index: abs(values[index] - current))
    values[nearest] = float(current)
    values.sort()
    return values


def numeric_domain_grid(
    start: float,
    end: float,
    points: int,
    *,
    field: InputFieldDefinition,
    current: float | None = None,
) -> list[float]:
    """Sample a Task numeric domain once, snapping and deduplicating values."""

    if not math.isfinite(start) or not math.isfinite(end) or start >= end:
        raise ValueError("response numeric range must be finite and ascending")
    if field.kind != "number" or field.allowed_range is None:
        raise ValueError(f"numeric domain is unavailable for {field.path}")
    if field.numeric_domain_kind in {"integer", "step"}:
        return _discrete_domain_grid(
            start,
            end,
            points,
            field=field,
            current=current,
        )
    if field.numeric_domain_kind == "continuous" and field.search_scale == "linear":
        values = [float(value) for value in np.linspace(start, end, points)]
    elif field.search_scale == "log":
        values = [math.exp(value) for value in np.linspace(math.log(start), math.log(end), points)]
    else:
        values = [float(value) for value in np.linspace(start, end, points)]
    snapped = [_snap(value, field) for value in values]
    if field.numeric_domain_kind == "continuous":
        unique = sorted({
            round(min(end, max(start, value)), 12)
            for value in snapped
            if start - 1e-9 <= value <= end + 1e-9
        })
    else:
        unique = sorted({
            round(value, 12)
            for value in snapped
            if start <= value <= end
        })
    snapped_current = (
        _snap(current, field)
        if current is not None and math.isfinite(current) and start <= current <= end
        else None
    )
    current_value = (
        round(snapped_current, 12)
        if snapped_current is not None
        and (
            field.numeric_domain_kind == "continuous"
            or start <= snapped_current <= end
        )
        else None
    )
    if current_value is not None and current_value not in unique:
        if len(unique) < points:
            unique.append(current_value)
        elif points >= 3:
            nearest = min(
                range(1, len(unique) - 1),
                key=lambda index: abs(unique[index] - current_value),
            )
            unique[nearest] = current_value
        unique.sort()
    return [float(value) for value in unique]


def _discrete_domain_grid(
    start: float,
    end: float,
    points: int,
    *,
    field: InputFieldDefinition,
    current: float | None,
) -> list[float]:
    step = 1.0 if field.numeric_domain_kind == "integer" else field.step
    assert step is not None
    origin = (
        0.0
        if field.numeric_domain_kind == "integer"
        else field.allowed_range.min
    )
    first = math.ceil((start - origin) / step - 1e-12)
    last = math.floor((end - origin) / step + 1e-12)
    available = last - first + 1
    if available <= 0:
        return []

    count = min(points, available)
    if count <= 0:
        return []
    first_value = origin + first * step
    last_value = origin + last * step
    targets = (
        [
            math.exp(value)
            for value in np.linspace(
                math.log(first_value),
                math.log(last_value),
                count,
            )
        ]
        if field.search_scale == "log"
        else [
            float(value)
            for value in np.linspace(first_value, last_value, count)
        ]
    )
    selected: set[int] = set()
    for target in targets:
        selected.add(
            _nearest_unused_lattice_index(
                target,
                selected=selected,
                first=first,
                last=last,
                origin=origin,
                step=step,
                logarithmic=field.search_scale == "log",
            )
        )
    indexes = sorted(selected)
    snapped_current = (
        _snap(current, field)
        if current is not None and math.isfinite(current) and start <= current <= end
        else None
    )
    if snapped_current is not None and start <= snapped_current <= end:
        current_index = int(round((snapped_current - origin) / step))
        if current_index not in indexes:
            candidates = (
                range(1, len(indexes) - 1)
                if len(indexes) >= 3
                else range(len(indexes))
            )
            nearest = min(
                candidates,
                key=lambda index: abs(indexes[index] - current_index),
            )
            indexes[nearest] = current_index
            indexes.sort()
    return [
        float(round(origin + index * step, 12))
        for index in indexes
    ]


def _nearest_unused_lattice_index(
    target: float,
    *,
    selected: set[int],
    first: int,
    last: int,
    origin: float,
    step: float,
    logarithmic: bool,
) -> int:
    center = min(last, max(first, int(round((target - origin) / step))))
    candidates: list[int] = []
    for distance in range(len(selected) + 2):
        for index in (center - distance, center + distance):
            if first <= index <= last and index not in selected:
                candidates.append(index)
        if candidates:
            break
    if not candidates:
        raise ValueError("response discrete domain cannot provide a unique lattice point")

    def distance(index: int) -> float:
        value = origin + index * step
        if logarithmic:
            return abs(math.log(value) - math.log(target))
        return abs(value - target)

    return min(candidates, key=lambda index: (distance(index), index))


def _snap(value: float, field: InputFieldDefinition) -> float:
    assert field.allowed_range is not None
    if field.numeric_domain_kind == "integer":
        value = float(round(value))
    elif field.step is not None:
        value = field.allowed_range.min + round(
            (value - field.allowed_range.min) / field.step
        ) * field.step
    return min(field.allowed_range.max, max(field.allowed_range.min, value))
