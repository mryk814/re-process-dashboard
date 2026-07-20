from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Any

from openpyxl import load_workbook


COMPOSITION_COLUMNS = {
    "C": "C[mass%]", "Si": "Si[mass%]", "Mn": "Mn[mass%]", "P": "P[mass%]",
    "S": "S[mass%]", "Cr": "Cr[mass%]", "Mo": "Mo[mass%]", "Ni": "Ni[mass%]",
    "Al": "Al[mass%]", "Ti": "Ti[mass%]", "B": "B[mass%]", "N": "N[mass%]",
    "O": "O[mass%]", "Ca": "Ca[mass%]",
}

# These sheets own an entity key.  Derived tables such as 焼鈍特徴量 and
# 焼鈍履歴 deliberately do not own 焼鈍_key: treating them as entities would
# make every process-history point look like a duplicate anneal condition.
ENTITY_SHEETS = {
    "溶製": "溶製_key",
    "熱延": "熱延_key",
    "冷延": "冷延_key",
    "焼鈍": "焼鈍_key",
    "熱延引張": "熱延引張_key",
    "熱延組織": "熱延組織_key",
    "焼鈍引張": "焼鈍引張_key",
    "焼鈍穴広げ": "焼鈍穴広げ_key",
    "焼鈍組織": "焼鈍組織_key",
}
KEY_TO_SHEET = {key: sheet for sheet, key in ENTITY_SHEETS.items()}
METADATA_COLUMNS = {"ロット", "プロジェクト名", "登録者", "登録日", "備考", "学習利用区分", "試験者", "試験日", "入力者"}


def _as_date(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, (int, float)) and value > 30_000:
        return (datetime(1899, 12, 30) + timedelta(days=float(value))).date().isoformat()
    return str(value) if value is not None else None


def _records(sheet: Any) -> list[dict[str, Any]]:
    rows = sheet.iter_rows(values_only=True)
    headers = next(rows, None)
    if not headers:
        return []
    keys = [str(header) if header is not None else f"column_{i}" for i, header in enumerate(headers)]
    result: list[dict[str, Any]] = []
    for row in rows:
        record = {keys[i]: value for i, value in enumerate(row) if i < len(keys)}
        if any(value is not None for value in record.values()):
            result.append(record)
    return result


@dataclass(frozen=True)
class WorkbookData:
    source_path: str
    source_mtime_ns: int
    source_sha256: str
    sheets: dict[str, list[dict[str, Any]]]
    composition: dict[str, dict[str, float]]
    anneal_features: dict[str, dict[str, Any]]
    lineage: dict[str, dict[str, list[str]]]
    observations: list[dict[str, Any]]
    quality: list[dict[str, Any]]
    detected_quality: list[dict[str, str]]
    entities: dict[str, dict[str, dict[str, Any]]]
    medians: dict[str, float]

    @property
    def source_summary(self) -> dict[str, Any]:
        return {
            "source": self.source_path,
            "source_sha256": self.source_sha256,
            "sheets": {name: len(rows) for name, rows in self.sheets.items()},
            "observations": len(self.observations),
            "quality_issues": len(self.quality),
            "detected_quality_issues": len(self.detected_quality),
        }


def _scalar(value: Any) -> str | float | int | bool | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, float, int, bool)) or value is None:
        return value
    return str(value)


def _detect_data_quality(sheets: dict[str, list[dict[str, Any]]], entities: dict[str, dict[str, dict[str, Any]]]) -> list[dict[str, str]]:
    """Detect structural data problems without trusting the workbook's scenarios.

    The workbook's 想定異常 sheet remains useful as a curated verification
    fixture, but it is not evidence that this copy of the source was checked.
    """
    issues: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(issue_type: str, source_sheet: str, entity_key: str, detail: str) -> None:
        item = (issue_type, source_sheet, entity_key, detail)
        if item in seen:
            return
        seen.add(item)
        issues.append({
            "issue_id": f"{issue_type}:{source_sheet}:{entity_key or 'row'}:{len(issues) + 1}",
            "issue_type": issue_type,
            "source_sheet": source_sheet,
            "entity_key": entity_key,
            "detail": detail,
        })

    for sheet_name, key_column in ENTITY_SHEETS.items():
        rows = sheets[sheet_name]
        counts: dict[str, int] = defaultdict(int)
        for row_index, row in enumerate(rows, start=2):
            value = row.get(key_column)
            if value is None or not str(value).strip():
                add("missing_key", sheet_name, "", f"{row_index}行目に{key_column}がありません")
                continue
            counts[str(value)] += 1
        for key, count in counts.items():
            if count > 1:
                add("duplicate_key", sheet_name, key, f"{key_column}が{count}回出現します")

    # A test row without its parent condition is unusable even when it is not
    # represented in relation, so detect that source-level missing reference.
    for sheet_name in ("熱延引張", "焼鈍引張", "焼鈍穴広げ"):
        for row_index, row in enumerate(sheets[sheet_name], start=2):
            if row.get("反復条件_key") is None or not str(row.get("反復条件_key")).strip():
                key_column = ENTITY_SHEETS[sheet_name]
                add("missing_key", sheet_name, str(row.get(key_column) or ""), f"{row_index}行目に反復条件_keyがありません")

    relation_keys: set[str] = set()
    for row_index, edge in enumerate(sheets["relation"], start=2):
        for key_column, value in edge.items():
            if not key_column.endswith("_key") or value is None or not str(value).strip():
                continue
            key = str(value)
            relation_keys.add(key)
            source_sheet = KEY_TO_SHEET.get(key_column)
            if source_sheet is None:
                continue
            if key not in entities.get(key_column, {}):
                add("invalid_reference", "relation", key, f"{row_index}行目の{key_column}に対応する{source_sheet}レコードがありません")

    for key_column, records in entities.items():
        source_sheet = KEY_TO_SHEET[key_column]
        for key in records:
            if key not in relation_keys:
                add("orphan_entity", source_sheet, key, "どのrelation行からも参照されていません")
    return issues


def lineage_node_detail(data: WorkbookData, entity_key: str) -> dict[str, Any]:
    """Return inspectable source facts for a lineage node, not only its edges."""
    entity_type = next((key_column for key_column, records in data.entities.items() if entity_key in records), None)
    if entity_type is None:
        relations = data.lineage.get(entity_key)
        if relations is None:
            raise KeyError(entity_key)
        entity_type = next((column for column, values in relations.items() if entity_key in values), None)
        if entity_type not in KEY_TO_SHEET:
            raise KeyError(entity_key)
        source_sheet = KEY_TO_SHEET[entity_type]
        source_row: dict[str, Any] = {}
        missing_source = True
    else:
        source_sheet = KEY_TO_SHEET[entity_type]
        source_row = data.entities[entity_type][entity_key]
        missing_source = False
    relations = data.lineage.get(entity_key, {})
    melt_keys = [entity_key] if entity_type == "溶製_key" else relations.get("溶製_key", [])
    composition = data.composition.get(melt_keys[0], {}) if len(melt_keys) == 1 else {}
    anneal_keys = [entity_key] if entity_type == "焼鈍_key" else relations.get("焼鈍_key", [])
    heat_pattern: list[dict[str, Any]] = []
    if len(anneal_keys) == 1:
        heat_pattern = [
            {"time_s": float(row["到達時間[s]"]), "temperature_c": float(row["実績温度[℃]"])}
            for row in sorted(data.sheets["焼鈍履歴"], key=lambda row: row.get("順番", 0))
            if str(row.get("焼鈍_key")) == anneal_keys[0]
            and isinstance(row.get("到達時間[s]"), (int, float))
            and isinstance(row.get("実績温度[℃]"), (int, float))
        ]
        _normalize_stage_local_times(heat_pattern)
    connected_keys = {entity_key, *(key for values in relations.values() for key in values)}
    aggregate: dict[str, list[float]] = defaultdict(list)
    grouped_values: dict[tuple[str, str], list[float]] = defaultdict(list)
    grouped_observations: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    observation_count = 0
    connected_observations: list[dict[str, Any]] = []
    for observation in data.observations:
        if observation["id"] not in connected_keys and observation["parent_key"] not in connected_keys:
            continue
        observation_count += 1
        connected_observations.append({
            "id": observation["id"],
            "source": observation["source"],
            "parent_key": observation["parent_key"],
            "outputs": observation["outputs"],
        })
        for property_name, value in observation["outputs"].items():
            aggregate[property_name].append(float(value))
            group = (str(observation["source"]), property_name)
            grouped_values[group].append(float(value))
            grouped_observations[group].append(connected_observations[-1])
    property_summary = {
        property_name: {
            "count": len(values), "min": min(values), "mean": float(fmean(values)),
            "std": float(pstdev(values)), "median": float(median(values)), "max": max(values),
        }
        for property_name, values in sorted(aggregate.items())
    }
    observation_groups = [
        {
            "stage": "熱延後" if source.startswith("熱延") else "焼鈍後",
            "test_type": source,
            "property": property_name,
            "count": len(values),
            "min": min(values),
            "mean": float(fmean(values)),
            "std": float(pstdev(values)),
            "median": float(median(values)),
            "max": max(values),
            "observations": grouped_observations[(source, property_name)],
        }
        for (source, property_name), values in sorted(grouped_values.items())
    ]
    primary_conditions = {
        column: _scalar(value)
        for column, value in source_row.items()
        if column != entity_type and not column.endswith("_key") and column not in METADATA_COLUMNS and value is not None
    }
    return {
        "key": entity_key,
        "entity_type": entity_type.removesuffix("_key"),
        "source_sheet": source_sheet,
        "source_row": {column: _scalar(value) for column, value in source_row.items()},
        "primary_conditions": primary_conditions,
        "composition": composition,
        "heat_pattern": heat_pattern,
        "connected_observation_count": observation_count,
        "connected_observations": connected_observations,
        "observation_groups": observation_groups,
        "property_summary": property_summary,
        "related_entities": relations,
        "missing_source": missing_source,
    }


LINEAGE_STAGE_ORDER = {
    "溶製_key": 0,
    "熱延_key": 1,
    "熱延引張_key": 2,
    "熱延組織_key": 2,
    "冷延_key": 3,
    "焼鈍_key": 4,
    "焼鈍引張_key": 5,
    "焼鈍穴広げ_key": 5,
    "焼鈍組織_key": 5,
}

LINEAGE_ADJACENCIES = (
    ("溶製_key", "熱延_key"),
    ("熱延_key", "熱延引張_key"),
    ("熱延_key", "熱延組織_key"),
    ("熱延_key", "冷延_key"),
    # Some valid routes omit cold rolling. Preserve that explicit skip as a
    # direct evidence edge instead of visually disconnecting annealing.
    ("熱延_key", "焼鈍_key"),
    ("冷延_key", "焼鈍_key"),
    ("焼鈍_key", "焼鈍引張_key"),
    ("焼鈍_key", "焼鈍穴広げ_key"),
    ("焼鈍_key", "焼鈍組織_key"),
)


def lineage_neighborhood(data: WorkbookData, entity_key: str, max_nodes: int = 80) -> dict[str, Any]:
    """Build a bounded route graph while preserving evidence from relation rows."""
    if entity_key not in data.lineage:
        raise KeyError(entity_key)
    route_rows: list[tuple[int, dict[str, Any]]] = []
    for row_number, row in enumerate(data.sheets["relation"], start=2):
        keys = {str(value) for column, value in row.items() if column.endswith("_key") and value}
        if entity_key in keys:
            route_rows.append((row_number, row))

    candidate_nodes: dict[str, str] = {}
    if not route_rows:
        own_type = next((column for column, records in data.entities.items() if entity_key in records), "")
        if own_type:
            candidate_nodes[entity_key] = own_type
    for _, row in route_rows:
        for column, value in row.items():
            if column in LINEAGE_STAGE_ORDER and value:
                candidate_nodes[str(value)] = column

    ordered = sorted(
        candidate_nodes.items(),
        key=lambda item: (LINEAGE_STAGE_ORDER.get(item[1], 99), item[0] != entity_key, item[0]),
    )
    visible = dict(ordered[:max_nodes])
    issue_by_key: dict[str, list[str]] = defaultdict(list)
    for issue in data.detected_quality:
        if issue["entity_key"] in visible and issue["issue_type"] not in issue_by_key[issue["entity_key"]]:
            issue_by_key[issue["entity_key"]].append(issue["issue_type"])
    nodes = [
        {
            "key": key,
            "entity_type": column.removesuffix("_key"),
            "source_sheet": KEY_TO_SHEET.get(column, column.removesuffix("_key")),
            "exists": key in data.entities.get(column, {}),
            "selected": key == entity_key,
            "issue_types": sorted(issue_by_key.get(key, [])),
        }
        for key, column in visible.items()
    ]
    edge_rows: dict[tuple[str, str], list[int]] = defaultdict(list)
    for row_number, row in route_rows:
        for source_column, target_column in LINEAGE_ADJACENCIES:
            source, target = row.get(source_column), row.get(target_column)
            if source and target and str(source) in visible and str(target) in visible:
                pair = (str(source), str(target))
                if row_number not in edge_rows[pair]:
                    edge_rows[pair].append(row_number)
    return {
        "nodes": nodes,
        "edges": [
            {"source": source, "target": target, "route_rows": rows}
            for (source, target), rows in sorted(edge_rows.items())
        ],
        "relation_row_count": len(route_rows),
        "omitted_node_count": max(0, len(ordered) - len(visible)),
    }


def _normalize_stage_local_times(points: list[dict[str, Any]]) -> None:
    """Stitch stage-local clock resets without adding the new stage's initial timestamp."""
    stage_raw_origin = 0.0
    stage_normalized_origin = 0.0
    previous_raw = 0.0
    previous_normalized = -1.0
    for point in points:
        raw = point["time_s"]
        if previous_normalized >= 0 and raw < previous_raw:
            stage_raw_origin = raw
            stage_normalized_origin = previous_normalized + 1e-6
            point["segment_start"] = True
        normalized = stage_normalized_origin + (raw - stage_raw_origin)
        point["time_s"] = max(normalized, previous_normalized + 1e-6)
        previous_raw = raw
        previous_normalized = point["time_s"]


def load_workbook_data(path: str | Path) -> WorkbookData:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Excel source not found: {path}")
    wb = load_workbook(path, read_only=True, data_only=True)
    sheets = {name: _records(wb[name]) for name in wb.sheetnames}
    entities: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for sheet_name, key_column in ENTITY_SHEETS.items():
        for row in sheets[sheet_name]:
            value = row.get(key_column)
            if value is not None and str(value).strip():
                # Keep the first row for node inspection. Duplicate rows are
                # independently surfaced by the quality detector below.
                entities[key_column].setdefault(str(value), row)
    melt_rows = sheets["溶製"]
    composition = {
        str(row["溶製_key"]): {short: float(row[column]) for short, column in COMPOSITION_COLUMNS.items() if row.get(column) is not None}
        for row in melt_rows
    }
    feature_rows = sheets["焼鈍特徴量"]
    anneal_history_by_key: dict[str, list[dict[str, float]]] = defaultdict(list)
    for row in sorted(sheets["焼鈍履歴"], key=lambda item: (str(item.get("焼鈍_key", "")), item.get("順番", 0))):
        if isinstance(row.get("到達時間[s]"), (int, float)) and isinstance(row.get("実績温度[℃]"), (int, float)):
            anneal_history_by_key[str(row["焼鈍_key"])].append({"time_s": float(row["到達時間[s]"]), "temperature_c": float(row["実績温度[℃]"])})
    # Some multi-stage routes restart their stage-local clock. Preserve the
    # recorded point order and stitch each reset after the preceding stage so
    # the canonical route remains strictly monotonic without reordering heat events.
    for key, points in anneal_history_by_key.items():
        _normalize_stage_local_times(points)
    anneal_features = {
        str(row["焼鈍_key"]): {
            "line_speed_m_min": float(row["ライン速度[m/min]"]),
            "max_temperature_c": float(row["最高実績温度[℃]"]),
            "hold_time_s": float(row["高温保持時間[s]"]),
            "coating": str(row["メッキ区分"]),
            "input_points": float(row["入力点数"]),
            "reheat": 1.0 if row["再加熱工程あり"] == "あり" else 0.0,
            "alloying": 1.0 if row["合金化工程あり"] == "通過" else 0.0,
            "feature_status": str(row["特徴量化判定"]),
            "heat_pattern": anneal_history_by_key.get(str(row["焼鈍_key"]), []),
        }
        for row in feature_rows
    }
    lineage: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for edge in sheets["relation"]:
        for column, value in edge.items():
            if column.endswith("_key") and value:
                lineage[str(value)][column].append(str(value))
        anchor = {key: str(value) for key, value in edge.items() if key.endswith("_key") and value}
        for current_key in anchor.values():
            bucket = lineage[current_key]
            for related_column, related_key in anchor.items():
                if related_key not in bucket[related_column]:
                    bucket[related_column].append(related_key)
    # Ensure every source entity can be inspected, including isolated tests and
    # process conditions. Relation references without source rows already exist
    # in lineage and are exposed as missing-source nodes.
    for records in entities.values():
        for key in records:
            lineage.setdefault(key, defaultdict(list))

    def upstream_composition(parent_key: str) -> dict[str, float] | None:
        melt_keys = sorted(set(lineage.get(parent_key, {}).get("溶製_key", [])))
        return composition.get(melt_keys[0]) if len(melt_keys) == 1 else None

    anneal_status = {str(row["焼鈍_key"]): str(row.get("学習利用区分", "")) for row in sheets["焼鈍"]}
    hot_status = {str(row["熱延_key"]): str(row.get("学習利用区分", "")) for row in sheets["熱延"]}
    observations: list[dict[str, Any]] = []
    targets = (("焼鈍引張", "焼鈍引張_key", ("TS[MPa]", "YS[MPa]", "EL[%]")), ("焼鈍穴広げ", "焼鈍穴広げ_key", ("λ[%]",)), ("熱延引張", "熱延引張_key", ("TS[MPa]", "YS[MPa]", "EL[%]")))
    for sheet_name, observation_key, output_columns in targets:
        for row in sheets[sheet_name]:
            parent = str(row.get("反復条件_key", ""))
            is_anneal = sheet_name != "熱延引張"
            process = anneal_features.get(parent) if is_anneal else None
            comp = upstream_composition(parent)
            outputs = {name: float(row[name]) for name in output_columns if isinstance(row.get(name), (int, float))}
            if not outputs:
                continue
            observations.append({
                "id": str(row[observation_key]), "source": sheet_name, "parent_key": parent,
                "features": process, "composition": comp, "outputs": outputs,
                "eligible": bool(process and comp and process["feature_status"] == "特徴量化可" and anneal_status.get(parent) == "学習" and row.get("判定") == "有効") if is_anneal else bool(comp and hot_status.get(parent) == "学習" and row.get("判定") == "有効"),
                "thickness_mm": float(row.get("板厚[mm]", 0) or 0), "date": _as_date(row.get("試験日")),
            })
    values = {name: sorted(float(row[name]) for row in melt_rows if isinstance(row.get(name), (int, float))) for name in COMPOSITION_COLUMNS.values()}
    medians = {short: series[len(series) // 2] for short, name in COMPOSITION_COLUMNS.items() if (series := values[name])}
    with path.open("rb") as source_file:
        source_sha256 = hashlib.file_digest(source_file, "sha256").hexdigest()
    normalized_lineage = {k: {inner: sorted(set(values)) for inner, values in v.items()} for k, v in lineage.items()}
    detected_quality = _detect_data_quality(sheets, entities)
    return WorkbookData(str(path), path.stat().st_mtime_ns, source_sha256, sheets, composition, anneal_features, normalized_lineage, observations, sheets["想定異常"], detected_quality, dict(entities), medians)
