"""Platform-neutral serialization boundary for computed floating-point evidence."""
from __future__ import annotations

import math


REPORT_SIGNIFICANT_DIGITS = 13


def canonicalize_report_float(
    value: float,
    *,
    label: str = "computed report value",
) -> float:
    """Return a finite, stable decimal representation for committed evidence.

    Numerical libraries may differ by a few ULPs across operating systems.
    Thirteen significant decimal digits preserve substantially more precision
    than the source measurements while removing that platform-only noise.
    """

    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    canonical = float(format(numeric, f".{REPORT_SIGNIFICANT_DIGITS}g"))
    return 0.0 if canonical == 0.0 else canonical
