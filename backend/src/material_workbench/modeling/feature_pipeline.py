from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

import numpy as np

from material_workbench.contracts.feature_contracts import FeatureBundle, FeatureDefinition
from material_workbench.modeling.metallurgy_features import transformation_temperature_proxies
from material_workbench.contracts.schemas import CandidateInput, HeatPoint


COMPOSITION_NAMES = (
    "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N",
)
CANONICAL_INPUT_PATHS = (
    *(f"composition.{name}" for name in COMPOSITION_NAMES),
    "process.ls_mpm",
    "heat_pattern",
)
FEATURE_PIPELINE_ID = "metallurgy-thermal"
FEATURE_PIPELINE_VERSION = "4.0.0"

_ALL_FEATURE_DEFINITIONS = (
    *(FeatureDefinition(name, "%", f"{name} composition", "composition") for name in COMPOSITION_NAMES),
    FeatureDefinition("ls_mpm", "mpm", "Annealing line speed", "process"),
    FeatureDefinition("ce_iiw", "%", "IIW carbon-equivalent proxy", "metallurgy", "炭素当量 CEIIW", "組成指標", "C + Mn/6 + (Cr+Mo)/5 + Ni/15", "規格判定や因果量ではありません。"),
    FeatureDefinition("pcm", "%", "Ito-Bessyo weld-cracking composition parameter", "metallurgy", "割れ感受性組成 Pcm", "組成指標", "C + Si/30 + (Mn+Cr)/20 + Ni/60 + Mo/15 + 5B + Cu/20", "水素量・拘束・板厚は含みません。"),
    FeatureDefinition("c_times_mn", "%²", "Carbon-manganese interaction proxy", "metallurgy", "C×Mn 相互作用", "組成指標", "C × Mn", "機構式ではなく低次数の相互作用項です。"),
    FeatureDefinition("si_plus_al", "%", "Combined silicon and aluminium content", "metallurgy", "Si+Al", "組成指標", "Si + Al", "有効固溶量や酸化物形態は表しません。"),
    FeatureDefinition("cr_plus_mo", "%", "Combined chromium and molybdenum content", "metallurgy", "Cr+Mo", "組成指標", "Cr + Mo", "両元素が同じ効果という意味ではありません。"),
    FeatureDefinition("microalloy_sum", "%", "Available microalloying content, Ti + B", "metallurgy", "微量添加量 Ti+B", "組成指標", "Ti + B", "Nb・Vを含まない取得範囲内の指標です。"),
    FeatureDefinition("ac1_proxy_c", "°C", "Simplified Ac1 transformation-temperature proxy", "metallurgy", "Ac1 目安", "変態点の目安", "723 - 10.7Mn - 16.9Ni + 29.1Si + 16.9Cr", "組成だけから求めた簡易式です。実測変態点ではありません。"),
    FeatureDefinition("ac3_proxy_c", "°C", "Simplified Ac3 transformation-temperature proxy", "metallurgy", "Ac3 目安", "変態点の目安", "910 - 203√C - 15.2Ni + 44.7Si + 31.5Mo", "利用可能元素だけの簡易式です。"),
    FeatureDefinition("ms_proxy_c", "°C", "Simplified martensite-start temperature proxy", "metallurgy", "Ms 目安", "変態点の目安", "539 - 423C - 30.4Mn - 17.7Ni - 12.1Cr - 7.5Mo", "冷却条件や旧γ粒径を含まない組成指標です。"),
    FeatureDefinition("ti_after_tin_proxy", "%", "Titanium remaining after stoichiometric TiN allocation", "metallurgy", "TiN固定後Tiの目安", "析出・固溶の目安", "max(Ti - 3.42N, 0)", "TiとNが全量TiNになる単純仮定です。"),
    FeatureDefinition("peak_temperature_c", "°C", "Peak temperature of the annealing history", "heat_pattern", "最高温度", "温度履歴", "max(T(t))"),
    FeatureDefinition("max_heating_rate_c_s", "°C/s", "Maximum positive segment heating rate", "heat_pattern", "最大加熱速度", "温度履歴", "max(ΔT/Δt, 0)", "点列の刻み方に敏感です。"),
    FeatureDefinition("time_at_or_above_95pct_peak_s", "s", "Time at or above 95% of peak", "heat_pattern", "最高温度95%以上の時間", "保持", "∫ I[T ≥ 0.95×peak] dt"),
    FeatureDefinition("time_at_or_above_700c_s", "s", "Time at or above 700°C", "heat_pattern", "700℃以上の時間", "保持", "∫ I[T ≥ 700] dt"),
    FeatureDefinition("thermal_exposure_above_600c_c_s", "°C·s", "Thermal exposure above 600°C", "heat_pattern", "600℃超過の熱履歴量", "保持", "∫ max(T-600, 0) dt"),
    FeatureDefinition("cooling_rate_800_to_500_c_s", "°C/s", "Cooling rate between 800°C and 500°C", "heat_pattern", "800→500℃冷却速度", "冷却", "300 / (t500 - t800)", "両温度の通過を観測できた場合だけ計算します。"),
    FeatureDefinition("cooling_800_to_500_observed", "1", "Whether the cooling rate is observed", "heat_pattern", "800→500℃観測あり", "冷却", "0 / 1"),
    FeatureDefinition("reheat_count", "count", "Cooling-to-heating excursions", "heat_pattern", "再加熱回数", "再加熱", "25℃以上の冷却→加熱反転数"),
    FeatureDefinition("has_reheat", "1", "Whether a reheat excursion exists", "heat_pattern", "再加熱あり", "再加熱", "reheat_count ≥ 1"),
    FeatureDefinition("peak_minus_ac1_c", "°C", "Peak-temperature margin above the Ac1 proxy", "heat_pattern", "最高温度−Ac1目安", "変態点×温度履歴", "peak - Ac1"),
    FeatureDefinition("peak_minus_ac3_c", "°C", "Peak-temperature margin above the Ac3 proxy", "heat_pattern", "最高温度−Ac3目安", "変態点×温度履歴", "peak - Ac3"),
    FeatureDefinition("time_above_ac1_s", "s", "Time above the composition-derived Ac1 proxy", "heat_pattern", "Ac1目安以上の時間", "変態点×温度履歴", "∫ I[T ≥ Ac1] dt"),
    FeatureDefinition("time_above_ac3_s", "s", "Time above the composition-derived Ac3 proxy", "heat_pattern", "Ac3目安以上の時間", "変態点×温度履歴", "∫ I[T ≥ Ac3] dt"),
    FeatureDefinition("intercritical_time_s", "s", "Time between the Ac1 and Ac3 proxies", "heat_pattern", "Ac1–Ac3間の時間", "変態点×温度履歴", "time(T≥Ac1) - time(T≥Ac3)", "二相域滞在の厳密な相分率ではありません。"),
    FeatureDefinition("time_within_10c_of_peak_s", "s", "Time within 10°C of the peak temperature", "heat_pattern", "最高温度±10℃の時間", "保持", "∫ I[T ≥ peak-10] dt"),
    FeatureDefinition("thermal_exposure_above_ac3_c_s", "°C·s", "Thermal exposure above the Ac3 proxy", "heat_pattern", "Ac3超過の熱履歴量", "変態点×温度履歴", "∫ max(T-Ac3, 0) dt"),
    FeatureDefinition("cooling_reaches_ms", "1", "Whether post-peak cooling reaches the Ms proxy", "heat_pattern", "冷却がMs目安へ到達", "変態点×冷却", "min(T after peak) ≤ Ms", "実際のマルテンサイト生成を保証しません。"),
)
FEATURE_DEFINITIONS = tuple(
    definition for definition in _ALL_FEATURE_DEFINITIONS
    if definition.name != "ls_mpm"
)
FEATURE_NAMES = tuple(item.name for item in FEATURE_DEFINITIONS)
FEATURE_UNITS = tuple(item.unit for item in FEATURE_DEFINITIONS)
V2_FEATURE_PIPELINE_VERSION = "2.0.0"
V2_FEATURE_NAMES = (
    *COMPOSITION_NAMES,
    "ls_mpm",
    "ce_iiw", "pcm", "c_times_mn", "si_plus_al", "cr_plus_mo", "microalloy_sum",
    "peak_temperature_c", "max_heating_rate_c_s", "time_at_or_above_95pct_peak_s",
    "time_at_or_above_700c_s", "thermal_exposure_above_600c_c_s",
    "cooling_rate_800_to_500_c_s", "cooling_800_to_500_observed", "reheat_count", "has_reheat",
)
V2_FEATURE_DEFINITIONS = tuple(
    FeatureDefinition(
        item.name,
        {
            "c_times_mn": "%^2",
            "thermal_exposure_above_600c_c_s": "°C*s",
        }.get(item.name, item.unit),
        item.meaning,
        item.group,
    )
    for item in _ALL_FEATURE_DEFINITIONS
    if item.name in V2_FEATURE_NAMES
)


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
    if not process or not composition or len(points) < 2:
        return None
    process_inputs = (
        {"ls_mpm": float(process["ls_mpm"])}
        if isinstance(process.get("ls_mpm"), (int, float))
        else {}
    )
    return CandidateInput(
        name=str(row["parent_key"]),
        inputs={
            "composition": composition,
            "process": process_inputs,
            "categorical": {},
            "heat_pattern": points,
            "heat_time_basis": "line_speed" if process_inputs else "elapsed_time",
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
    transformations = transformation_temperature_proxies(composition)
    ac1 = transformations["ac1_proxy_c"]
    ac3 = transformations["ac3_proxy_c"]
    ms = transformations["ms_proxy_c"]
    time_above_ac1 = _time_above(points, ac1)
    time_above_ac3 = _time_above(points, ac3)
    peak_stage = next(stage for stage in _stages(points) if points[peak_index] in stage)
    stage_peak_index = peak_stage.index(points[peak_index])
    post_peak_minimum = min(point.temperature_c for point in peak_stage[stage_peak_index:])
    values = np.asarray([
        *(composition[name] for name in COMPOSITION_NAMES),
        c + mn / 6.0 + (cr + mo) / 5.0 + ni / 15.0,
        c + si / 30.0 + (mn + cr) / 20.0 + ni / 60.0 + mo / 15.0 + 5.0 * b + cu / 20.0,
        c * mn, si + al, cr + mo, ti + b,
        ac1, ac3, ms, transformations["ti_after_tin_proxy"],
        peak, max(0.0, *rates), _time_above(points, peak * 0.95), _time_above(points, 700.0),
        _excess_integral(points, 600.0), cooling_rate, cooling_observed, float(reheat_count), float(bool(reheat_count)),
        peak - ac1, peak - ac3, time_above_ac1, time_above_ac3,
        max(time_above_ac1 - time_above_ac3, 0.0), _time_above(points, peak - 10.0),
        _excess_integral(points, ac3), float(post_peak_minimum <= ms),
    ], dtype=np.float64)
    if values.shape != (len(FEATURE_DEFINITIONS),) or not np.isfinite(values).all():
        raise ValueError("Annealing feature pipeline produced an invalid vector")
    return FeatureBundle(FEATURE_PIPELINE_ID, FEATURE_PIPELINE_VERSION, FEATURE_DEFINITIONS, values)


def build_feature_bundle_v2(
    candidate: CandidateInput,
    composition_defaults: Mapping[str, float] | None = None,
) -> FeatureBundle:
    if not isinstance(candidate.inputs.process.get("ls_mpm"), (int, float)):
        raise ValueError(
            "Feature Pipeline 2.0.0 requires line speed; "
            "use a 4.0.0 model package for elapsed-time heat patterns"
        )
    current = build_feature_bundle(candidate, composition_defaults).as_dict()
    current["ls_mpm"] = float(candidate.inputs.process["ls_mpm"])
    values = np.asarray([current[name] for name in V2_FEATURE_NAMES], dtype=np.float64)
    return FeatureBundle(
        FEATURE_PIPELINE_ID,
        V2_FEATURE_PIPELINE_VERSION,
        V2_FEATURE_DEFINITIONS,
        values,
    )
