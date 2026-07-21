"""Feature pipeline for the cutting-tool flank wear task.

切削距離は設計変数ではなく摩耗曲線の横軸だが、機械学習上は他の入力と同じ
1特徴量として扱う。曲線としての意味付けはruntime側（response curve）が持つ。
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np

from .feature_contracts import FeatureBundle, FeatureDefinition
from .schemas import CandidateInput


PIPELINE_ID = "cutting-flank-wear"
PIPELINE_VERSION = "1.0.0"
INPUT_SCHEMA_VERSION = "flank-wear-candidate-v1"
TASK_ID = "flank-wear-v1"

COMPOSITION_NAMES = ("C", "Si", "Mn", "Cr", "Ni", "Mo", "V", "W", "Co", "Ti", "Al", "Cu")
PROCESS_NAMES = (
    "cutting_distance_m", "cutting_speed_mpm", "feed_mm_rev", "depth_of_cut_mm",
    "hardness_hv", "tensile_mpa",
    "wear_resistance_index", "fracture_resistance_index", "nose_radius_mm", "rake_angle_deg",
)
COATING_CHOICES = ("なし", "CVD", "PVD")
COOLANT_CHOICES = ("ドライ", "エアブロー", "MQL", "外部給油", "高圧クーラント")
CUTTING_MODE_CHOICES = ("連続", "断続")
RIGIDITY_CHOICES = ("低剛性", "標準", "高剛性")
CATEGORICAL_CHOICES = {
    "coating_method": COATING_CHOICES,
    "coolant_method": COOLANT_CHOICES,
    "cutting_mode": CUTTING_MODE_CHOICES,
    "holder_rigidity": RIGIDITY_CHOICES,
}
CANONICAL_INPUT_PATHS = (
    *(f"process.{name}" for name in PROCESS_NAMES),
    *(f"composition.{name}" for name in COMPOSITION_NAMES),
    *(f"categorical.{name}" for name in CATEGORICAL_CHOICES),
)
_COOLANT_FEATURES = ("coolant_dry", "coolant_air_blow", "coolant_mql", "coolant_flood", "coolant_high_pressure")
FEATURE_DEFINITIONS = (
    *(FeatureDefinition(name, "%", f"{name} composition", "composition") for name in COMPOSITION_NAMES),
    FeatureDefinition("hardness_hv", "HV", "workpiece hardness", "metallurgy"),
    FeatureDefinition("tensile_mpa", "MPa", "workpiece tensile strength", "metallurgy"),
    FeatureDefinition("wear_resistance_index", "-", "tool wear resistance index", "other"),
    FeatureDefinition("fracture_resistance_index", "-", "tool fracture resistance index", "other"),
    FeatureDefinition("nose_radius_mm", "mm", "tool nose radius", "other"),
    FeatureDefinition("rake_angle_deg", "deg", "tool rake angle", "other"),
    FeatureDefinition("coating_cvd", "1", "CVD coated tool", "other"),
    FeatureDefinition("coating_pvd", "1", "PVD coated tool", "other"),
    FeatureDefinition("cutting_speed_mpm", "m/min", "cutting speed Vc", "process"),
    FeatureDefinition("log_cutting_speed", "1", "log cutting speed", "process"),
    FeatureDefinition("feed_mm_rev", "mm/rev", "feed per revolution", "process"),
    FeatureDefinition("depth_of_cut_mm", "mm", "depth of cut", "process"),
    *(FeatureDefinition(name, "1", f"coolant method flag {name}", "process") for name in _COOLANT_FEATURES),
    FeatureDefinition("interrupted_cut", "1", "interrupted cutting mode", "process"),
    FeatureDefinition("holder_rigidity_ord", "1", "holder rigidity (0=low,1=standard,2=high)", "process"),
    FeatureDefinition("log_cutting_distance", "1", "log(1 + cutting distance)", "process"),
    FeatureDefinition("speed_distance", "1", "log-distance x log-speed interaction", "process"),
    FeatureDefinition("hardness_distance", "1", "log-distance x hardness interaction", "process"),
    FeatureDefinition("wear_index_distance", "1", "log-distance / tool wear resistance", "process"),
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
            raise ValueError(f"Composition {name} must be finite and within 0-100%")
        values[name] = float(raw)
    return values


def _categorical(candidate: CandidateInput) -> dict[str, str]:
    values: dict[str, str] = {}
    for name, choices in CATEGORICAL_CHOICES.items():
        raw = candidate.inputs.categorical.get(name)
        if raw not in choices:
            raise ValueError(f"未対応の選択肢です: {name}={raw!r}")
        values[name] = raw
    return values


def candidate_from_observation(row: dict[str, Any]) -> CandidateInput | None:
    if row.get("task_id") not in {None, TASK_ID}:
        return None
    features, composition = row.get("features"), row.get("composition")
    if not features or not composition:
        return None
    if any(features.get(name) is None for name in PROCESS_NAMES):
        return None
    if any(features.get(name) not in choices for name, choices in CATEGORICAL_CHOICES.items()):
        return None
    return CandidateInput(
        name=str(row["parent_key"]),
        inputs={
            "composition": {name: composition[name] for name in COMPOSITION_NAMES if name in composition},
            "process": {name: float(features[name]) for name in PROCESS_NAMES},
            "categorical": {name: str(features[name]) for name in CATEGORICAL_CHOICES},
            "heat_pattern": None,
        },
    )


def build_flank_wear_features_from_observation(row: dict[str, Any], composition_defaults: Mapping[str, float]) -> FeatureBundle | None:
    candidate = candidate_from_observation(row)
    return None if candidate is None else build_flank_wear_features(candidate, composition_defaults)


def build_flank_wear_features(candidate: CandidateInput, composition_defaults: Mapping[str, float]) -> FeatureBundle:
    composition = _composition(candidate, composition_defaults)
    categorical = _categorical(candidate)
    process: dict[str, float] = {}
    for name in PROCESS_NAMES:
        raw = candidate.inputs.process.get(name)
        if raw is None or not math.isfinite(float(raw)):
            raise ValueError(f"工程入力が不足しています: {name}")
        process[name] = float(raw)
    if process["cutting_distance_m"] < 0:
        raise ValueError("切削距離は0以上にしてください")
    if process["cutting_speed_mpm"] <= 0:
        raise ValueError("切削速度は正の値にしてください")
    log_distance = math.log1p(process["cutting_distance_m"])
    log_speed = math.log(process["cutting_speed_mpm"])
    wear_index = max(process["wear_resistance_index"], 0.1)
    values = np.asarray([
        *(composition[name] for name in COMPOSITION_NAMES),
        process["hardness_hv"],
        process["tensile_mpa"],
        process["wear_resistance_index"],
        process["fracture_resistance_index"],
        process["nose_radius_mm"],
        process["rake_angle_deg"],
        1.0 if categorical["coating_method"] == "CVD" else 0.0,
        1.0 if categorical["coating_method"] == "PVD" else 0.0,
        process["cutting_speed_mpm"],
        log_speed,
        process["feed_mm_rev"],
        process["depth_of_cut_mm"],
        *(1.0 if categorical["coolant_method"] == choice else 0.0 for choice in COOLANT_CHOICES),
        1.0 if categorical["cutting_mode"] == "断続" else 0.0,
        float(RIGIDITY_CHOICES.index(categorical["holder_rigidity"])),
        log_distance,
        log_distance * log_speed / 6.0,
        log_distance * process["hardness_hv"] / 500.0,
        log_distance / wear_index,
    ], dtype=np.float64)
    if values.shape != (len(FEATURE_DEFINITIONS),) or not np.isfinite(values).all():
        raise ValueError("Flank-wear feature pipeline produced an invalid vector")
    return FeatureBundle(PIPELINE_ID, PIPELINE_VERSION, FEATURE_DEFINITIONS, values)
