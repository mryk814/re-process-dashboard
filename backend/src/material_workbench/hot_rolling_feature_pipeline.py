from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from .feature_contracts import FeatureBundle, FeatureDefinition
from .schemas import CandidateInput


PIPELINE_ID = "metallurgy-hot-rolling"
PIPELINE_VERSION = "2.0.0"
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
    FeatureDefinition("ce_iiw", "%", "IIW carbon-equivalent proxy", "metallurgy"),
    FeatureDefinition("pcm", "%", "Ito-Bessyo weld-cracking parameter", "metallurgy"),
    FeatureDefinition("c_times_mn", "%^2", "Carbon-manganese interaction", "metallurgy"),
    FeatureDefinition("si_plus_al", "%", "Silicon plus aluminium", "metallurgy"),
    FeatureDefinition("cr_plus_mo", "%", "Chromium plus molybdenum", "metallurgy"),
    *(FeatureDefinition(name, "°C" if name.endswith("temperature_c") else "mm" if name.endswith("thickness_mm") else "min", name, "process") for name in PROCESS_NAMES),
)
FEATURE_NAMES = tuple(item.name for item in FEATURE_DEFINITIONS)


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
    values = np.asarray([
        *(composition[name] for name in COMPOSITION_NAMES),
        c + mn / 6.0 + (cr + mo) / 5.0 + ni / 15.0,
        c + si / 30.0 + (mn + cr) / 20.0 + ni / 60.0 + mo / 15.0 + 5.0 * b + cu / 20.0,
        c * mn, si + al, cr + mo,
        *(float(candidate.inputs.process[name]) for name in PROCESS_NAMES),
    ], dtype=np.float64)
    if values.shape != (len(FEATURE_DEFINITIONS),) or not np.isfinite(values).all():
        raise ValueError("Hot-rolling feature pipeline produced an invalid vector")
    return FeatureBundle(PIPELINE_ID, PIPELINE_VERSION, FEATURE_DEFINITIONS, values)
