from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

import numpy as np

from .schemas import CandidateInput, HeatPoint


COMPOSITION_NAMES = (
    "C", "Si", "Mn", "P", "S", "Cr", "Mo", "Ni", "Al", "Ti", "B", "N", "O", "Ca",
)
CANONICAL_INPUT_PATHS = (
    *(f"composition.{name}" for name in COMPOSITION_NAMES),
    "process.thickness_mm",
    "process.line_speed_m_min",
    "heat_pattern",
    "categorical.coating",
)
FEATURE_PIPELINE_ID = "metallurgy-thermal"
FEATURE_PIPELINE_VERSION = "1.4.0"


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    unit: str
    meaning: str


FEATURE_DEFINITIONS = (
    *(FeatureDefinition(name, "mass%", f"{name} composition") for name in COMPOSITION_NAMES),
    FeatureDefinition("thickness_mm", "mm", "Product thickness"),
    FeatureDefinition("line_speed_m_min", "m/min", "Process line speed"),
    FeatureDefinition("coating_none", "1", "Uncoated indicator"),
    FeatureDefinition("coating_GI", "1", "GI coating indicator"),
    FeatureDefinition("coating_GA", "1", "GA coating indicator"),
    FeatureDefinition("ce_iiw", "mass%", "IIW carbon-equivalent proxy"),
    FeatureDefinition("pcm", "mass%", "Ito-Bessyo weld-cracking composition parameter"),
    FeatureDefinition("c_times_mn", "mass%^2", "Carbon-manganese interaction proxy"),
    FeatureDefinition("si_plus_al", "mass%", "Combined silicon and aluminium content"),
    FeatureDefinition("cr_plus_mo", "mass%", "Combined chromium and molybdenum content"),
    FeatureDefinition("microalloy_sum", "mass%", "Available microalloying content, Ti + B"),
    FeatureDefinition("peak_temperature_c", "degC", "Peak temperature of the entered route"),
    FeatureDefinition("max_heating_rate_c_s", "degC/s", "Maximum positive segment heating rate"),
    FeatureDefinition("time_at_or_above_95pct_peak_s", "s", "Interpolated time at or above 95% of peak"),
    FeatureDefinition("time_at_or_above_700c_s", "s", "Interpolated time at or above 700 degC"),
    FeatureDefinition("thermal_exposure_above_600c_c_s", "degC*s", "Integral of max(T - 600 degC, 0)"),
    FeatureDefinition("cooling_rate_800_to_500_c_s", "degC/s", "Equivalent cooling rate between downward 800 and 500 degC crossings after peak"),
    FeatureDefinition("cooling_800_to_500_observed", "1", "One when both downward 800 and 500 degC crossings are observed; zero marks the rate as unavailable"),
    FeatureDefinition("reheat_count", "count", "Cooling-to-heating excursions with at least 25 degC amplitude"),
    FeatureDefinition("has_reheat", "1", "One when reheat_count is positive"),
)

FEATURE_NAMES = tuple(definition.name for definition in FEATURE_DEFINITIONS)
FEATURE_UNITS = tuple(definition.unit for definition in FEATURE_DEFINITIONS)


@dataclass(frozen=True)
class FeatureBundle:
    pipeline_id: str
    pipeline_version: str
    names: tuple[str, ...]
    values: np.ndarray
    units: tuple[str, ...]

    def as_dict(self) -> dict[str, float]:
        return {name: float(value) for name, value in zip(self.names, self.values, strict=True)}


def _composition(
    candidate: CandidateInput,
    defaults: Mapping[str, float] | None,
) -> dict[str, float]:
    unknown = sorted(set(candidate.composition) - set(COMPOSITION_NAMES))
    if unknown:
        raise ValueError(f"Unsupported composition elements: {', '.join(unknown)}")

    values: dict[str, float] = {}
    missing: list[str] = []
    for name in COMPOSITION_NAMES:
        raw = candidate.composition.get(name)
        if raw is None and defaults is not None:
            raw = defaults.get(name)
        if raw is None:
            missing.append(name)
            continue
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"Composition {name} must be a finite, non-negative mass percentage")
        values[name] = value
    if missing:
        raise ValueError(f"Missing composition values and no defaults supplied: {', '.join(missing)}")
    return values


def _segment_duration_above(t0: float, y0: float, t1: float, y1: float, threshold: float) -> float:
    duration = t1 - t0
    if y0 >= threshold and y1 >= threshold:
        return duration
    if y0 < threshold and y1 < threshold:
        return 0.0
    fraction = (threshold - y0) / (y1 - y0)
    return duration * (1.0 - fraction if y1 > y0 else fraction)


def _segment_excess_integral(t0: float, y0: float, t1: float, y1: float, threshold: float) -> float:
    duration = t1 - t0
    e0, e1 = y0 - threshold, y1 - threshold
    if e0 <= 0 and e1 <= 0:
        return 0.0
    if e0 >= 0 and e1 >= 0:
        return duration * (e0 + e1) / 2.0
    fraction = -e0 / (e1 - e0)
    if e0 < 0:
        return duration * (1.0 - fraction) * e1 / 2.0
    return duration * fraction * e0 / 2.0


def _time_above(points: Sequence[HeatPoint], threshold: float) -> float:
    return sum(
        _segment_duration_above(left.time_s, left.temperature_c, right.time_s, right.temperature_c, threshold)
        for left, right in zip(points, points[1:]) if not right.segment_start
    )


def _excess_integral(points: Sequence[HeatPoint], threshold: float) -> float:
    return sum(
        _segment_excess_integral(left.time_s, left.temperature_c, right.time_s, right.temperature_c, threshold)
        for left, right in zip(points, points[1:]) if not right.segment_start
    )


def _downward_crossing_time(points: Sequence[HeatPoint], threshold: float, start_index: int, start_time: float = -math.inf) -> float | None:
    for index in range(start_index, len(points) - 1):
        left, right = points[index], points[index + 1]
        if right.segment_start:
            continue
        if left.temperature_c >= threshold > right.temperature_c:
            fraction = (left.temperature_c - threshold) / (left.temperature_c - right.temperature_c)
            crossing = left.time_s + fraction * (right.time_s - left.time_s)
            if crossing >= start_time:
                return float(crossing)
    return None


def _stages(points: Sequence[HeatPoint]) -> list[list[HeatPoint]]:
    stages: list[list[HeatPoint]] = []
    for point in points:
        if point.segment_start or not stages:
            stages.append([point])
        else:
            stages[-1].append(point)
    return stages


def _cooling_rate_800_to_500(points: Sequence[HeatPoint], peak_index: int) -> tuple[float, float]:
    peak_point = points[peak_index]
    reached_peak_stage = False
    for stage in _stages(points):
        if peak_point in stage:
            reached_peak_stage = True
            local_start = stage.index(peak_point)
        elif reached_peak_stage:
            local_start = 0
        else:
            continue
        t800 = _downward_crossing_time(stage, 800.0, local_start)
        if t800 is None:
            continue
        t500 = _downward_crossing_time(stage, 500.0, local_start, t800)
        if t500 is not None and t500 > t800:
            return 300.0 / (t500 - t800), 1.0
    return 0.0, 0.0


def _reheat_count(points: Sequence[HeatPoint], minimum_excursion_c: float = 25.0) -> int:
    count = 0
    for stage in _stages(points):
        descending = False
        valley: float | None = None
        rise_peak: float | None = None
        for left, right in zip(stage, stage[1:]):
            delta = right.temperature_c - left.temperature_c
            if delta < 0:
                if valley is not None and rise_peak is not None and rise_peak - valley >= minimum_excursion_c:
                    count += 1
                descending = True
                valley = None
                rise_peak = None
            elif delta > 0 and descending:
                if valley is None:
                    valley = left.temperature_c
                rise_peak = max(rise_peak if rise_peak is not None else -math.inf, right.temperature_c)
        if valley is not None and rise_peak is not None and rise_peak - valley >= minimum_excursion_c:
            count += 1
    return count


def build_feature_bundle(
    candidate: CandidateInput,
    composition_defaults: Mapping[str, float] | None = None,
) -> FeatureBundle:
    """Build the versioned, fixed-order numerical feature contract.

    The heat route is interpreted as a piecewise-linear temperature-time path.
    Missing composition values must be supplied explicitly through
    ``composition_defaults`` (for example, training-set medians).
    """
    composition = _composition(candidate, composition_defaults)
    points = candidate.heat_pattern
    peak = max(point.temperature_c for point in points)
    peak_index = next(index for index, point in enumerate(points) if point.temperature_c == peak)
    heating_rates = [
        (right.temperature_c - left.temperature_c) / (right.time_s - left.time_s)
        for left, right in zip(points, points[1:]) if not right.segment_start
    ]
    max_heating_rate = max(0.0, *heating_rates)
    reheat_count = _reheat_count(points)
    cooling_rate, cooling_observed = _cooling_rate_800_to_500(points, peak_index)

    c, si, mn = composition["C"], composition["Si"], composition["Mn"]
    cr, mo, ni = composition["Cr"], composition["Mo"], composition["Ni"]
    al, ti, b = composition["Al"], composition["Ti"], composition["B"]
    # V and Cu are not available in this workbook and are explicitly zero in
    # the standard IIW and Pcm equations rather than silently inferred.
    ce_iiw = c + mn / 6.0 + (cr + mo) / 5.0 + ni / 15.0
    pcm = c + si / 30.0 + (mn + cr) / 20.0 + ni / 60.0 + mo / 15.0 + 5.0 * b

    coating = {
        "なし": (1.0, 0.0, 0.0),
        "GI": (0.0, 1.0, 0.0),
        "GA": (0.0, 0.0, 1.0),
    }[candidate.coating]
    values = np.asarray(
        [
            *(composition[name] for name in COMPOSITION_NAMES),
            candidate.thickness_mm,
            candidate.line_speed_m_min,
            *coating,
            ce_iiw,
            pcm,
            c * mn,
            si + al,
            cr + mo,
            ti + b,
            peak,
            max_heating_rate,
            _time_above(points, peak * 0.95),
            _time_above(points, 700.0),
            _excess_integral(points, 600.0),
            cooling_rate,
            cooling_observed,
            float(reheat_count),
            1.0 if reheat_count else 0.0,
        ],
        dtype=np.float64,
    )
    if values.shape != (len(FEATURE_DEFINITIONS),) or not np.isfinite(values).all():
        raise ValueError("Feature pipeline produced an invalid feature vector")
    values.setflags(write=False)
    return FeatureBundle(FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION, FEATURE_NAMES, values, FEATURE_UNITS)
