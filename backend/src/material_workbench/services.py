from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from io import BytesIO
from typing import Any

import numpy as np
from openpyxl import Workbook, load_workbook

from .importer import COMPOSITION_COLUMNS, WorkbookData
from .runtime import ModelRuntime
from .schemas import Candidate, CandidateInput, HeatPoint, ScreeningRequest


SCREENABLE_FIELDS = {"thickness_mm", "line_speed_m_min", "coating", "max_temperature_c", *(f"composition.{name}" for name in COMPOSITION_COLUMNS)}
SCREENING_SEED = 20260719


def _set_screen_value(candidate: Candidate, name: str, value: float | str) -> Candidate:
    updated = candidate.model_copy(deep=True)
    if name.startswith("composition."):
        updated.composition[name.removeprefix("composition.")] = float(value)
    elif name == "max_temperature_c":
        peak = max(range(len(updated.heat_pattern)), key=lambda index: updated.heat_pattern[index].temperature_c)
        updated.heat_pattern[peak].temperature_c = float(value)
    else:
        setattr(updated, name, value)
    return updated


def run_latin_hypercube(runtime: ModelRuntime, base: Candidate, request: ScreeningRequest) -> dict[str, Any]:
    unknown = sorted(set(request.variables) - SCREENABLE_FIELDS)
    if unknown:
        raise ValueError(f"スクリーニング対象外の変数です: {', '.join(unknown)}")
    rng = np.random.default_rng(SCREENING_SEED)
    sample_values: dict[str, list[float | str]] = {}
    for name in sorted(request.variables):
        spec = request.variables[name]
        if spec.mode == "fixed":
            sample_values[name] = [spec.value] * request.samples  # type: ignore[list-item]
        elif spec.mode == "range":
            permutation = rng.permutation(request.samples)
            sample_values[name] = [float(spec.min + (permutation[index] + rng.random()) / request.samples * (spec.max - spec.min)) for index in range(request.samples)]  # type: ignore[operator]
        else:
            values = spec.values or []
            permutation = rng.permutation(request.samples)
            sample_values[name] = [values[int(np.floor((permutation[index] + rng.random()) / request.samples * len(values))) % len(values)] for index in range(request.samples)]
    points: list[dict[str, Any]] = []
    base_prediction = runtime.predict(base, detailed=False)
    for index in range(request.samples):
        candidate = base.model_copy(deep=True)
        applied = {name: sample_values[name][index] for name in sorted(sample_values)}
        for name, value in applied.items():
            candidate = _set_screen_value(candidate, name, value)
        candidate_input = CandidateInput.model_validate(candidate.model_dump())
        candidate = Candidate.model_validate({**candidate.model_dump(), **candidate_input.model_dump()})
        prediction = runtime.predict(candidate, detailed=False)
        selected = prediction["predictions"][request.target]
        support = prediction["support"]
        penalty = {"supported": 0.0, "caution": 0.15, "extrapolated": 0.5}[support.status]
        score = abs(selected.value - request.target_value) + penalty * max(1.0, abs(request.target_value) * 0.05)
        points.append({
            "index": index,
            "inputs": applied,
            "candidate": CandidateInput.model_validate(candidate.model_dump()).model_dump(mode="json"),
            "prediction": selected.model_dump(),
            "color_value": selected.value,
            "support": support.model_dump(),
            "score": round(float(score), 6),
        })
    ranked = sorted(points, key=lambda point: (point["score"], point["index"]))
    return {
        "seed": SCREENING_SEED,
        "base_candidate_id": base.id,
        "base_canonical_input": base_prediction["canonical_input"],
        "model_provenance": base_prediction["model_meta"],
        "target": request.target,
        "target_value": request.target_value,
        "samples": request.samples,
        "variables": {name: spec.model_dump() for name, spec in request.variables.items()},
        "points": points,
        "representative_points": ranked[:10],
    }


def candidate_from_lineage(data: WorkbookData, entity_key: str) -> CandidateInput:
    anneal_key = entity_key
    if anneal_key not in data.anneal_features:
        relations = data.lineage.get(entity_key, {})
        candidates = relations.get("焼鈍_key", [])
        if len(candidates) != 1:
            raise ValueError("焼鈍条件を一意にたどれないため候補化できません")
        anneal_key = candidates[0]
    relations = data.lineage.get(anneal_key, {})
    melt_keys = sorted(set(relations.get("溶製_key", [])))
    if len(melt_keys) != 1 or melt_keys[0] not in data.composition:
        raise ValueError("焼鈍条件に一意な成分が接続されていません")
    history = [row for row in data.sheets["焼鈍履歴"] if str(row.get("焼鈍_key")) == anneal_key]
    if len(history) < 2:
        raise ValueError("候補化に必要な焼鈍履歴がありません")
    history.sort(key=lambda row: (float(row.get("到達時間[s]") or 0), int(row.get("順番") or 0)))
    heat_pattern = [HeatPoint(time_s=float(row["到達時間[s]"]), temperature_c=float(row["実績温度[℃]"])) for row in history]
    feature = data.anneal_features[anneal_key]
    thickness_rows = [row for row in data.observations if row["parent_key"] == anneal_key and row["thickness_mm"] > 0]
    thickness = float(np.median([row["thickness_mm"] for row in thickness_rows])) if thickness_rows else 1.4
    return CandidateInput(name=f"過去条件 {anneal_key}", composition=deepcopy(data.composition[melt_keys[0]]), thickness_mm=thickness, line_speed_m_min=float(feature["line_speed_m_min"]), coating=str(feature["coating"]), heat_pattern=heat_pattern)


def import_candidates_xlsx(contents: bytes) -> tuple[list[CandidateInput], list[dict[str, Any]]]:
    try:
        workbook = load_workbook(BytesIO(contents), read_only=True, data_only=True)
    except Exception as exc:
        return [], [{"row": 0, "message": f"Excelを読み込めません: {exc}"}]
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    headers = [str(value).strip() if value is not None else "" for value in next(rows, [])]
    positions = {header: index for index, header in enumerate(headers) if header}
    if not headers or "name" not in positions:
        return [], [{"row": 1, "message": "必須列 name がありません"}]
    imported: list[CandidateInput] = []
    errors: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        if not any(value is not None for value in row):
            continue
        try:
            value = lambda header, default=None: row[positions[header]] if header in positions and positions[header] < len(row) else default
            composition = {name: float(value(name)) for name in COMPOSITION_COLUMNS if value(name) is not None}
            points = []
            index = 1
            while f"time_s_{index}" in positions and f"temperature_c_{index}" in positions:
                time, temperature = value(f"time_s_{index}"), value(f"temperature_c_{index}")
                if time is not None and temperature is not None:
                    points.append({"time_s": float(time), "temperature_c": float(temperature)})
                index += 1
            if len(points) < 2:
                raise ValueError("time_s_1 / temperature_c_1 から少なくとも2点が必要です")
            name = value("name")
            if name is None or not str(name).strip():
                raise ValueError("nameは空にできません")
            imported.append(CandidateInput(name=str(name).strip(), composition=composition, thickness_mm=float(value("thickness_mm", 1.4)), line_speed_m_min=float(value("line_speed_m_min", 103)), coating=str(value("coating", "なし")), heat_pattern=points))
        except (TypeError, ValueError) as exc:
            errors.append({"row": row_number, "message": str(exc)})
    return imported, errors


def candidates_xlsx(candidates: list[Candidate], runtime: ModelRuntime) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "候補"
    max_points = max((len(candidate.heat_pattern) for candidate in candidates), default=2)
    heat_headers = [item for index in range(1, max_points + 1) for item in (f"time_s_{index}", f"temperature_c_{index}")]
    headers = ["schema_version", "id", "name", *COMPOSITION_COLUMNS, "thickness_mm", "line_speed_m_min", "coating", *heat_headers, "TS", "YS", "EL", "lambda", "support_status", "support_distance"]
    sheet.append(headers)
    for candidate in candidates:
        result = runtime.predict(candidate, detailed=False)
        heat_values = [item for point in candidate.heat_pattern for item in (point.time_s, point.temperature_c)]
        heat_values.extend([None] * (len(heat_headers) - len(heat_values)))
        sheet.append(["material-workbench-candidate-v1", candidate.id, candidate.name, *(candidate.composition.get(name) for name in COMPOSITION_COLUMNS), candidate.thickness_mm, candidate.line_speed_m_min, candidate.coating, *heat_values, *(result["predictions"][target].value for target in ("TS", "YS", "EL", "lambda")), result["support"].status, result["support"].distance])
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(22, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
    guide = workbook.create_sheet("説明")
    guide.append(["schema_version", "material-workbench-candidate-v1"])
    guide.append(["用途", "候補入力と軽量プレビュー予測の出力。候補シートはそのまま候補importに使用できます。"])
    guide.append(["単位", "C, Si, Mn, P, S, Cr, Mo, Ni, Al, Ti, B, N, O, Ca は mass%; thickness_mm は mm; line_speed_m_min は m/min; time_s は s; temperature_c は ℃。"])
    guide.append(["heat pattern", "time_s_N と temperature_c_N を対で指定し、少なくとも2点を時刻の昇順で入力します。"])
    guide.append(["prediction", "TS/YS は MPa、EL/lambda は %。予測はexport時のモデル・データに基づく参考値です。"])
    guide.column_dimensions["A"].width = 20
    guide.column_dimensions["B"].width = 110
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
