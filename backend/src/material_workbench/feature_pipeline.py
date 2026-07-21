from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

from .feature_contracts import FeatureBundle, FeatureDefinition
from .schemas import CandidateInput, HeatPoint


COMPOSITION_NAMES = (
    "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N",
)
CANONICAL_INPUT_PATHS = (
    *(f"composition.{name}" for name in COMPOSITION_NAMES),
    "process.ls_mpm",
    "heat_pattern",
)
FEATURE_PIPELINE_ID = "metallurgy-thermal"
FEATURE_PIPELINE_VERSION = "2.0.0"

FEATURE_DEFINITIONS = (
    *(FeatureDefinition(name, "%", f"{name} composition", "composition") for name in COMPOSITION_NAMES),
    FeatureDefinition("ls_mpm", "mpm", "Annealing line speed", "process"),
    FeatureDefinition("ce_iiw", "%", "IIW carbon-equivalent proxy", "metallurgy"),
    FeatureDefinition("pcm", "%", "Ito-Bessyo weld-cracking composition parameter", "metallurgy"),
    FeatureDefinition("c_times_mn", "%^2", "Carbon-manganese interaction proxy", "metallurgy"),
    FeatureDefinition("si_plus_al", "%", "Combined silicon and aluminium content", "metallurgy"),
    FeatureDefinition("cr_plus_mo", "%", "Combined chromium and molybdenum content", "metallurgy"),
    FeatureDefinition("microalloy_sum", "%", "Available microalloying content, Ti + B", "metallurgy"),
    FeatureDefinition("peak_temperature_c", "°C", "Peak temperature of the annealing history", "heat_pattern"),
    FeatureDefinition("max_heating_rate_c_s", "°C/s", "Maximum positive segment heating rate", "heat_pattern"),
    FeatureDefinition("time_at_or_above_95pct_peak_s", "s", "Time at or above 95% of peak", "heat_pattern"),
    FeatureDefinition("time_at_or_above_700c_s", "s", "Time at or above 700°C", "heat_pattern"),
    FeatureDefinition("thermal_exposure_above_600c_c_s", "°C*s", "Thermal exposure above 600°C", "heat_pattern"),
    FeatureDefinition("cooling_rate_800_to_500_c_s", "°C/s", "Cooling rate between 800°C and 500°C", "heat_pattern"),
    FeatureDefinition("cooling_800_to_500_observed", "1", "Whether the cooling rate is observed", "heat_pattern"),
    FeatureDefinition("reheat_count", "count", "Cooling-to-heating excursions", "heat_pattern"),
    FeatureDefinition("has_reheat", "1", "Whether a reheat excursion exists", "heat_pattern"),
)
FEATURE_NAMES = tuple(item.name for item in FEATURE_DEFINITIONS)
FEATURE_UNITS = tuple(item.unit for item in FEATURE_DEFINITIONS)


def _composition(candidate: CandidateInput, defaults: Mapping[str, float] | None) -> dict[str, float]:
    unknown = sorted(set(candidate.inputs.composition) - set(COMPOSITION_NAMES))
    if unknown:
        raise ValueError(f"未対応の組成元素です: {', '.join(unknown)}")
    values: dict[str, float] = {}
    missing: list[str] = []
    for name in COMPOSITION_NAMES:
        raw = candidate.inputs.composition.get(name)
        if raw is None and defaults is not None:
            raw = defaults.get(name)
        if raw is None:
            missing.append(name)
            continue
        value = float(raw)
        if not math.isfinite(value) or value < 0 or value > 100:
            raise ValueError(f"組成は0〜100の有限値にしてください: {name}")
        values[name] = value
    if missing:
        raise ValueError(f"Missing composition values and no defaults supplied: {', '.join(missing)}")
    return values


def candidate_from_observation(row: dict[str, Any]) -> CandidateInput | None:
    if row.get("task_id") not in {None, "annealed-properties-v1"}:
        return None
    process, composition = row.get("features"), row.get("composition")
    points = (process or {}).get("heat_pattern", [])
    if not process or not composition or process.get("ls_mpm") is None or len(points) < 2:
        return None
    return CandidateInput(
        name=str(row["parent_key"]),
        inputs={
            "composition": composition,
            "process": {"ls_mpm": process["ls_mpm"]},
            "categorical": {},
            "heat_pattern": points,
        },
    )


def build_feature_bundle_from_observation(row: dict[str, Any], composition_defaults: Mapping[str, float]) -> FeatureBundle | None:
    candidate = candidate_from_observation(row)
    return None if candidate is None else build_feature_bundle(candidate, composition_defaults)


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
    return duration * ((1.0 - fraction) * e1 / 2.0 if e0 < 0 else fraction * e0 / 2.0)


def _time_above(points: Sequence[HeatPoint], threshold: float) -> float:
    return sum(_segment_duration_above(a.time_s, a.temperature_c, b.time_s, b.temperature_c, threshold) for a, b in zip(points, points[1:]) if not b.segment_start)


def _excess_integral(points: Sequence[HeatPoint], threshold: float) -> float:
    return sum(_segment_excess_integral(a.time_s, a.temperature_c, b.time_s, b.temperature_c, threshold) for a, b in zip(points, points[1:]) if not b.segment_start)


def _stages(points: Sequence[HeatPoint]) -> list[list[HeatPoint]]:
    stages: list[list[HeatPoint]] = []
    for point in points:
        if point.segment_start or not stages:
            stages.append([point])
        else:
            stages[-1].append(point)
    return stages


def _crossing(points: Sequence[HeatPoint], threshold: float, start: int = 0, after: float = -math.inf) -> float | None:
    for index in range(start, len(points) - 1):
        left, right = points[index], points[index + 1]
        if right.segment_start or not (left.temperature_c >= threshold > right.temperature_c):
            continue
        fraction = (left.temperature_c - threshold) / (left.temperature_c - right.temperature_c)
        result = left.time_s + fraction * (right.time_s - left.time_s)
        if result >= after:
            return result
    return None


def _cooling_rate(points: Sequence[HeatPoint], peak_index: int) -> tuple[float, float]:
    peak = points[peak_index]
    for stage in _stages(points):
        if peak not in stage:
            continue
        start = stage.index(peak)
        t800 = _crossing(stage, 800.0, start)
        t500 = _crossing(stage, 500.0, start, t800 or -math.inf)
        if t800 is not None and t500 is not None and t500 > t800:
            return 300.0 / (t500 - t800), 1.0
    return 0.0, 0.0


def _reheat_count(points: Sequence[HeatPoint]) -> int:
    count = 0
    for stage in _stages(points):
        fell = False
        valley: float | None = None
        rise_peak: float | None = None
        for left, right in zip(stage, stage[1:]):
            delta = right.temperature_c - left.temperature_c
            if delta < 0:
                fell = True
                valley = right.temperature_c if valley is None else min(valley, right.temperature_c)
                rise_peak = None
            elif delta > 0 and fell:
                rise_peak = max(rise_peak or right.temperature_c, right.temperature_c)
                if valley is not None and rise_peak - valley >= 25:
                    count += 1
                    fell, valley, rise_peak = False, None, None
    return count


def build_feature_bundle(candidate: CandidateInput, composition_defaults: Mapping[str, float] | None = None) -> FeatureBundle:
    composition = _composition(candidate, composition_defaults)
    points = candidate.inputs.heat_pattern
    if points is None or len(points) < 2:
        raise ValueError("Annealing feature pipeline requires at least two history points")
    peak = max(point.temperature_c for point in points)
    peak_index = next(index for index, point in enumerate(points) if point.temperature_c == peak)
    rates = [(b.temperature_c - a.temperature_c) / (b.time_s - a.time_s) for a, b in zip(points, points[1:]) if not b.segment_start]
    cooling_rate, cooling_observed = _cooling_rate(points, peak_index)
    reheat_count = _reheat_count(points)
    c, si, mn = composition["C"], composition["Si"], composition["Mn"]
    cr, mo, ni = composition["Cr"], composition["Mo"], composition["Ni"]
    al, ti, b, cu = composition["Al"], composition["Ti"], composition["B"], composition["Cu"]
    values = np.asarray([
        *(composition[name] for name in COMPOSITION_NAMES),
        candidate.inputs.process["ls_mpm"],
        c + mn / 6.0 + (cr + mo) / 5.0 + ni / 15.0,
        c + si / 30.0 + (mn + cr) / 20.0 + ni / 60.0 + mo / 15.0 + 5.0 * b + cu / 20.0,
        c * mn, si + al, cr + mo, ti + b,
        peak, max(0.0, *rates), _time_above(points, peak * 0.95), _time_above(points, 700.0),
        _excess_integral(points, 600.0), cooling_rate, cooling_observed, float(reheat_count), float(bool(reheat_count)),
    ], dtype=np.float64)
    if values.shape != (len(FEATURE_DEFINITIONS),) or not np.isfinite(values).all():
        raise ValueError("Annealing feature pipeline produced an invalid vector")
    return FeatureBundle(FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION, FEATURE_DEFINITIONS, values)
