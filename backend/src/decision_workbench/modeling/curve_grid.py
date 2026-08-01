from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import math

import numpy as np

from decision_workbench.contracts.task_contracts import InputFieldDefinition


_numeric_domain_field: ContextVar[InputFieldDefinition | None] = ContextVar(
    "numeric_domain_field", default=None
)


@contextmanager
def use_numeric_domain(field: InputFieldDefinition | None):
    """Apply Task-owned scalar semantics during one response computation."""

    token = _numeric_domain_field.set(field)
    try:
        yield
    finally:
        _numeric_domain_field.reset(token)


def anchored_curve_grid(
    start: float,
    end: float,
    points: int,
    *,
    current: float | None = None,
) -> list[float]:
    """Build a fixed-size sweep grid that includes the candidate's current value."""

    field = _numeric_domain_field.get()
    if field is not None and field.kind == "number":
        return numeric_domain_grid(start, end, points, field=field, current=current)
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
    if field.numeric_domain_kind == "continuous" and field.search_scale == "linear":
        values = [float(value) for value in np.linspace(start, end, points)]
    elif field.search_scale == "log":
        values = [math.exp(value) for value in np.linspace(math.log(start), math.log(end), points)]
    else:
        values = [float(value) for value in np.linspace(start, end, points)]
    snapped = [_snap(value, field) for value in values]
    if current is not None and math.isfinite(current) and start <= current <= end:
        snapped.append(_snap(current, field))
    unique = sorted({
        round(min(end, max(start, value)), 12)
        for value in snapped
        if start - 1e-9 <= value <= end + 1e-9
    })
    return [float(value) for value in unique]


def _snap(value: float, field: InputFieldDefinition) -> float:
    assert field.allowed_range is not None
    if field.numeric_domain_kind == "integer":
        value = float(round(value))
    elif field.step is not None:
        value = field.allowed_range.min + round(
            (value - field.allowed_range.min) / field.step
        ) * field.step
    return min(field.allowed_range.max, max(field.allowed_range.min, value))
