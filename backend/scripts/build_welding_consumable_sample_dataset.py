"""多段構造（原料配合→材料成分→溶着金属成分→特性）の合成データを生成する。

`docs/decisions/multistage-chain-architecture.md` の方針を検証するためのデータであり、
実測値ではない。生成物は
`artifacts/derived-data/welding_consumable_multistage_synthetic_dataset.xlsx`。

段の構造は次のとおり。

    [原料 slot × K] --A: 線形--> [材料成分] --B: 非線形--> [溶着金属成分] --C: 非線形--> [特性]

Aはフープ（外皮）と充填粉の合成を含む厳密な線形変換であり、行列は `原料成分` シートに
そのまま入っている。BとCは歩留まり・塩基度・酸素・入熱に依存する非線形関数で生成し、
中間量である溶着金属成分は `溶着金属成分` シートへ実測相当として保存する。

実行:

    uv run python backend/scripts/build_welding_consumable_sample_dataset.py
"""
from __future__ import annotations

import argparse
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from openpyxl import Workbook

SEED = 20260725
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = (REPOSITORY_ROOT / "data" / "source").resolve()
OUTPUT_PATH = Path(
    "artifacts/derived-data/welding_consumable_multistage_synthetic_dataset.xlsx"
)

# 原料と材料の成分軸。元素と化合物を同じ軸上に並べ、合計が100%になる。
ELEMENTS = (
    "Fe", "C", "Si", "Mn", "Cr", "Ni", "Mo", "Ti", "B", "Al",
    "Mg", "Nb", "V", "Cu", "Zr", "Ca", "N", "O", "S", "P",
)
COMPOUNDS = (
    "CaF2", "TiO2", "SiO2", "Al2O3", "MgO", "ZrO2", "K2O", "Na2O", "CaCO3", "Fe2O3", "その他",
)
COMPONENTS = ELEMENTS + COMPOUNDS

# 溶着金属として分析する成分。材料成分より少ない。
WELD_METAL_ELEMENTS = (
    "C", "Si", "Mn", "P", "S", "Ni", "Cr", "Mo", "Cu", "Ti", "B", "Nb", "V", "Al", "N", "O",
)

PROJECT_NAMES = ("低温靭性PJ", "高強度FCWPJ", "耐食盛金PJ", "原料代替PJ")
OPERATORS = ("森", "山田", "伊藤", "小島")

# 原料の原型。銘柄はここから派生させる。
# (グループ, 名称, 平均組成, バランス成分, 粒度D50の範囲, 銘柄数, 採用確率, 添加量[%]の範囲)
RAW_MATERIAL_ARCHETYPES: tuple[
    tuple[str, str, dict[str, float], str, tuple[float, float], int, float, tuple[float, float]], ...
] = (
    ("鉄粉", "還元鉄粉", {"C": 0.03, "Si": 0.10, "Mn": 0.20, "O": 0.35, "S": 0.012, "P": 0.015}, "Fe", (45, 150), 10, 1.00, (0.0, 0.0)),
    ("鉄粉", "アトマイズ鉄粉", {"C": 0.02, "Si": 0.06, "Mn": 0.15, "O": 0.18, "S": 0.010, "P": 0.012}, "Fe", (60, 180), 8, 0.45, (0.0, 0.0)),
    ("合金鉄", "高炭素フェロマンガン", {"Mn": 76.0, "C": 6.8, "Si": 1.2, "P": 0.22, "S": 0.02}, "Fe", (75, 300), 10, 0.45, (0.2, 1.6)),
    ("合金鉄", "低炭素フェロマンガン", {"Mn": 80.0, "C": 0.7, "Si": 1.0, "P": 0.20, "S": 0.02}, "Fe", (75, 300), 10, 0.85, (2.5, 9.0)),
    ("合金鉄", "フェロシリコン", {"Si": 75.0, "Al": 1.2, "Ca": 0.6, "C": 0.10}, "Fe", (75, 250), 9, 0.80, (0.6, 3.2)),
    ("合金鉄", "シリコマンガン", {"Mn": 66.0, "Si": 18.0, "C": 1.8, "P": 0.18}, "Fe", (75, 250), 8, 0.55, (1.0, 4.5)),
    ("合金鉄", "フェロクロム", {"Cr": 62.0, "C": 6.5, "Si": 1.4, "S": 0.03}, "Fe", (75, 300), 9, 0.25, (0.3, 4.5)),
    ("合金鉄", "フェロモリブデン", {"Mo": 62.0, "Si": 1.0, "C": 0.08, "S": 0.08}, "Fe", (75, 250), 8, 0.40, (0.1, 1.6)),
    ("合金鉄", "フェロニオブ", {"Nb": 64.0, "Si": 2.0, "Al": 1.5, "C": 0.10}, "Fe", (75, 250), 6, 0.22, (0.05, 0.45)),
    ("合金鉄", "フェロチタン", {"Ti": 40.0, "Al": 6.0, "Si": 2.5, "C": 0.10}, "Fe", (60, 200), 8, 0.70, (0.15, 1.10)),
    ("合金鉄", "フェロバナジウム", {"V": 52.0, "Si": 1.5, "Al": 1.5, "C": 0.15}, "Fe", (75, 250), 6, 0.20, (0.05, 0.40)),
    ("合金鉄", "フェロボロン", {"B": 18.0, "C": 0.5, "Si": 1.0, "Al": 0.5}, "Fe", (45, 150), 6, 0.55, (0.02, 0.16)),
    ("純金属粉", "ニッケル粉", {"Ni": 99.4, "C": 0.05, "O": 0.20, "S": 0.005}, "Fe", (10, 60), 9, 0.45, (0.3, 6.0)),
    ("純金属粉", "銅粉", {"Cu": 99.4, "O": 0.25, "P": 0.01}, "Fe", (20, 90), 6, 0.20, (0.1, 1.2)),
    ("純金属粉", "モリブデン粉", {"Mo": 99.5, "O": 0.20, "C": 0.02}, "Fe", (5, 40), 6, 0.18, (0.05, 0.90)),
    ("純金属粉", "金属クロム粉", {"Cr": 99.0, "O": 0.35, "C": 0.03, "N": 0.05}, "Fe", (30, 120), 6, 0.18, (0.2, 3.0)),
    ("脱酸剤", "アルミニウム粉", {"Al": 99.2, "Si": 0.15, "O": 0.35}, "Fe", (25, 110), 8, 0.70, (0.15, 1.10)),
    ("脱酸剤", "マグネシウム粉", {"Mg": 99.0, "Al": 0.30, "O": 0.40}, "Fe", (25, 120), 7, 0.60, (0.10, 0.90)),
    ("脱酸剤", "アルミマグネシウム合金粉", {"Al": 51.0, "Mg": 47.0, "O": 0.50}, "Fe", (30, 130), 6, 0.35, (0.10, 0.80)),
    ("脱酸剤", "カルシウムシリコン", {"Si": 60.0, "Ca": 30.0, "Al": 1.5}, "Fe", (45, 180), 6, 0.30, (0.10, 0.90)),
    ("スラグ形成剤", "ルチール", {"TiO2": 94.5, "SiO2": 1.8, "Al2O3": 0.9, "Fe2O3": 1.2, "ZrO2": 0.3}, "その他", (60, 250), 12, 0.92, (9.0, 28.0)),
    ("スラグ形成剤", "珪砂", {"SiO2": 97.5, "Al2O3": 1.0, "Fe2O3": 0.2}, "その他", (75, 300), 8, 0.65, (0.8, 5.0)),
    ("スラグ形成剤", "アルミナ", {"Al2O3": 96.0, "SiO2": 1.5, "Fe2O3": 0.3}, "その他", (45, 200), 7, 0.50, (0.4, 3.5)),
    ("スラグ形成剤", "ジルコンサンド", {"ZrO2": 64.0, "SiO2": 32.0, "Al2O3": 1.0, "Fe2O3": 0.3}, "その他", (60, 200), 6, 0.45, (0.4, 3.5)),
    ("スラグ形成剤", "マグネシアクリンカー", {"MgO": 94.0, "SiO2": 2.0, "Al2O3": 0.8, "CaCO3": 1.0}, "その他", (60, 250), 7, 0.50, (0.4, 4.5)),
    ("スラグ形成剤", "酸化鉄", {"Fe2O3": 95.0, "SiO2": 1.5, "Al2O3": 0.6}, "その他", (45, 180), 6, 0.35, (0.3, 2.5)),
    ("アーク安定剤", "蛍石", {"CaF2": 95.0, "SiO2": 2.0, "CaCO3": 1.0}, "その他", (60, 250), 9, 0.70, (0.8, 8.0)),
    ("アーク安定剤", "カリ長石", {"SiO2": 65.0, "Al2O3": 18.0, "K2O": 11.5, "Na2O": 2.5}, "その他", (60, 250), 7, 0.55, (0.5, 4.0)),
    ("アーク安定剤", "ソーダ長石", {"SiO2": 68.0, "Al2O3": 19.0, "Na2O": 10.5, "K2O": 1.0}, "その他", (60, 250), 6, 0.40, (0.4, 3.0)),
    ("アーク安定剤", "珪酸カリウム", {"SiO2": 62.0, "K2O": 30.0, "Na2O": 2.0}, "その他", (30, 150), 6, 0.45, (0.2, 2.0)),
    ("炭素源", "黒鉛", {"C": 98.5, "SiO2": 0.4, "S": 0.05}, "その他", (10, 90), 6, 0.45, (0.02, 0.28)),
    ("ガス発生剤", "石灰石", {"CaCO3": 96.5, "SiO2": 1.0, "MgO": 0.8}, "その他", (60, 250), 6, 0.40, (0.4, 3.0)),
    ("ガス発生剤", "ドロマイト", {"CaCO3": 56.0, "MgO": 21.0, "SiO2": 1.5}, "その他", (60, 250), 5, 0.30, (0.4, 2.5)),
    ("バインダ", "珪酸ソーダ", {"SiO2": 58.0, "Na2O": 24.0}, "その他", (20, 120), 5, 0.75, (0.4, 1.6)),
    ("バインダ", "セルロース", {"C": 44.0, "O": 49.0}, "その他", (20, 150), 4, 0.25, (0.1, 0.6)),
)

# 設計狙いごとの採用確率の偏り。原型名に対する倍率。
PURPOSE_BIAS: dict[str, dict[str, float]] = {
    "低温靭性": {"ニッケル粉": 2.0, "蛍石": 1.3, "ルチール": 0.85, "高炭素フェロマンガン": 0.4, "石灰石": 1.4},
    "高強度": {"フェロモリブデン": 2.0, "フェロボロン": 1.5, "フェロニオブ": 2.0, "ニッケル粉": 1.5},
    "耐食": {"フェロクロム": 3.0, "金属クロム粉": 3.0, "ニッケル粉": 2.0, "フェロニオブ": 2.0, "黒鉛": 0.3},
    "原料代替": {"アトマイズ鉄粉": 1.6, "シリコマンガン": 1.8, "ソーダ長石": 1.5, "酸化鉄": 1.5},
    "標準": {},
}

# 鉄粉が取る残部の下限[%]。これを下回る配合は非鉄粉側を縮めて成立させる。
MIN_IRON_POWDER_RATIO = 18.0

PURPOSE_BY_GROUP = {
    "鉄粉": "母材金属",
    "合金鉄": "合金添加",
    "純金属粉": "合金添加",
    "脱酸剤": "脱酸",
    "スラグ形成剤": "スラグ形成",
    "アーク安定剤": "アーク安定",
    "炭素源": "炭素調整",
    "ガス発生剤": "シールド補助",
    "バインダ": "造粒",
}

HOOPS = (
    # (key, 名称, 材質記号, 板厚, 幅, 組成)
    ("HP-01", "軟鋼フープ", "SPCC", 0.4, 12.0, {"C": 0.045, "Si": 0.02, "Mn": 0.24, "P": 0.014, "S": 0.010}),
    ("HP-02", "極低炭素フープ", "SPCE", 0.4, 12.0, {"C": 0.010, "Si": 0.01, "Mn": 0.18, "P": 0.012, "S": 0.008}),
    ("HP-03", "13Cr系フープ", "SUS410L", 0.4, 12.5, {"C": 0.020, "Si": 0.35, "Mn": 0.40, "Cr": 12.8, "Ni": 0.30}),
    ("HP-04", "18-8系フープ", "SUS304", 0.4, 12.5, {"C": 0.045, "Si": 0.45, "Mn": 1.10, "Cr": 18.2, "Ni": 8.3}),
)

SHIELD_GASES = ("100%CO2", "80%Ar-20%CO2")
POSITIONS = ("下向", "立向上進", "横向")
GROOVES = ("V形20mm", "V形25mm", "レ形20mm")


def _jitter(rng: np.random.Generator, value: float, relative: float) -> float:
    return float(max(0.0, value * math.exp(rng.normal(0.0, relative))))


def _normalize_to_total(values: dict[str, float], balance: str, total: float = 100.0) -> dict[str, float]:
    """バランス成分で合計をtotalへ合わせる。超過分は非バランス成分を縮める。"""
    fixed = {key: value for key, value in values.items() if key != balance}
    fixed_sum = sum(fixed.values())
    if fixed_sum > total:
        scale = total / fixed_sum
        fixed = {key: value * scale for key, value in fixed.items()}
        fixed_sum = total
    result = {component: 0.0 for component in COMPONENTS}
    result.update(fixed)
    result[balance] = result.get(balance, 0.0) + (total - fixed_sum)
    return result


def build_raw_materials(rng: np.random.Generator) -> list[dict[str, Any]]:
    materials: list[dict[str, Any]] = []
    index = 0
    base_day = date(2024, 1, 9)
    for group, base_name, archetype, balance, d50_range, count, _use_prob, _add_range in RAW_MATERIAL_ARCHETYPES:
        for variant in range(count):
            index += 1
            jittered = {
                component: _jitter(rng, value, 0.06 if value > 1.0 else 0.20)
                for component, value in archetype.items()
            }
            composition = _normalize_to_total(jittered, balance)
            # 調達区分。常用と条件付だけで概ね100点になり、残りは選択肢として存在する。
            procurement = str(
                rng.choice(
                    ("常用", "条件付", "試作限定", "廃止予定"),
                    p=(0.26, 0.24, 0.24, 0.26),
                )
            )
            materials.append(
                {
                    "key": f"RM-{index:04d}",
                    "group": group,
                    "archetype": base_name,
                    "name": f"{base_name}-{variant + 1:02d}",
                    "supplier": f"サプライヤ{chr(ord('A') + (index % 7))}",
                    "procurement": procurement,
                    "unit_price": round(float(rng.uniform(90, 2400)), 1),
                    "d50": round(float(rng.uniform(*d50_range)), 1),
                    "shape": str(rng.choice(("粉末", "顆粒", "破砕"), p=(0.6, 0.25, 0.15))),
                    "moisture": round(float(rng.uniform(0.01, 0.35)), 3),
                    "registered_on": base_day + timedelta(days=int(rng.integers(0, 640))),
                    "composition": composition,
                }
            )
    return materials


def build_blends(
    rng: np.random.Generator,
    materials: list[dict[str, Any]],
    count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # 廃止予定は選択肢として残るが配合には現れない。調達区分が選ばれやすさを決める。
    pick_weight = {"常用": 1.0, "条件付": 0.18, "試作限定": 0.02}
    by_archetype: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for material in materials:
        if material["procurement"] == "廃止予定":
            continue
        by_archetype.setdefault(material["archetype"], []).append(
            (material, pick_weight[material["procurement"]])
        )

    def pick_brand(archetype: str) -> dict[str, Any] | None:
        pool = by_archetype.get(archetype)
        if not pool:
            return None
        weights = np.array([weight for _, weight in pool], dtype=float)
        weights = weights / weights.sum()
        return pool[int(rng.choice(len(pool), p=weights))][0]

    blends: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    base_day = date(2025, 1, 14)
    for blend_index in range(1, count + 1):
        blend_key = f"BL-{blend_index:04d}"
        purpose = str(rng.choice(tuple(PURPOSE_BIAS), p=(0.26, 0.24, 0.16, 0.14, 0.20)))
        bias = PURPOSE_BIAS[purpose]

        selected: list[tuple[dict[str, Any], float]] = []
        iron_brands: list[dict[str, Any]] = []
        for group, base_name, _c, _b, _d, _n, use_prob, (low, high) in RAW_MATERIAL_ARCHETYPES:
            probability = min(0.97, use_prob * bias.get(base_name, 1.0))
            if rng.random() >= probability:
                continue
            brand = pick_brand(base_name)
            if brand is None:
                continue
            if group == "鉄粉":
                iron_brands.append(brand)
            else:
                selected.append((brand, float(rng.uniform(low, high))))

        # 点数を10〜20へ収める。多い場合は少量側を落とし、少ない場合は合金鉄を足す。
        selected.sort(key=lambda item: -item[1])
        while len(selected) + max(1, len(iron_brands)) > 20:
            selected.pop()
        for base_name in ("フェロクロム", "フェロモリブデン", "珪砂", "アルミナ", "石灰石"):
            if len(selected) + max(1, len(iron_brands)) >= 10:
                break
            if any(material["archetype"] == base_name for material, _ in selected):
                continue
            brand = pick_brand(base_name)
            if brand is not None:
                selected.append((brand, float(rng.uniform(0.3, 1.5))))

        if not iron_brands:
            fallback = pick_brand("還元鉄粉")
            if fallback is not None:
                iron_brands.append(fallback)

        non_iron_total = sum(ratio for _, ratio in selected)
        ceiling = 100.0 - MIN_IRON_POWDER_RATIO
        if non_iron_total > ceiling:
            scale = ceiling / non_iron_total
            selected = [(material, ratio * scale) for material, ratio in selected]
            non_iron_total = ceiling
        iron_split = rng.dirichlet(np.full(len(iron_brands), 3.0))
        for position, brand in enumerate(iron_brands):
            selected.append((brand, (100.0 - non_iron_total) * float(iron_split[position])))
        selected.sort(key=lambda item: -item[1])

        # 表示桁で厳密に合計100%へ合わせる。段Aをシートの値からそのまま再現できるようにする。
        selected = [(material, round(ratio, 4)) for material, ratio in selected]
        residual = round(100.0 - sum(ratio for _, ratio in selected), 4)
        selected[0] = (selected[0][0], round(selected[0][1] + residual, 4))

        hoop = HOOPS[int(rng.choice(len(HOOPS), p=(0.62, 0.16, 0.10, 0.12)))]
        blends.append(
            {
                "key": blend_key,
                "control_no": f"FC-2025-{blend_index:04d}",
                "hoop_key": hoop[0],
                "fill_ratio": round(float(rng.uniform(13.5, 22.5)), 2),
                "wire_diameter": float(rng.choice((1.2, 1.4, 1.6))),
                "purpose": purpose,
                "project": str(rng.choice(PROJECT_NAMES)),
                "operator": str(rng.choice(OPERATORS)),
                "registered_on": base_day + timedelta(days=int(rng.integers(0, 420))),
                "items": selected,
            }
        )
        for material, ratio in selected:
            lines.append(
                {
                    "blend_key": blend_key,
                    "material_key": material["key"],
                    "ratio": ratio,
                    "purpose": PURPOSE_BY_GROUP[material["group"]],
                }
            )
    return blends, lines


def material_composition(blend: dict[str, Any], hoop: dict[str, Any]) -> dict[str, float]:
    """段A。フープと充填粉の線形合成。学習モデルを使わない厳密な変換。"""
    fill = blend["fill_ratio"] / 100.0
    core = {component: 0.0 for component in COMPONENTS}
    for material, ratio in blend["items"]:
        weight = ratio / 100.0
        for component, value in material["composition"].items():
            core[component] += weight * value
    result = {}
    for component in COMPONENTS:
        result[component] = fill * core[component] + (1.0 - fill) * hoop["composition"].get(component, 0.0)
    return result


def alloy_powder_d50(blend: dict[str, Any]) -> float:
    """成分ベクトルに現れない原料属性。段Bの補助特徴の例として使う。"""
    numerator = 0.0
    denominator = 0.0
    for material, ratio in blend["items"]:
        if material["group"] not in {"合金鉄", "純金属粉", "脱酸剤"}:
            continue
        numerator += ratio * material["d50"]
        denominator += ratio
    return numerator / denominator if denominator > 0 else 120.0


def build_weld_conditions(rng: np.random.Generator, count: int) -> list[dict[str, Any]]:
    conditions = []
    for index in range(1, count + 1):
        current = float(rng.uniform(180, 320))
        voltage = float(rng.uniform(24, 34))
        speed = float(rng.uniform(240, 460))
        heat_input = 60.0 * current * voltage / (1000.0 * speed)
        conditions.append(
            {
                "key": f"WC-{index:04d}",
                "current": round(current, 1),
                "voltage": round(voltage, 2),
                "speed": round(speed, 1),
                "heat_input": round(heat_input, 3),
                "preheat": round(float(rng.choice((20, 50, 80, 100, 150))), 1),
                "interpass": round(float(rng.uniform(120, 250)), 1),
                "gas": str(rng.choice(SHIELD_GASES, p=(0.58, 0.42))),
                "gas_flow": round(float(rng.uniform(20, 30)), 1),
                "polarity": "DCEP",
                "position": str(rng.choice(POSITIONS, p=(0.7, 0.2, 0.1))),
                "groove": str(rng.choice(GROOVES)),
                "layers": int(rng.integers(6, 13)),
                "project": str(rng.choice(PROJECT_NAMES)),
            }
        )
    return conditions


def weld_metal_composition(
    rng: np.random.Generator,
    mc: dict[str, float],
    condition: dict[str, Any],
    d50_alloy: float,
) -> dict[str, float]:
    """段B。歩留まりが入熱・シールドガス・スラグ塩基度・酸素に依存する非線形変換。"""
    heat = condition["heat_input"]
    gas = 1.0 if condition["gas"] == "100%CO2" else 0.72
    basic = mc["CaF2"] + mc["MgO"] + 0.56 * mc["CaCO3"] + mc["K2O"] + mc["Na2O"]
    acid = mc["SiO2"] + mc["TiO2"] + 0.5 * (mc["Al2O3"] + mc["ZrO2"]) + 1e-6
    basicity = basic / acid
    fine_bonus = 0.035 * (170.0 - d50_alloy) / 170.0

    oxygen = (
        0.008
        + 0.022 * gas
        + 0.014 / (0.6 + basicity)
        - 0.012 * mc["Al"]
        - 0.010 * mc["Mg"]
        + 0.004 * heat
    )
    oxygen = float(np.clip(oxygen * math.exp(rng.normal(0.0, 0.07)), 0.014, 0.115))

    nitrogen = (
        0.0040
        + 0.00035 * (condition["voltage"] - 26.0)
        + (0.0013 if condition["position"] == "立向上進" else 0.0)
        - 0.00006 * (condition["gas_flow"] - 25.0)
    )
    nitrogen = float(np.clip(nitrogen * math.exp(rng.normal(0.0, 0.09)), 0.0022, 0.0145))

    excess_oxygen = oxygen - 0.030
    recovery = {
        "Mn": 0.86 - 0.07 * gas - 0.020 * (heat - 1.4) + 0.05 * min(basicity, 1.6) + fine_bonus,
        "Si": 0.82 - 0.09 * gas - 0.022 * (heat - 1.4) + 0.04 * min(basicity, 1.6) + fine_bonus,
        "C": 0.92 - 0.05 * gas,
        "Ti": 0.60 * math.exp(-12.0 * excess_oxygen) + fine_bonus,
        "B": 0.55 * math.exp(-14.0 * excess_oxygen),
        "Al": 0.25 * math.exp(-9.0 * excess_oxygen),
        "Nb": 0.90 + fine_bonus,
        "V": 0.90 + fine_bonus,
        "Ni": 0.985,
        "Cu": 0.980,
        "Mo": 0.975,
        "Cr": 0.955 - 0.03 * gas,
        "P": 0.90,
        "S": 0.75 - 0.15 * min(basicity, 1.6),
    }

    result: dict[str, float] = {}
    for element, ratio in recovery.items():
        value = max(ratio, 0.01) * mc[element]
        if element == "Si":
            value += 0.010 * mc["SiO2"]
        if element == "Ti":
            value += 0.0040 * mc["TiO2"]
        if element == "C":
            value += 0.004 * gas
        result[element] = float(max(0.0, value * math.exp(rng.normal(0.0, 0.05))))
    result["O"] = oxygen
    result["N"] = nitrogen
    result["_basicity"] = basicity
    return result


def properties(
    rng: np.random.Generator,
    wm: dict[str, float],
    condition: dict[str, Any],
) -> dict[str, float]:
    """段C。溶着金属成分と入熱から特性を決める。"""
    heat = condition["heat_input"]
    # 置換型固溶元素は飽和させ、Cr・Ni量の多い設計でも強度が発散しないようにする。
    tensile = (
        350.0
        + 1400.0 * wm["C"]
        + 90.0 * wm["Mn"]
        + 50.0 * wm["Si"]
        + 220.0 * wm["Mo"]
        + 800.0 * wm["Ti"]
        + 6000.0 * wm["B"]
        + 110.0 * (1.0 - math.exp(-wm["Ni"] / 3.0))
        + 120.0 * (1.0 - math.exp(-wm["Cr"] / 6.0))
        - 30.0 * (heat - 1.4)
        - 0.2 * (condition["preheat"] - 20.0)
    )
    tensile = float(tensile + rng.normal(0.0, 11.0))
    yield_strength = float(tensile * (0.845 + 0.02 * wm["Mo"]) - 22.0 + rng.normal(0.0, 9.0))
    elongation = float(
        np.clip(34.0 - 0.028 * (tensile - 450.0) - 40.0 * wm["O"] + rng.normal(0.0, 1.4), 7.0, 42.0)
    )
    reduction = float(np.clip(72.0 - 0.030 * (tensile - 450.0) - 60.0 * wm["O"] + rng.normal(0.0, 2.0), 20.0, 85.0))

    transition = (
        -115.0
        + 1000.0 * wm["O"]
        + 3000.0 * wm["N"]
        + 30.0 * (heat - 1.4)
        + 0.05 * (tensile - 500.0)
        - 12.0 * wm["Ni"]
        - 1500.0 * wm["B"]
    )
    upper_shelf = float(np.clip(215.0 - 0.09 * (tensile - 450.0) - 900.0 * wm["O"], 45.0, 260.0))
    corrosion = float(
        math.exp(-0.51 - 0.16 * wm["Cr"] - 0.05 * wm["Ni"] - 0.25 * wm["Mo"] + 3.0 * wm["O"])
        * math.exp(rng.normal(0.0, 0.10))
    )
    return {
        "tensile": tensile,
        "yield": yield_strength,
        "elongation": elongation,
        "reduction": reduction,
        "transition": float(transition),
        "upper_shelf": upper_shelf,
        "corrosion": corrosion,
    }


def charpy(rng: np.random.Generator, prop: dict[str, float], temperature: float) -> tuple[float, float]:
    energy = prop["upper_shelf"] / (1.0 + math.exp(-(temperature - prop["transition"]) / 17.0))
    energy = float(max(3.0, energy * math.exp(rng.normal(0.0, 0.13))))
    brittle = 100.0 / (1.0 + math.exp((temperature - (prop["transition"] + 8.0)) / 15.0))
    brittle = float(np.clip(brittle + rng.normal(0.0, 4.0), 0.0, 100.0))
    return energy, brittle


def build_dataset(rng: np.random.Generator, blend_count: int, run_count: int) -> dict[str, list[list[Any]]]:
    materials = build_raw_materials(rng)
    material_by_key = {material["key"]: material for material in materials}
    blends, lines = build_blends(rng, materials, blend_count)
    blend_by_key = {blend["key"]: blend for blend in blends}
    hoop_by_key = {
        key: {"key": key, "name": name, "grade": grade, "thickness": thickness, "width": width, "composition": comp}
        for key, name, grade, thickness, width, comp in HOOPS
    }
    conditions = build_weld_conditions(rng, max(36, run_count // 7))

    sheets: dict[str, list[list[Any]]] = {}

    sheets["原料マスタ"] = [
        [
            "原料_key**", "原料名", "原料グループ", "供給者", "調達区分", "単価[円/kg]",
            "粒度D50[um]", "形状", "水分[%]", "登録日",
        ]
    ]
    sheets["原料成分"] = [["原料_key**", "原料名", *[f"{component}[%]" for component in COMPONENTS]]]
    for material in materials:
        sheets["原料マスタ"].append(
            [
                material["key"], material["name"], material["group"], material["supplier"],
                material["procurement"], material["unit_price"], material["d50"],
                material["shape"], material["moisture"], material["registered_on"],
            ]
        )
        sheets["原料成分"].append(
            [material["key"], material["name"], *[round(material["composition"][c], 5) for c in COMPONENTS]]
        )

    sheets["フープマスタ"] = [
        ["フープ_key**", "フープ名", "材質記号", "板厚[mm]", "幅[mm]", *[f"{e}[%]" for e in ELEMENTS]]
    ]
    for hoop in hoop_by_key.values():
        sheets["フープマスタ"].append(
            [
                hoop["key"], hoop["name"], hoop["grade"], hoop["thickness"], hoop["width"],
                *[round(hoop["composition"].get(element, 0.0), 5) for element in ELEMENTS],
            ]
        )
    # フープのFeはバランスとして扱う。
    fe_index = 5 + ELEMENTS.index("Fe")
    for row in sheets["フープマスタ"][1:]:
        others = sum(value for index, value in enumerate(row[5:], start=5) if index != fe_index)
        row[fe_index] = round(100.0 - others, 5)
        hoop_by_key[row[0]]["composition"]["Fe"] = row[fe_index]

    sheets["配合"] = [
        [
            "配合_key**", "管理番号", "フープ_key**", "充填率[%]", "ワイヤ径[mm]", "配合点数",
            "設計狙い", "プロジェクト名", "登録者", "登録日",
        ]
    ]
    for blend in blends:
        sheets["配合"].append(
            [
                blend["key"], blend["control_no"], blend["hoop_key"], blend["fill_ratio"],
                blend["wire_diameter"], len(blend["items"]), blend["purpose"],
                blend["project"], blend["operator"], blend["registered_on"],
            ]
        )

    sheets["配合明細"] = [["配合_key**", "原料_key**", "原料名", "原料グループ", "配合比[%]", "添加目的"]]
    for line in lines:
        material = material_by_key[line["material_key"]]
        sheets["配合明細"].append(
            [
                line["blend_key"], line["material_key"], material["name"], material["group"],
                line["ratio"], line["purpose"],
            ]
        )

    sheets["溶接条件"] = [
        [
            "溶接条件_key**", "電流[A]", "電圧[V]", "溶接速度[mm/min]", "入熱[kJ/mm]", "予熱温度[℃]",
            "パス間温度[℃]", "シールドガス", "ガス流量[L/min]", "極性", "溶接姿勢", "開先形状",
            "積層数", "プロジェクト名",
        ]
    ]
    for condition in conditions:
        sheets["溶接条件"].append(
            [
                condition["key"], condition["current"], condition["voltage"], condition["speed"],
                condition["heat_input"], condition["preheat"], condition["interpass"],
                condition["gas"], condition["gas_flow"], condition["polarity"],
                condition["position"], condition["groove"], condition["layers"], condition["project"],
            ]
        )

    sheets["溶接施工"] = [
        ["溶接施工_key**", "試験板番号", "試験規格", "溶接士", "施工日", "外観判定", "備考"]
    ]
    sheets["溶着金属成分"] = [
        ["溶着金属成分_key**", "分析方法", "分析日", *[f"{element}[%]" for element in WELD_METAL_ELEMENTS]]
    ]
    sheets["引張試験"] = [
        [
            "引張試験_key**", "試験片番号", "引張強さ[MPa]", "0.2%耐力[MPa]", "破断伸び[%]",
            "絞り[%]", "試験片位置", "試験日", "試験者",
        ]
    ]
    sheets["シャルピー試験"] = [
        [
            "シャルピー試験_key**", "試験片番号", "試験温度[℃]", "吸収エネルギー[J]", "脆性破面率[%]",
            "試験片位置", "ノッチ位置", "試験日", "試験者",
        ]
    ]
    sheets["腐食試験"] = [
        ["腐食試験_key**", "試験方法", "試験液", "浸漬時間[h]", "腐食速度[mm/year]", "試験日", "試験者"]
    ]
    relation_header = [
        "配合_key**", "フープ_key**", "溶接条件_key**", "溶接施工_key**", "溶着金属成分_key**",
        "引張試験_key**", "シャルピー試験_key**", "腐食試験_key**", "dummy_key**",
    ]
    sheets["relationEx"] = [relation_header]

    blend_keys = [blend["key"] for blend in blends]
    run_plan: list[tuple[str, dict[str, Any]]] = []
    for index in range(run_count):
        blend_key = blend_keys[index % len(blend_keys)] if index < len(blend_keys) else str(rng.choice(blend_keys))
        run_plan.append((blend_key, conditions[int(rng.integers(0, len(conditions)))]))

    tensile_index = 0
    charpy_index = 0
    corrosion_index = 0
    dummy_index = 0
    base_day = date(2025, 3, 3)
    for run_index, (blend_key, condition) in enumerate(run_plan, start=1):
        blend = blend_by_key[blend_key]
        hoop = hoop_by_key[blend["hoop_key"]]
        mc = material_composition(blend, hoop)
        wm = weld_metal_composition(rng, mc, condition, alloy_powder_d50(blend))
        prop = properties(rng, wm, condition)

        run_key = f"WR-{run_index:05d}"
        analysis_key = f"WA-{run_index:05d}"
        weld_day = base_day + timedelta(days=int(rng.integers(0, 500)))
        sheets["溶接施工"].append(
            [
                run_key, f"TP-{run_index:05d}", "全溶着金属試験", str(rng.choice(OPERATORS)),
                weld_day, str(rng.choice(("良", "良", "良", "要確認"), p=(0.4, 0.3, 0.22, 0.08))), None,
            ]
        )
        sheets["溶着金属成分"].append(
            [
                analysis_key, str(rng.choice(("発光分光分析", "燃焼赤外吸収", "不活性ガス融解"), p=(0.7, 0.2, 0.1))),
                weld_day + timedelta(days=2),
                *[round(wm[element], 5) for element in WELD_METAL_ELEMENTS],
            ]
        )

        observations: list[tuple[str | None, str | None, str | None]] = []
        for specimen in range(2):
            tensile_index += 1
            key = f"TT-{tensile_index:05d}"
            tensile_noise = rng.normal(0.0, 6.0)
            sheets["引張試験"].append(
                [
                    key, f"{run_key}-T{specimen + 1}", round(prop["tensile"] + tensile_noise, 1),
                    round(prop["yield"] + tensile_noise * 0.8, 1),
                    round(prop["elongation"] + float(rng.normal(0.0, 0.8)), 2),
                    round(prop["reduction"] + float(rng.normal(0.0, 1.2)), 2),
                    str(rng.choice(("中央", "表層"))), weld_day + timedelta(days=5),
                    str(rng.choice(OPERATORS)),
                ]
            )
            observations.append((key, None, None))

        temperature_set = (-60.0, -40.0, -20.0) if rng.random() < 0.6 else (-40.0, -20.0, 0.0)
        for temperature in temperature_set:
            for specimen in range(3):
                charpy_index += 1
                key = f"CH-{charpy_index:06d}"
                energy, brittle = charpy(rng, prop, temperature)
                sheets["シャルピー試験"].append(
                    [
                        key, f"{run_key}-V{int(abs(temperature)):02d}-{specimen + 1}", temperature,
                        round(energy, 1), round(brittle, 1), "中央", "溶接金属中央",
                        weld_day + timedelta(days=6), str(rng.choice(OPERATORS)),
                    ]
                )
                observations.append((None, key, None))

        if rng.random() < 0.36:
            corrosion_index += 1
            key = f"CR-{corrosion_index:05d}"
            sheets["腐食試験"].append(
                [
                    key, "浸漬試験", str(rng.choice(("3.5%NaCl", "5%H2SO4"))), 336,
                    round(prop["corrosion"], 4), weld_day + timedelta(days=20), str(rng.choice(OPERATORS)),
                ]
            )
            observations.append((None, None, key))

        for tensile_key, charpy_key, corrosion_key in observations:
            dummy_index += 1
            sheets["relationEx"].append(
                [
                    blend_key, blend["hoop_key"], condition["key"], run_key, analysis_key,
                    tensile_key, charpy_key, corrosion_key, f"dummy{dummy_index:06d}",
                ]
            )

    return sheets


def write_workbook(sheets: dict[str, list[list[Any]]], output_path: Path) -> None:
    resolved_output = output_path.resolve()
    if resolved_output == SOURCE_ROOT or SOURCE_ROOT in resolved_output.parents:
        raise ValueError(
            "data/source is read-only; generate under artifacts/derived-data "
            "and promote the reviewed revision separately"
        )
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        worksheet = workbook.create_sheet(title=name)
        for row in rows:
            worksheet.append(row)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--blends", type=int, default=120)
    parser.add_argument("--runs", type=int, default=300)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    sheets = build_dataset(rng, args.blends, args.runs)
    write_workbook(sheets, args.output)

    used_materials = {row[1] for row in sheets["配合明細"][1:]}
    print(f"wrote {args.output}")
    for name, rows in sheets.items():
        print(f"  {name}: {len(rows) - 1} rows x {len(rows[0])} cols")
    print(f"  原料の選択肢: {len(sheets['原料マスタ']) - 1} / 実際に使われた原料: {len(used_materials)}")


if __name__ == "__main__":
    main()
