from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from .schemas import CandidateInput
from .feature_contracts import FeatureBundle, FeatureDefinition


PIPELINE_ID = "metallurgy-hot-rolling"
PIPELINE_VERSION = "1.2.0"
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


FEATURE_DEFINITIONS = (
    *(FeatureDefinition(name, "mass%", f"{name} composition", "composition") for name in COMPOSITION_NAMES),
    FeatureDefinition("ce_iiw", "mass%", "IIW carbon-equivalent proxy", "metallurgy"),
    FeatureDefinition("pcm", "mass%", "Ito-Bessyo weld-cracking parameter", "metallurgy"),
    FeatureDefinition("c_times_mn", "mass%^2", "Carbon-manganese interaction", "metallurgy"),
    FeatureDefinition("si_plus_al", "mass%", "Silicon plus aluminium", "metallurgy"),
    FeatureDefinition("cr_plus_mo", "mass%", "Chromium plus molybdenum", "metallurgy"),
    FeatureDefinition("reheat_temperature_c", "degC", "Reheat temperature", "process"),
    FeatureDefinition("hold_time_min", "min", "Reheat holding time", "process"),
    FeatureDefinition("finish_temperature_c", "degC", "Finishing temperature", "process"),
    FeatureDefinition("coiling_temperature_c", "degC", "Coiling temperature", "process"),
    FeatureDefinition("cooling_rate_c_s", "degC/s", "Cooling rate", "process"),
    FeatureDefinition("entry_thickness_mm", "mm", "Entry thickness", "process"),
    FeatureDefinition("exit_thickness_mm", "mm", "Exit thickness", "process"),
    FeatureDefinition("route_A", "1", "Route A indicator", "categorical"),
    FeatureDefinition("route_B", "1", "Route B indicator", "categorical"),
    FeatureDefinition("route_C", "1", "Route C indicator", "categorical"),
)
FEATURE_NAMES = tuple(item.name for item in FEATURE_DEFINITIONS)


def candidate_from_observation(row: dict[str, Any]) -> CandidateInput | None:
    process, composition = row["features"], row["composition"]
    if row["source"] != "熱延引張" or not process or not composition:
        return None
    return CandidateInput(
        name=str(row["parent_key"]),
        inputs={
            "composition": composition,
            "process": {
                key: process[key]
                for key in (
                    "reheat_temperature_c", "hold_time_min", "finish_temperature_c",
                    "coiling_temperature_c", "cooling_rate_c_s", "entry_thickness_mm",
                    "exit_thickness_mm",
                )
            },
            "categorical": {"route": process["route"]},
            "heat_pattern": None,
        },
    )


def build_hot_rolling_features_from_observation(
    row: dict[str, Any],
    composition_defaults: Mapping[str, float],
) -> FeatureBundle | None:
    candidate = candidate_from_observation(row)
    return None if candidate is None else build_hot_rolling_features(candidate, composition_defaults)


def build_hot_rolling_features(
    candidate: CandidateInput,
    composition_defaults: Mapping[str, float],
) -> FeatureBundle:
    composition: dict[str, float] = {}
    for name in COMPOSITION_NAMES:
        value = float(candidate.inputs.composition.get(name, composition_defaults[name]))
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
        *(candidate.inputs.process[key] for key in (
            "reheat_temperature_c", "hold_time_min", "finish_temperature_c",
            "coiling_temperature_c", "cooling_rate_c_s", "entry_thickness_mm",
            "exit_thickness_mm",
        )),
        *(1.0 if candidate.inputs.categorical["route"] == route else 0.0 for route in ("A", "B", "C")),
    ], dtype=np.float64)
    if values.shape != (len(FEATURE_NAMES),) or not np.isfinite(values).all():
        raise ValueError("Hot-rolling feature pipeline produced an invalid vector")
    return FeatureBundle(PIPELINE_ID, PIPELINE_VERSION, FEATURE_DEFINITIONS, values)
