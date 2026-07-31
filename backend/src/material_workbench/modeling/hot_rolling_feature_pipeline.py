from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from material_workbench.contracts.feature_contracts import FeatureBundle, FeatureDefinition
from material_workbench.modeling.metallurgy_features import ar3_temperature_proxy, transformation_temperature_proxies
from material_workbench.contracts.candidate_project_contracts import CandidateInput


PIPELINE_ID = "metallurgy-hot-rolling"
PIPELINE_VERSION = "3.0.0"
INPUT_SCHEMA_VERSION = "hot-rolling-candidate-v2"
COMPOSITION_NAMES = (
    "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N",
)
PROCESS_NAMES = (
    "soaking_temperature_c", "finish_temperature_c", "entry_thickness_mm",
    "exit_thickness_mm", "hold_temperature_c", "hold_time_min",
)
CANONICAL_INPUT_PATHS = (
    *(f"composition.{name}" for name in COMPOSITION_NAMES),
    *(f"process.{name}" for name in PROCESS_NAMES),
)
FEATURE_DEFINITIONS = (
    *(FeatureDefinition(name, "%", f"{name} composition", "composition") for name in COMPOSITION_NAMES),
    FeatureDefinition("ce_iiw", "%", "IIW carbon-equivalent proxy", "metallurgy", "炭素当量 CEIIW", "組成指標", "C + Mn/6 + (Cr+Mo)/5 + Ni/15", "規格判定や因果量ではありません。"),
    FeatureDefinition("pcm", "%", "Ito-Bessyo weld-cracking parameter", "metallurgy", "割れ感受性組成 Pcm", "組成指標", "C + Si/30 + (Mn+Cr)/20 + Ni/60 + Mo/15 + 5B + Cu/20"),
    FeatureDefinition("c_times_mn", "%²", "Carbon-manganese interaction", "metallurgy", "C×Mn 相互作用", "組成指標", "C × Mn"),
    FeatureDefinition("si_plus_al", "%", "Silicon plus aluminium", "metallurgy", "Si+Al", "組成指標", "Si + Al"),
    FeatureDefinition("cr_plus_mo", "%", "Chromium plus molybdenum", "metallurgy", "Cr+Mo", "組成指標", "Cr + Mo"),
    FeatureDefinition("ac1_proxy_c", "°C", "Simplified Ac1 transformation-temperature proxy", "metallurgy", "Ac1 目安", "変態点の目安", "723 - 10.7Mn - 16.9Ni + 29.1Si + 16.9Cr", "組成だけから求めた簡易式です。"),
    FeatureDefinition("ac3_proxy_c", "°C", "Simplified Ac3 transformation-temperature proxy", "metallurgy", "Ac3 目安", "変態点の目安", "910 - 203√C - 15.2Ni + 44.7Si + 31.5Mo", "利用可能元素だけの簡易式です。"),
    FeatureDefinition("ms_proxy_c", "°C", "Simplified martensite-start temperature proxy", "metallurgy", "Ms 目安", "変態点の目安", "539 - 423C - 30.4Mn - 17.7Ni - 12.1Cr - 7.5Mo"),
    FeatureDefinition("ti_after_tin_proxy", "%", "Titanium remaining after stoichiometric TiN allocation", "metallurgy", "TiN固定後Tiの目安", "析出・固溶の目安", "max(Ti - 3.42N, 0)", "単純な化学量論仮定です。"),
    *(FeatureDefinition(name, "°C" if name.endswith("temperature_c") else "mm" if name.endswith("thickness_mm") else "min", name, "process") for name in PROCESS_NAMES),
    FeatureDefinition("total_reduction_percent", "%", "Total thickness reduction", "process", "総圧下率", "圧延", "100 × (entry-exit) / entry"),
    FeatureDefinition("log_thickness_strain", "1", "Logarithmic thickness strain", "process", "板厚対数ひずみ", "圧延", "ln(entry / exit)"),
    FeatureDefinition("ar3_proxy_c", "°C", "Simplified composition and thickness Ar3 proxy", "metallurgy", "Ar3 目安", "変態点の目安", "910 - 310C - 80Mn - 20Cu - 15Cr - 55Ni - 80Mo + 0.35(t-8)", "組成と板厚だけの簡易式です。"),
    FeatureDefinition("soak_minus_ac3_c", "°C", "Soaking-temperature margin above Ac3 proxy", "process", "均熱温度−Ac3目安", "変態点×熱延条件", "soaking temperature - Ac3"),
    FeatureDefinition("finish_minus_ar3_c", "°C", "Finish-temperature margin above Ar3 proxy", "process", "仕上温度−Ar3目安", "変態点×熱延条件", "finish temperature - Ar3"),
    FeatureDefinition("hold_minus_ac1_c", "°C", "Hold-temperature margin above Ac1 proxy", "process", "保持温度−Ac1目安", "変態点×保持", "hold temperature - Ac1"),
    FeatureDefinition("hold_exposure_above_ac1_c_min", "°C·min", "Hold exposure above Ac1 proxy", "process", "Ac1超過の保持量", "変態点×保持", "hold time × max(hold temperature - Ac1, 0)", "温度一定とした簡易な保持指標です。"),
    FeatureDefinition("soak_to_finish_drop_c", "°C", "Temperature drop from soaking to finishing", "process", "均熱→仕上の温度差", "熱延温度履歴", "soaking temperature - finish temperature"),
)
FEATURE_NAMES = tuple(item.name for item in FEATURE_DEFINITIONS)
V2_PIPELINE_VERSION = "2.0.0"
V2_FEATURE_NAMES = (
    *COMPOSITION_NAMES,
    "ce_iiw", "pcm", "c_times_mn", "si_plus_al", "cr_plus_mo",
    *PROCESS_NAMES,
)
V2_FEATURE_DEFINITIONS = tuple(
    FeatureDefinition(
        item.name,
        "%^2" if item.name == "c_times_mn" else item.unit,
        item.meaning,
        item.group,
    )
    for item in FEATURE_DEFINITIONS
    if item.name in V2_FEATURE_NAMES
)


def _composition(candidate: CandidateInput, defaults: Mapping[str, float]) -> dict[str, float]:
    unknown = sorted(set(candidate.inputs.composition) - set(COMPOSITION_NAMES))
    if unknown:
        raise ValueError(f"未対応の組成元素です: {', '.join(unknown)}")
    values: dict[str, float] = {}
    for name in COMPOSITION_NAMES:
        raw = candidate.inputs.composition.get(name, defaults.get(name))
        if raw is None or not math.isfinite(float(raw)) or float(raw) < 0 or float(raw) > 100:
            raise ValueError(f"Composition {name} must be finite and non-negative")
        values[name] = float(raw)
    return values


def candidate_from_observation(row: dict[str, Any]) -> CandidateInput | None:
    if row.get("task_id") not in {None, "hot-rolled-properties-v1"}:
        return None
    process, composition = row.get("features"), row.get("composition")
    if not process or not composition or any(process.get(name) is None for name in PROCESS_NAMES):
        return None
    return CandidateInput(
        name=str(row["parent_key"]),
        inputs={
            "composition": composition,
            "process": {name: process[name] for name in PROCESS_NAMES},
            "categorical": {},
            "heat_pattern": None,
        },
    )


def build_hot_rolling_features_from_observation(row: dict[str, Any], composition_defaults: Mapping[str, float]) -> FeatureBundle | None:
    candidate = candidate_from_observation(row)
    return None if candidate is None else build_hot_rolling_features(candidate, composition_defaults)


def build_hot_rolling_features(candidate: CandidateInput, composition_defaults: Mapping[str, float]) -> FeatureBundle:
    composition = _composition(candidate, composition_defaults)
    c, si, mn = composition["C"], composition["Si"], composition["Mn"]
    cr, mo, ni, al, b, cu = (composition[name] for name in ("Cr", "Mo", "Ni", "Al", "B", "Cu"))
    process = {name: float(candidate.inputs.process[name]) for name in PROCESS_NAMES}
    transformations = transformation_temperature_proxies(composition)
    ac1 = transformations["ac1_proxy_c"]
    ac3 = transformations["ac3_proxy_c"]
    ar3 = ar3_temperature_proxy(composition, process["exit_thickness_mm"])
    entry = process["entry_thickness_mm"]
    exit_thickness = process["exit_thickness_mm"]
    values = np.asarray([
        *(composition[name] for name in COMPOSITION_NAMES),
        c + mn / 6.0 + (cr + mo) / 5.0 + ni / 15.0,
        c + si / 30.0 + (mn + cr) / 20.0 + ni / 60.0 + mo / 15.0 + 5.0 * b + cu / 20.0,
        c * mn, si + al, cr + mo,
        ac1, ac3, transformations["ms_proxy_c"], transformations["ti_after_tin_proxy"],
        *(process[name] for name in PROCESS_NAMES),
        100.0 * (entry - exit_thickness) / entry,
        math.log(entry / exit_thickness),
        ar3,
        process["soaking_temperature_c"] - ac3,
        process["finish_temperature_c"] - ar3,
        process["hold_temperature_c"] - ac1,
        process["hold_time_min"] * max(process["hold_temperature_c"] - ac1, 0.0),
        process["soaking_temperature_c"] - process["finish_temperature_c"],
    ], dtype=np.float64)
    if values.shape != (len(FEATURE_DEFINITIONS),) or not np.isfinite(values).all():
        raise ValueError("Hot-rolling feature pipeline produced an invalid vector")
    return FeatureBundle(PIPELINE_ID, PIPELINE_VERSION, FEATURE_DEFINITIONS, values)


def build_hot_rolling_features_v2(
    candidate: CandidateInput,
    composition_defaults: Mapping[str, float],
) -> FeatureBundle:
    current = build_hot_rolling_features(candidate, composition_defaults).as_dict()
    values = np.asarray([current[name] for name in V2_FEATURE_NAMES], dtype=np.float64)
    return FeatureBundle(PIPELINE_ID, V2_PIPELINE_VERSION, V2_FEATURE_DEFINITIONS, values)
