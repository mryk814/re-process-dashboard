"""Immutable CSV materialization and curation for tabular regression tasks."""
from __future__ import annotations

import csv
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from statistics import median
from typing import Any, Literal

from .profile import CurationColumnRule, TabularDatasetProfile, load_tabular_profile


@dataclass(frozen=True)
class TabularData:
    source_path: str
    source_mtime_ns: int
    source_sha256: str
    profile_path: str
    profile: TabularDatasetProfile
    profile_id: str
    observations: list[dict[str, Any]]
    medians: dict[str, float]
    measurement_labels: dict[str, str]
    row_count: int
    quality: list[dict[str, Any]]
    detected_quality: list[dict[str, Any]]
    technical_columns: dict[tuple[str, str], str]
    lifecycle_profile: Any | None = None

_SCALAR_RE = re.compile(
    r"^\s*(?P<value>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)"
    r"(?:\s*±\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?)?"
    r"\s*(?P<unit>[^0-9\s].*?)?\s*$"
)


def _reported_state(raw: str) -> Literal["active", "inactive", "unknown"]:
    normalized = raw.strip().casefold()
    if not normalized or normalized in {"n/a", "na", "unknown", "不明"}:
        return "unknown"
    if normalized in {"0", "no", "none", "false"}:
        return "inactive"
    return "active"


def _reported(raw: str) -> bool:
    return _reported_state(raw) == "active"


def _curate_value(text: str, rule: CurationColumnRule) -> tuple[float | str, str | None]:
    raw = text.strip()
    if rule.parser == "reported_flag":
        return ("yes" if _reported(raw) else "no"), None
    match = _SCALAR_RE.fullmatch(raw)
    if match is None:
        raise ValueError("単一の数値として解釈できません")
    value = float(match.group("value"))
    if not math.isfinite(value):
        raise ValueError("有限数ではありません")
    unit = (match.group("unit") or "").strip().casefold().replace(" ", "")
    warning = "±表記の中心値を採用しました" if "±" in raw else None
    if rule.parser == "finite_number":
        if unit:
            raise ValueError("単位なしの数値が必要です")
    elif rule.parser == "temperature_c":
        if unit in {"k", "kelvin"}:
            value -= 273.15
        elif unit not in {"", "°c", "℃", "c"}:
            raise ValueError("温度単位を解釈できません")
    elif rule.parser in {"duration_hours", "duration_minutes"}:
        hour_units = {"h", "hr", "hrs", "hour", "hours"}
        minute_units = {"min", "mins", "minute", "minutes"}
        if unit in hour_units:
            value = value if rule.parser == "duration_hours" else value * 60
        elif unit in minute_units:
            value = value / 60 if rule.parser == "duration_hours" else value
        elif unit:
            raise ValueError("時間単位を解釈できません")
    elif rule.parser == "percentage":
        if unit not in {"", "%", "percent", "at.%", "at%"}:
            raise ValueError("割合の単位を解釈できません")
    elif rule.parser == "reported_scalar":
        normalized = tuple(item.casefold().replace(" ", "") for item in rule.allowed_units)
        if normalized and unit not in normalized:
            raise ValueError("許可されていない単位です")
    if rule.reject_below is not None and value < rule.reject_below:
        raise ValueError(f"{value:g} は採用下限 {rule.reject_below:g} 未満です")
    if rule.reject_above is not None and value > rule.reject_above:
        raise ValueError(f"{value:g} は採用上限 {rule.reject_above:g} を超えます")
    if rule.warn_below is not None and value < rule.warn_below:
        warning = f"要確認: {value:g} は通常の下限 {rule.warn_below:g} 未満です"
    if rule.warn_above is not None and value > rule.warn_above:
        warning = f"要確認: {value:g} は通常の上限 {rule.warn_above:g} を超えます"
    return value, warning


def load_tabular_data(
    path: str | Path,
    profile_path: str | Path | TabularDatasetProfile,
    *,
    profile_locator: str | Path | None = None,
) -> TabularData:
    source = Path(path)
    if isinstance(profile_path, TabularDatasetProfile):
        profile = profile_path
        selected_profile_locator = (
            str(profile_locator)
            if profile_locator is not None
            else f"catalog:{profile.profile_id}"
        )
    else:
        profile_file = Path(profile_path)
        profile = load_tabular_profile(profile_file)
        selected_profile_locator = str(profile_locator or profile_file)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    observations: list[dict[str, Any]] = []
    numeric_series: dict[str, list[float]] = {
        item.path: [] for item in profile.inputs if item.kind == "number"
    }
    quality_values: dict[str, list[float]] = {
        rule.column: [] for rule in profile.quality_rules
    }
    quality_records: dict[str, list[tuple[str, str, float]]] = {
        rule.column: [] for rule in profile.quality_rules
    }
    curve_axis_column = next(
        (
            item.column
            for item in profile.inputs
            if item.path == profile.curve_axis_path
        ),
        None,
    )
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        for _ in range(profile.curation_recipe.header_row_index if profile.curation_recipe else 0):
            next(stream, None)
        reader = csv.DictReader(stream)
        required = {
            *(item.column for item in profile.inputs),
            *(item.column for item in profile.outputs),
            *(rule.column for rule in profile.quality_rules),
        }
        required.update(
            column for column in (profile.id_column, profile.group_column) if column is not None
        )
        missing = sorted(required - set(reader.fieldnames or ()))
        if missing:
            raise ValueError(f"CSVにProfile必須列がありません: {', '.join(missing)}")
        header_offset = (profile.curation_recipe.header_row_index if profile.curation_recipe else 0) + 2
        for index, raw in enumerate(reader, start=header_offset):
            input_reasons: list[str] = []
            warnings: list[str] = []
            curation_errors: dict[str, str] = {}
            curated: dict[str, float | str] = {}
            if profile.curation_recipe is not None:
                for column, rule in profile.curation_recipe.columns.items():
                    source_text = raw.get(column) or ""
                    if rule.condition_column is not None:
                        condition_state = _reported_state(raw.get(rule.condition_column) or "")
                        if condition_state == "inactive":
                            curated[column] = rule.inactive_value  # type: ignore[assignment]
                            continue
                        if condition_state == "unknown":
                            curated[column] = source_text.strip()
                            input_reasons.append(f"{rule.condition_column}: 工程の実施有無が不明です")
                            continue
                    try:
                        curated[column], warning = _curate_value(source_text, rule)
                        if warning:
                            warnings.append(f"{column}: {warning}")
                    except ValueError as exc:
                        curated[column] = source_text.strip()
                        curation_errors[column] = str(exc)
                        if column in {item.column for item in profile.inputs}:
                            input_reasons.append(f"{column}: {exc}")
            composition: dict[str, float] = {}
            process: dict[str, float] = {}
            categorical: dict[str, str] = {}
            for item in profile.inputs:
                value_or_text = curated.get(item.column, (raw.get(item.column) or "").strip())
                group, key = item.path.split(".", 1)
                if item.kind == "number":
                    try:
                        value = float(value_or_text)
                        if not math.isfinite(value):
                            raise ValueError
                    except ValueError:
                        if not any(reason.startswith(f"{item.column}:") for reason in input_reasons):
                            input_reasons.append(f"{item.column}が有限数ではありません")
                        continue
                    (composition if group == "composition" else process)[key] = value
                    numeric_series[item.path].append(value)
                elif str(value_or_text) not in item.choices:
                    input_reasons.append(f"{item.column}が定義済み区分ではありません")
                else:
                    categorical[key] = str(value_or_text)
            if profile.curation_recipe is not None and not input_reasons:
                for rule in profile.curation_recipe.sum_rules:
                    total = sum(float(curated[column]) for column in rule.columns)
                    if not rule.minimum <= total <= rule.maximum:
                        input_reasons.append(
                            f"{rule.label}が許容範囲外です（{total:g}; {rule.minimum:g}–{rule.maximum:g}）"
                        )
            outputs: dict[str, float] = {}
            target_status: dict[str, dict[str, Any]] = {}
            for item in profile.outputs:
                try:
                    value = float(curated.get(item.column, (raw.get(item.column) or "").strip()))
                    if not math.isfinite(value):
                        raise ValueError
                    if item.lower_bound is not None and value < item.lower_bound:
                        raise ValueError
                    if item.upper_bound is not None and value > item.upper_bound:
                        raise ValueError
                    outputs[item.key] = value
                    target_status[item.key] = {"usable": True, "reason": None}
                except ValueError:
                    raw_target = (raw.get(item.column) or "").strip()
                    reason = (
                        "欠損"
                        if not raw_target
                        else curation_errors.get(item.column, "値がProfileの採用範囲外です")
                    )
                    target_status[item.key] = {"usable": False, "reason": reason}
            for column, values in quality_values.items():
                try:
                    value = float((raw.get(column) or "").strip())
                    if math.isfinite(value):
                        values.append(value)
                except ValueError:
                    continue
            observation_id = (
                (raw.get(profile.id_column) or "").strip()
                if profile.id_column is not None
                else ""
            ) or f"row-{index}"
            group_id = (
                (raw.get(profile.group_column) or "").strip()
                if profile.group_column is not None
                else ""
            ) or observation_id
            for column in quality_records:
                try:
                    quality_value = float((raw.get(column) or "").strip())
                except ValueError:
                    continue
                axis_value = (raw.get(curve_axis_column) or "").strip() if curve_axis_column else ""
                quality_records[column].append((group_id, axis_value, quality_value))
            eligible = not input_reasons and bool(outputs)
            status = (
                "quarantined"
                if input_reasons
                else "warning"
                if warnings
                else "accepted"
            )
            observations.append({
                "id": f"{observation_id}:{index}",
                "task_id": profile.task_id,
                "source": source.name,
                "parent_key": group_id,
                "features": process,
                "composition": composition,
                "categorical": categorical,
                "outputs": outputs,
                "eligible": eligible,
                "eligible_targets": sorted(outputs),
                "eligibility_reasons": input_reasons or ([] if outputs else ["利用可能な目的変数がありません"]),
                "date": None,
                "measurement_order": index,
                "run_context": {
                    "group_key": group_id,
                    "curation": {
                        "recipe_id": profile.curation_recipe.id if profile.curation_recipe else None,
                        "status": status,
                        "reasons": input_reasons,
                        "warnings": warnings,
                        "target_status": target_status,
                        "values": {
                            column: {
                                "raw": (raw.get(column) or "").strip(),
                                "normalized": curated.get(
                                    column, (raw.get(column) or "").strip()
                                ),
                                "parser": profile.curation_recipe.columns[column].parser,
                                "conversion": (
                                    "inactive-stage-neutral"
                                    if profile.curation_recipe.columns[column].condition_column
                                    and _reported_state(
                                        raw.get(
                                            profile.curation_recipe.columns[column].condition_column
                                        ) or ""
                                    ) == "inactive"
                                    else profile.curation_recipe.columns[column].parser
                                ),
                            }
                            for column in (
                                profile.curation_recipe.columns
                                if profile.curation_recipe is not None
                                else ()
                            )
                        },
                    },
                },
            })
    medians = {
        path.split(".", 1)[1]: float(median(values))
        for path, values in numeric_series.items()
        if path.startswith("composition.") and values
    }
    detected_quality: list[dict[str, Any]] = []
    if profile.curation_recipe is not None:
        quarantined = [row for row in observations if row["run_context"]["curation"]["status"] == "quarantined"]
        if quarantined:
            detected_quality.append({
                "issue_id": f"tabular:{profile.task_id}:curation-quarantine",
                "issue_type": "curation_quarantine",
                "source_sheet": source.name,
                "entity_key": profile.curation_recipe.id,
                "detail": (
                    f"Curation Recipeにより{len(quarantined):,}/{len(observations):,}行を隔離しました。"
                    f"例: {', '.join(row['id'] for row in quarantined[:3])}。元データは変更していません。"
                ),
                "focus_entity_key": None, "related_entity_keys": [],
                "missing_reference_key": None, "suggested_view": "source_sheet",
            })
        for output in profile.outputs:
            missing = [row for row in observations if output.key not in row["outputs"]]
            if not missing:
                continue
            detected_quality.append({
                "issue_id": f"tabular:{profile.task_id}:missing-target:{output.key}",
                "issue_type": "missing_target",
                "source_sheet": source.name,
                "entity_key": output.column,
                "detail": (
                    f"{output.key}は{len(observations)-len(missing):,}行で値を解釈可能、"
                    f"{sum(row['eligible'] and output.key in row['outputs'] for row in observations):,}行で"
                    f"入力条件も満たし学習利用可能です。{len(missing):,}行は欠損または解釈不能です。"
                    "目的変数ごとに学習母集団を分けます。"
                ),
                "focus_entity_key": None, "related_entity_keys": [],
                "missing_reference_key": None, "suggested_view": "source_sheet",
            })
    for rule in profile.quality_rules:
        values = quality_values[rule.column]
        if not values:
            continue
        if rule.kind == "below_minimum":
            assert rule.minimum is not None
            affected = [value for value in values if value < rule.minimum]
            if not affected:
                continue
            affected_records = [
                record for record in quality_records[rule.column]
                if record[2] < rule.minimum
            ]
            examples = ", ".join(
                f"{group}@{axis or 'row'}={value:g}"
                for group, axis, value in affected_records[:3]
            )
            detail = (
                f"{rule.label}が下限{rule.minimum:g}未満の行が"
                f"{len(affected):,}/{len(values):,}件あります（最小{min(affected):g}）。"
                f"影響する親条件は{len({record[0] for record in affected_records}):,}件です。"
                f"例: {examples}。"
                "学習からは自動除外していません。"
            )
            issue_type = "out_of_range"
        else:
            assert rule.value is not None and rule.fraction is not None
            affected = [value for value in values if value == rule.value]
            share = len(affected) / len(values)
            if share < rule.fraction:
                continue
            affected_records = [
                record for record in quality_records[rule.column]
                if record[2] == rule.value
            ]
            examples = ", ".join(
                f"{group}@{axis or 'row'}={value:g}"
                for group, axis, value in affected_records[:3]
            )
            detail = (
                f"{rule.label}が{rule.value:g}に一致する行が"
                f"{len(affected):,}/{len(values):,}件（{share:.1%}）あります。"
                f"影響する親条件は{len({record[0] for record in affected_records}):,}件です。"
                f"例: {examples}。"
                "打ち切り・クリップ・測定限界の可能性を確認してください。"
                "学習からは自動除外していません。"
            )
            issue_type = "suspicious_distribution"
        detected_quality.append({
            "issue_id": f"tabular:{profile.task_id}:{rule.id}",
            "issue_type": issue_type,
            "source_sheet": source.name,
            "entity_key": rule.column,
            "detail": detail,
            "focus_entity_key": None,
            "related_entity_keys": [],
            "missing_reference_key": None,
            "suggested_view": "source_sheet",
        })
    return TabularData(
        source_path=str(source),
        source_mtime_ns=source.stat().st_mtime_ns,
        source_sha256=digest,
        profile_path=selected_profile_locator,
        profile=profile,
        profile_id=profile.profile_id,
        observations=observations,
        medians=medians,
        measurement_labels={item.key: item.key for item in profile.outputs},
        row_count=len(observations),
        quality=[],
        detected_quality=detected_quality,
        technical_columns={},
    )
