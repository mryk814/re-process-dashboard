from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import numpy as np

from .schemas import HotRollingCandidateInput


PIPELINE_ID = "metallurgy-hot-rolling"
PIPELINE_VERSION = "1.1.0"
INPUT_SCHEMA_VERSION = "hot-rolling-candidate-v1"
COMPOSITION_NAMES = (
    "C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca",
)
CANONICAL_INPUT_PATHS = (
    *(f"composition.{name}" for name in COMPOSITION_NAMES),
    "process.reheat_temperature_c",
    "process.hold_time_min",
    "process.finish_temperature_c",
    "process.coiling_temperature_c",
    "process.cooling_rate_c_s",
    "process.entry_thickness_mm",
    "process.exit_thickness_mm",
    "categorical.route",
)


@dataclass(frozen=True)
class HotRollingFeatureBundle:
    names: tuple[str, ...]
    values: np.ndarray

    def as_dict(self) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.names, self.values, strict=True)}


FEATURE_NAMES = (
    *COMPOSITION_NAMES,
    "ce_iiw", "pcm", "c_times_mn", "si_plus_al", "cr_plus_mo",
    "reheat_temperature_c", "hold_time_min", "finish_temperature_c",
    "coiling_temperature_c", "cooling_rate_c_s", "entry_thickness_mm",
    "exit_thickness_mm",
    "route_A", "route_B", "route_C",
)

COMPOSITION_SLICE = tuple(range(0, 14))
METALLURGY_SLICE = tuple(range(14, 19))
PROCESS_SLICE = tuple(range(19, 26))
CATEGORY_SLICE = tuple(range(26, len(FEATURE_NAMES)))
FEATURE_COMPONENTS = {
    "composition": COMPOSITION_SLICE,
    "metallurgy": METALLURGY_SLICE,
    "process": PROCESS_SLICE,
    "route": CATEGORY_SLICE,
}


def build_hot_rolling_features(
    candidate: HotRollingCandidateInput,
    composition_defaults: Mapping[str, float],
) -> HotRollingFeatureBundle:
    composition: dict[str, float] = {}
    for name in COMPOSITION_NAMES:
        value = float(candidate.composition.get(name, composition_defaults[name]))
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Composition {name} must be finite and non-negative")
        composition[name] = value
    c, si, mn = composition["C"], composition["Si"], composition["Mn"]
    cr, mo, ni, al, b = composition["Cr"], composition["Mo"], composition["Ni"], composition["Al"], composition["B"]
    values = np.asarray([
        *(composition[name] for name in COMPOSITION_NAMES),
        c + mn / 6.0 + (cr + mo) / 5.0 + ni / 15.0,
        c + si / 30.0 + (mn + cr) / 20.0 + ni / 60.0 + mo / 15.0 + 5.0 * b,
        c * mn,
        si + al,
        cr + mo,
        candidate.reheat_temperature_c,
        candidate.hold_time_min,
        candidate.finish_temperature_c,
        candidate.coiling_temperature_c,
        candidate.cooling_rate_c_s,
        candidate.entry_thickness_mm,
        candidate.exit_thickness_mm,
        *(1.0 if candidate.route == route else 0.0 for route in ("A", "B", "C")),
    ], dtype=np.float64)
    if values.shape != (len(FEATURE_NAMES),) or not np.isfinite(values).all():
        raise ValueError("Hot-rolling feature pipeline produced an invalid vector")
    values.setflags(write=False)
    return HotRollingFeatureBundle(FEATURE_NAMES, values)
