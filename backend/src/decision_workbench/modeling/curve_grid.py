from __future__ import annotations

import math

import numpy as np


def anchored_curve_grid(
    start: float,
    end: float,
    points: int,
    *,
    current: float | None = None,
) -> list[float]:
    """Build a fixed-size sweep grid that includes the candidate's current value."""

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
