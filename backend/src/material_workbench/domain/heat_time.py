from __future__ import annotations

from collections.abc import Sequence

from material_workbench.contracts.candidate_project_contracts import HeatPoint


def line_speed_scaled_times(
    points: Sequence[HeatPoint],
    old_speed_mpm: float,
    new_speed_mpm: float,
) -> tuple[float, ...]:
    """Return line-position-preserving times without mutating the input points."""
    if old_speed_mpm <= 0:
        raise ValueError("基準ラインスピードは0より大きくしてください")
    if new_speed_mpm <= 0:
        raise ValueError("ラインスピードは0より大きくしてください")
    if not points:
        return ()
    origin = points[0].time_s
    scale = old_speed_mpm / new_speed_mpm
    return tuple(origin + (point.time_s - origin) * scale for point in points)
