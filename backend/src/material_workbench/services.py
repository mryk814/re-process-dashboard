from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from io import BytesIO
from typing import Any, Callable

import numpy as np
from openpyxl import Workbook, load_workbook

from .dataset_profile import load_dataset_profile
from .hot_rolling_feature_pipeline import PROCESS_NAMES
from .importer import WorkbookData, composition_names
from .task_registry import RuntimeProtocol, load_task_contracts
from .schemas import Candidate, CandidateInput, HeatPoint, ScreeningRequest
from .screening_score import GoalDirection, evaluate_screening_goal, score_contract


COMPOSITION_COLUMNS = composition_names(task_id="annealed-properties-v1")
SCREENING_SEED = 20260719


def _set_screen_value(candidate: Candidate, name: str, value: float | str) -> Candidate:
    updated = candidate.model_copy(deep=True)
    heat_parts = name.split(".")
    if len(heat_parts) == 3 and heat_parts[0] == "heat_pattern" and heat_parts[1].isdigit() and heat_parts[2] in {"time_s", "temperature_c"}:
        points = updated.inputs.heat_pattern
        index = int(heat_parts[1])
        if points is None or index >= len(points):
            raise ValueError(f"ヒートパターンに存在しない点です: {name}")
        setattr(points[index], heat_parts[2], float(value))
        return updated
    group, separator, field = name.partition(".")
    if not separator or group not in {"composition", "process", "categorical"}:
        raise ValueError(f"スクリーニング対象外の変数です: {name}")
    values = getattr(updated.inputs, group)
    values[field] = str(value) if group == "categorical" else float(value)
    return updated


def run_latin_hypercube(
    runtime: RuntimeProtocol,
    base: Candidate,
    request: ScreeningRequest,
    *,
    goal_directions: dict[str, GoalDirection | None],
    probability_available: dict[str, bool],
    candidate_validator: Callable[[CandidateInput], None],
) -> dict[str, Any]:
    rng = np.random.default_rng(SCREENING_SEED)
    pool_size = request.samples * 4
    sample_values: dict[str, list[float | str]] = {}
    for name in sorted(request.variables):
        spec = request.variables[name]
        if spec.mode == "fixed":
            sample_values[name] = [spec.value] * pool_size  # type: ignore[list-item]
        elif spec.mode == "range":
            permutation = rng.permutation(pool_size)
            sample_values[name] = [float(spec.min + (permutation[index] + rng.random()) / pool_size * (spec.max - spec.min)) for index in range(pool_size)]  # type: ignore[operator]
        else:
            values = spec.values or []
            permutation = rng.permutation(pool_size)
            sample_values[name] = [values[int(np.floor((permutation[index] + rng.random()) / pool_size * len(values))) % len(values)] for index in range(pool_size)]
    points: list[dict[str, Any]] = []
    base_prediction = runtime.predict(base, detailed=False)
    for sample_index in range(pool_size):
        if len(points) >= request.samples:
            break
        candidate = base.model_copy(deep=True)
        applied = {name: sample_values[name][sample_index] for name in sorted(sample_values)}
        for name, value in applied.items():
            candidate = _set_screen_value(candidate, name, value)
        try:
            candidate_input = CandidateInput.model_validate(candidate.model_dump())
            candidate_validator(candidate_input)
        except ValueError:
            continue
        candidate = Candidate.model_validate({**candidate.model_dump(), **candidate_input.model_dump()})
        target_values = {
            key: value
            for key, value in {request.target: request.target_value, **request.secondary_targets}.items()
            if value is not None and probability_available.get(key, False)
        }
        prediction = runtime.predict(
            candidate,
            detailed=False,
            target_values=target_values,
        )
        selected = prediction["predictions"][request.target]
        support = prediction["support"]
        evaluation = evaluate_screening_goal(
            selected.value,
            target_value=request.target_value,
            direction=goal_directions.get(request.target),
            at_least_probability=selected.goal_probability if probability_available.get(request.target, False) else None,
            support_distance=support.distance,
        )
        secondary_evaluations = {
            key: evaluate_screening_goal(
                prediction["predictions"][key].value,
                target_value=value,
                direction=goal_directions.get(key),
                at_least_probability=prediction["predictions"][key].goal_probability if probability_available.get(key, False) else None,
                support_distance=support.distance,
            )
            for key, value in request.secondary_targets.items()
        }
        prediction_payload = selected.model_dump()
        prediction_payload["goal_direction"] = goal_directions.get(request.target)
        predictions_payload = {}
        for key, item in prediction["predictions"].items():
            predictions_payload[key] = item.model_dump()
            predictions_payload[key]["goal_direction"] = goal_directions.get(key)
        points.append({
            "index": len(points),
            "inputs": applied,
            "candidate": CandidateInput.model_validate(candidate.model_dump()).model_dump(mode="json"),
            "prediction": prediction_payload,
            "predictions": predictions_payload,
            "color_value": selected.value,
            "support": support.model_dump(),
            "warnings": prediction.get("warnings", []),
            "similar": [item.model_dump() if hasattr(item, "model_dump") else item for item in prediction.get("similar", [])],
            "score": None if evaluation.score is None else round(float(evaluation.score), 6),
            "goal_evaluation": evaluation.model_dump(),
            "secondary_goal_evaluations": {key: item.model_dump() for key, item in secondary_evaluations.items()},
        })
    if len(points) < request.samples:
        raise ValueError(f"指定範囲では制約を満たす点を{request.samples}件作れませんでした。範囲を見直してください")
    support_rank = {"supported": 0, "caution": 1, "extrapolated": 2}
    ranked = sorted(
        points,
        key=lambda point: (
            sum(item["achieved"] is False for item in point["secondary_goal_evaluations"].values()),
            point["score"] is None,
            point["score"] if point["score"] is not None else support_rank[point["support"]["status"]],
            support_rank[point["support"]["status"]],
            point["index"],
        ),
    )
    return {
        "schema_version": "screening-run/v2",
        "seed": SCREENING_SEED,
        "base_candidate_id": base.id,
        "base_inputs": base.inputs.model_dump(mode="json"),
        "base_canonical_input": base_prediction["canonical_input"],
        "model_provenance": base_prediction["model_meta"],
        "target": request.target,
        "target_value": request.target_value,
        "secondary_targets": request.secondary_targets,
        "score_contract": score_contract(
            goal_directions.get(request.target),
            request.target_value,
            probability_available=probability_available.get(request.target, False),
        ),
        "samples": request.samples,
        "variables": {name: spec.model_dump() for name, spec in request.variables.items()},
        "points": points,
        "representative_points": ranked[:10],
    }


def candidate_from_lineage(data: WorkbookData, entity_key: str) -> CandidateInput:
    process_role = "annealing"
    process_key = entity_key
    if process_key not in data.anneal_features and process_key not in data.hot_rolling_features:
        relations = data.lineage.get(entity_key, {})
        anneal_candidates = relations.get(data.role_to_key["annealing"], [])
        hot_candidates = relations.get(data.role_to_key["hot_rolling"], [])
        if len(anneal_candidates) == 1:
            process_key = anneal_candidates[0]
        elif len(hot_candidates) == 1:
            process_key = hot_candidates[0]
            process_role = "hot_rolling"
        else:
            raise ValueError("焼鈍または熱延条件を一意にたどれないため候補化できません")
    elif process_key in data.hot_rolling_features:
        process_role = "hot_rolling"

    relations = data.lineage.get(process_key, {})
    melt_keys = sorted(set(relations.get(data.role_to_key["melt"], [])))
    if len(melt_keys) != 1 or melt_keys[0] not in data.composition:
        raise ValueError("工程条件に一意な成分が接続されていません")
    feature = data.anneal_features.get(process_key) if process_role == "annealing" else data.hot_rolling_features.get(process_key)
    if feature is None:
        raise ValueError("候補化できる工程条件が見つかりません")
    heat_pattern = None
    if process_role == "annealing":
        heat_pattern = [HeatPoint.model_validate(point) for point in deepcopy(feature["heat_pattern"])]
        if len(heat_pattern) < 2:
            raise ValueError("候補化に必要な焼鈍履歴がありません")
        process_values = {"ls_mpm": float(feature["ls_mpm"])}
        entity_type = "annealing"
    else:
        process_values = {name: float(feature[name]) for name in PROCESS_NAMES}
        entity_type = "hot_rolling"
    return CandidateInput(
        name=f"過去条件 {process_key}",
        inputs={
            "composition": deepcopy(data.composition[melt_keys[0]]),
            "process": process_values,
            "categorical": {},
            "heat_pattern": heat_pattern,
        },
        provenance={
                "source_kind": "lineage",
                "source_ref": {
                    "entity_type": entity_type,
                    "entity_key": process_key,
                "data_source_digest": data.source_sha256,
            },
        },
    )


def _candidate_xlsx_names(task_id: str, profile_path: str | None = None) -> dict[str, str]:
    profile = load_dataset_profile(profile_path)
    task_profile = profile.tasks[task_id]
    names = {
        "schema_version": "形式バージョン",
        "id": "候補ID",
        "name": "候補名",
        "support_status": "学習範囲判定",
        "support_distance": "学習範囲からの距離",
        "segment_start": "工程境界",
    }
    names.update({mapping.path: mapping.column for mapping in task_profile.mappings if mapping.column})
    heat_mapping = next((mapping for mapping in task_profile.mappings if mapping.kind == "ordered_heat_series"), None)
    if heat_mapping and heat_mapping.series_columns:
        names["time_s"] = heat_mapping.series_columns.time
        names["temperature_c"] = heat_mapping.series_columns.value
    technical_names = {
        item.name: item.column
        for item in profile.shared.technical
        if item.role == "anneal_history"
    }
    names["stage_name"] = technical_names.get("stage_name", "工程")
    names["stage_category"] = technical_names.get("stage_category", "標準工程カテゴリ")
    for observation in task_profile.observations:
        for target in (*observation.targets, *observation.auxiliary):
            names.setdefault(target.key, target.column)
    return names


def _xlsx_position(positions: dict[str, int], internal_name: str, source_name: str | None = None) -> int | None:
    for name in (source_name, internal_name):
        if name and name in positions:
            return positions[name]
    return None


def import_candidates_xlsx(
    contents: bytes,
    task_id: str = "annealed-properties-v1",
    profile_path: str | None = None,
) -> tuple[list[CandidateInput], list[dict[str, Any]]]:
    try:
        workbook = load_workbook(BytesIO(contents), read_only=True, data_only=True)
    except Exception as exc:
        return [], [{"row": 0, "message": f"Excelを読み込めません: {exc}"}]
    definition = load_task_contracts()[task_id].task_definition
    fields = {
        field.path: field
        for group in definition.input_groups
        for field in group.fields
        if field.kind in {"number", "categorical"}
    }
    composition_fields = [field for field in fields.values() if field.path.startswith("composition.")]
    process_fields = [field for field in fields.values() if field.path.startswith("process.")]
    categorical_fields = [field for field in fields.values() if field.path.startswith("categorical.")]
    heat_enabled = any(field.kind == "heat_pattern" for group in definition.input_groups for field in group.fields)
    display_names = _candidate_xlsx_names(task_id, profile_path)
    sheet = workbook.active
    rows = sheet.iter_rows(values_only=True)
    headers = [str(value).strip() if value is not None else "" for value in next(rows, [])]
    duplicate_headers = sorted({header for header in headers if header and headers.count(header) > 1})
    if duplicate_headers:
        workbook.close()
        return [], [{"row": 1, "message": f"列名が重複しています: {', '.join(duplicate_headers)}"}]
    positions = {header: index for index, header in enumerate(headers) if header}
    if not headers or _xlsx_position(positions, "name", display_names["name"]) is None:
        return [], [{"row": 1, "message": f"必須列 {display_names['name']} がありません"}]
    imported: list[CandidateInput] = []
    errors: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, start=2):
        if not any(value is not None for value in row):
            continue
        try:
            def value(header: str, source_header: str | None = None, default: Any = None) -> Any:
                position = _xlsx_position(positions, header, source_header)
                return row[position] if position is not None and position < len(row) else default

            composition = {
                field.path.removeprefix("composition."): float(value(field.path.removeprefix("composition."), display_names.get(field.path)))
                for field in composition_fields
                if value(field.path.removeprefix("composition."), display_names.get(field.path)) is not None
            }
            process = {
                field.path.removeprefix("process."): float(value(field.path.removeprefix("process."), display_names.get(field.path)))
                for field in process_fields
                if value(field.path.removeprefix("process."), display_names.get(field.path)) is not None
            }
            categorical = {
                field.path.removeprefix("categorical."): str(value(field.path.removeprefix("categorical."), display_names.get(field.path)))
                for field in categorical_fields
                if value(field.path.removeprefix("categorical."), display_names.get(field.path)) is not None
            }
            points: list[dict[str, Any]] = []
            if heat_enabled:
                index = 1
                while (
                    _xlsx_position(positions, f"time_s_{index}", f"{display_names['time_s']}_{index}") is not None
                    and _xlsx_position(positions, f"temperature_c_{index}", f"{display_names['temperature_c']}_{index}") is not None
                ):
                    time = value(f"time_s_{index}", f"{display_names['time_s']}_{index}")
                    temperature = value(f"temperature_c_{index}", f"{display_names['temperature_c']}_{index}")
                    if time is not None and temperature is not None:
                        segment_raw = value(f"segment_start_{index}", f"{display_names['segment_start']}_{index}", False)
                        segment_start = segment_raw is True or str(segment_raw).strip().lower() in {"1", "true", "yes", "あり"}
                        points.append({
                            "time_s": float(time),
                            "temperature_c": float(temperature),
                            "segment_start": segment_start,
                            "stage_name": str(value(f"stage_name_{index}", f"{display_names['stage_name']}_{index}")).strip() if value(f"stage_name_{index}", f"{display_names['stage_name']}_{index}") is not None else None,
                            "stage_category": str(value(f"stage_category_{index}", f"{display_names['stage_category']}_{index}")).strip() if value(f"stage_category_{index}", f"{display_names['stage_category']}_{index}") is not None else None,
                        })
                    index += 1
                if len(points) < 2:
                    raise ValueError(f"{display_names['time_s']}_1 / {display_names['temperature_c']}_1 から少なくとも2点が必要です")
            name = value("name", display_names["name"])
            if name is None or not str(name).strip():
                raise ValueError(f"{display_names['name']}は空にできません")
            imported.append(CandidateInput(
                name=str(name).strip(),
                inputs={
                    "composition": composition,
                    "process": process,
                    "categorical": categorical,
                    "heat_pattern": points if heat_enabled else None,
                },
            ))
        except (TypeError, ValueError) as exc:
            errors.append({"row": row_number, "message": str(exc)})
    workbook.close()
    return imported, errors


def candidates_xlsx(candidates: list[Candidate], runtime: RuntimeProtocol, task_id: str = "annealed-properties-v1") -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "候補"
    definition = load_task_contracts()[task_id].task_definition
    numeric_fields = [
        field for group in definition.input_groups
        for field in group.fields
        if field.kind == "number"
    ]
    categorical_fields = [
        field for group in definition.input_groups
        for field in group.fields
        if field.kind == "categorical"
    ]
    heat_enabled = any(field.kind == "heat_pattern" for group in definition.input_groups for field in group.fields)
    display_names = _candidate_xlsx_names(task_id, runtime.data.profile_path)
    max_points = max((len(candidate.inputs.heat_pattern or []) for candidate in candidates), default=2) if heat_enabled else 0
    heat_headers = [
        item for index in range(1, max_points + 1)
        for item in (f"time_s_{index}", f"temperature_c_{index}", f"segment_start_{index}", f"stage_name_{index}", f"stage_category_{index}")
    ]
    headers = [
        display_names["schema_version"], display_names["id"], display_names["name"],
        *[display_names.get(field.path, field.label) for field in numeric_fields],
        *[display_names.get(field.path, field.label) for field in categorical_fields],
        *[
            f"{display_names.get(header.rsplit('_', 1)[0], header.rsplit('_', 1)[0])}_{header.rsplit('_', 1)[1]}"
            for header in heat_headers
        ],
        *[display_names.get(output.key, output.label) for output in definition.outputs],
        display_names["support_status"], display_names["support_distance"],
    ]
    if len(headers) != len(set(headers)):
        duplicates = sorted({header for header in headers if headers.count(header) > 1})
        raise ValueError(f"候補XLSXの見出しが重複しています: {', '.join(duplicates)}")
    sheet.append(headers)
    for candidate in candidates:
        result = runtime.predict(candidate, detailed=False)
        heat_values = [
            item
            for point in (candidate.inputs.heat_pattern or [])
            for item in (point.time_s, point.temperature_c, point.segment_start, point.stage_name, point.stage_category)
        ] if heat_enabled else []
        heat_values.extend([None] * (len(heat_headers) - len(heat_values)))
        input_values = [
            candidate.inputs.composition.get(field.path.removeprefix("composition."))
            if field.path.startswith("composition.")
            else candidate.inputs.process.get(field.path.removeprefix("process."))
            for field in numeric_fields
        ]
        input_values.extend([
            candidate.inputs.categorical.get(field.path.removeprefix("categorical."))
            for field in categorical_fields
        ])
        sheet.append([
            "material-workbench-candidate-v2", candidate.id, candidate.name, *input_values, *heat_values,
            *(result["predictions"][output.key].value for output in definition.outputs),
            result["support"].status, result["support"].distance,
        ])
    for column in sheet.columns:
        letter = column[0].column_letter
        sheet.column_dimensions[letter].width = min(22, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
    guide = workbook.create_sheet("説明")
    guide.append([display_names["schema_version"], "material-workbench-candidate-v2"])
    guide.append(["用途", "候補入力と軽量プレビュー予測の出力。候補シートはそのまま候補importに使用できます。"])
    guide.append(["入力項目", " / ".join(f"{field.label}[{field.unit}]" for field in (*numeric_fields, *categorical_fields))])
    if heat_enabled:
        guide.append(["焼鈍履歴", f"{display_names['time_s']}_N / {display_names['temperature_c']}_N を履歴点として指定し、{display_names['stage_name']}_N を工程名として表示します。工程の先頭は {display_names['segment_start']}_N で表します。"])
    guide.append(["予測", "出力単位はTaskDefinitionの定義に従います。"])
    guide.column_dimensions["A"].width = 20
    guide.column_dimensions["B"].width = 110
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
