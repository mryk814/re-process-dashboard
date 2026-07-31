"""Allow-listed semantic normalization and separate feature representations."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime

import numpy as np

from decision_workbench.contracts.series_contracts import (
    CanonicalSeriesPoint,
    CanonicalSeriesRevision,
    RawSeriesAsset,
    SeriesFeatureContract,
    SeriesFeaturePreview,
    SeriesNormalizationRecipe,
    SeriesQualityFinding,
)
from decision_workbench.execution.inference_work_graph import semantic_digest


class SeriesCurationError(ValueError):
    pass


def _coordinate_converter(source: str, target: str):
    aliases = {
        "sec": "s",
        "second": "s",
        "seconds": "s",
        "minute": "min",
        "minutes": "min",
        "hour": "h",
        "hours": "h",
        "cycles": "cycle",
    }
    source = aliases.get(source.strip().lower(), source.strip())
    target = aliases.get(target.strip().lower(), target.strip())
    seconds = {"s": 1.0, "min": 60.0, "h": 3600.0}
    if source in seconds and target in seconds:
        factor = seconds[source] / seconds[target]
        return lambda value: value * factor
    if source == target:
        return lambda value: value
    raise SeriesCurationError(
        f"座標単位を変換できません: {source} → {target}"
    )


def _value_converter(source: str, target: str):
    aliases = {
        "c": "°C",
        "degc": "°C",
        "celsius": "°C",
        "kelvin": "K",
        "percent": "%",
    }
    source = aliases.get(source.strip().lower(), source.strip())
    target = aliases.get(target.strip().lower(), target.strip())
    if source == target:
        return lambda value: value
    if source == "K" and target == "°C":
        return lambda value: value - 273.15
    if source == "°C" and target == "K":
        return lambda value: value + 273.15
    if source == "mV" and target == "V":
        return lambda value: value / 1000.0
    if source == "V" and target == "mV":
        return lambda value: value * 1000.0
    raise SeriesCurationError(f"値単位を変換できません: {source} → {target}")


def _blocked_revision(
    raw: RawSeriesAsset,
    recipe: SeriesNormalizationRecipe,
    *,
    status: str,
    coordinate_unit: str,
    value_unit: str,
    findings: list[SeriesQualityFinding],
    log: list[str],
) -> CanonicalSeriesRevision:
    payload = {
        "raw_content_digest": raw.content_digest,
        "recipe_digest": recipe.digest,
        "status": status,
        "coordinate_name": raw.coordinate_name,
        "coordinate_unit": coordinate_unit,
        "value_name": raw.value_name,
        "value_unit": value_unit,
        "points": [],
        "findings": [item.model_dump(mode="json") for item in findings],
        "transformation_log": tuple(log),
    }
    return CanonicalSeriesRevision(
        id=f"canonical-series-{semantic_digest(payload).removeprefix('sha256:')[:24]}",
        raw_series_id=raw.id,
        raw_content_digest=raw.content_digest,
        recipe=recipe,
        recipe_digest=recipe.digest,
        status=status,  # type: ignore[arg-type]
        coordinate_name=raw.coordinate_name,
        coordinate_unit=coordinate_unit,
        value_name=raw.value_name,
        value_unit=value_unit,
        points=(),
        findings=tuple(findings),
        transformation_log=tuple(log),
        canonical_digest=semantic_digest(payload),
        created_at=datetime.now(UTC),
    )


def canonicalize_series(
    raw: RawSeriesAsset,
    recipe: SeriesNormalizationRecipe,
) -> CanonicalSeriesRevision:
    """Normalize meaning-preserving details only; never interpolate or smooth."""

    coordinate_unit = raw.coordinate_unit
    value_unit = raw.value_unit
    rows = [
        {
            "coordinate": point.coordinate,
            "value": point.value,
            "channel": point.channel,
            "positions": (point.source_position,),
        }
        for point in raw.points
    ]
    findings: list[SeriesQualityFinding] = []
    log: list[str] = []
    step_kinds = {step.kind for step in recipe.steps}

    try:
        for step in recipe.steps:
            if step.kind == "convert_coordinate_unit":
                converter = _coordinate_converter(coordinate_unit, step.to_unit)
                for row in rows:
                    row["coordinate"] = converter(float(row["coordinate"]))
                log.append(f"coordinate unit: {coordinate_unit} → {step.to_unit}")
                coordinate_unit = step.to_unit
            elif step.kind == "convert_value_unit":
                converter = _value_converter(value_unit, step.to_unit)
                for row in rows:
                    row["value"] = converter(float(row["value"]))
                log.append(f"value unit: {value_unit} → {step.to_unit}")
                value_unit = step.to_unit
            elif step.kind == "elapsed_origin":
                origins = {
                    channel: min(
                        float(row["coordinate"])
                        for row in rows
                        if row["channel"] == channel
                    )
                    for channel in {str(row["channel"]) for row in rows}
                }
                for row in rows:
                    row["coordinate"] = (
                        float(row["coordinate"]) - origins[str(row["channel"])]
                    )
                log.append("channelごとの先頭座標を0へ移動")
            elif step.kind == "stable_sort":
                rows.sort(
                    key=lambda row: (
                        str(row["channel"]),
                        float(row["coordinate"]),
                        int(tuple(row["positions"])[0]),
                    )
                )
                log.append("channel・座標・source positionでstable sort")
    except SeriesCurationError as exc:
        reason = (
            "unsupported_coordinate_unit"
            if "座標単位" in str(exc)
            else "unsupported_value_unit"
        )
        findings.append(
            SeriesQualityFinding(
                severity="blocked",
                reason_code=reason,  # type: ignore[arg-type]
                message=str(exc),
            )
        )
        return _blocked_revision(
            raw,
            recipe,
            status="blocked",
            coordinate_unit=coordinate_unit,
            value_unit=value_unit,
            findings=findings,
            log=log,
        )

    original_by_channel: dict[str, list[float]] = defaultdict(list)
    for point in raw.points:
        original_by_channel[point.channel].append(point.coordinate)
    out_of_order = any(
        later < earlier
        for values in original_by_channel.values()
        for earlier, later in zip(values, values[1:])
    )
    if out_of_order:
        severity = "info" if "stable_sort" in step_kinds else "blocked"
        findings.append(
            SeriesQualityFinding(
                severity=severity,
                reason_code="coordinate_out_of_order",
                message=(
                    "source順を保持したrawは降順を含みます。stable sortで正規化しました。"
                    if severity == "info"
                    else "座標が昇順ではありません。stable sortをRecipeへ追加してください。"
                ),
            )
        )
        if severity == "blocked":
            return _blocked_revision(
                raw,
                recipe,
                status="blocked",
                coordinate_unit=coordinate_unit,
                value_unit=value_unit,
                findings=findings,
                log=log,
            )

    grouped: dict[tuple[str, float], list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row["channel"]), float(row["coordinate"]))].append(row)
    merged: list[dict[str, object]] = []
    for (channel, coordinate), duplicates in grouped.items():
        values = [float(item["value"]) for item in duplicates]
        positions = tuple(
            position
            for item in duplicates
            for position in tuple(item["positions"])
        )
        if len(duplicates) > 1 and not all(
            math.isclose(value, values[0], rel_tol=0, abs_tol=1e-12)
            for value in values[1:]
        ):
            findings.append(
                SeriesQualityFinding(
                    severity="quarantined",
                    reason_code="conflicting_duplicate",
                    message=(
                        f"{channel}の座標{coordinate:g}に矛盾する値があります。"
                        "rawを修正せず隔離しました。"
                    ),
                    source_positions=positions,
                )
            )
            continue
        if len(duplicates) > 1:
            severity = (
                "info"
                if "merge_identical_duplicates" in step_kinds
                else "blocked"
            )
            findings.append(
                SeriesQualityFinding(
                    severity=severity,
                    reason_code="identical_duplicate",
                    message=(
                        "完全同一の重複点をsource position付きで統合しました。"
                        if severity == "info"
                        else "完全同一の重複点があります。重複統合をRecipeへ追加してください。"
                    ),
                    source_positions=positions,
                )
            )
            if severity == "blocked":
                continue
        merged.append(
            {
                "channel": channel,
                "coordinate": coordinate,
                "value": values[0],
                "positions": positions,
            }
        )
    if any(item.severity == "quarantined" for item in findings):
        return _blocked_revision(
            raw,
            recipe,
            status="quarantined",
            coordinate_unit=coordinate_unit,
            value_unit=value_unit,
            findings=findings,
            log=log,
        )
    if any(item.severity == "blocked" for item in findings):
        return _blocked_revision(
            raw,
            recipe,
            status="blocked",
            coordinate_unit=coordinate_unit,
            value_unit=value_unit,
            findings=findings,
            log=log,
        )

    counts: dict[str, int] = defaultdict(int)
    for row in merged:
        counts[str(row["channel"])] += 1
    too_short = sorted(channel for channel, count in counts.items() if count < 2)
    if not merged or too_short:
        findings.append(
            SeriesQualityFinding(
                severity="blocked",
                reason_code="too_few_points",
                message=(
                    "系列はchannelごとに2点以上必要です: "
                    + ", ".join(too_short or ["pointsなし"])
                ),
            )
        )
        return _blocked_revision(
            raw,
            recipe,
            status="blocked",
            coordinate_unit=coordinate_unit,
            value_unit=value_unit,
            findings=findings,
            log=log,
        )

    merged.sort(key=lambda item: (str(item["channel"]), float(item["coordinate"])))
    points = tuple(
        CanonicalSeriesPoint(
            coordinate=float(item["coordinate"]),
            value=float(item["value"]),
            channel=str(item["channel"]),
            source_positions=tuple(item["positions"]),
        )
        for item in merged
    )
    status = "normalized" if log or findings else "accepted"
    payload = {
        "raw_content_digest": raw.content_digest,
        "recipe_digest": recipe.digest,
        "status": status,
        "coordinate_name": raw.coordinate_name,
        "coordinate_unit": coordinate_unit,
        "value_name": raw.value_name,
        "value_unit": value_unit,
        "points": [item.model_dump(mode="json") for item in points],
        "findings": [item.model_dump(mode="json") for item in findings],
        "transformation_log": tuple(log),
    }
    return CanonicalSeriesRevision(
        id=f"canonical-series-{semantic_digest(payload).removeprefix('sha256:')[:24]}",
        raw_series_id=raw.id,
        raw_content_digest=raw.content_digest,
        recipe=recipe,
        recipe_digest=recipe.digest,
        status=status,
        coordinate_name=raw.coordinate_name,
        coordinate_unit=coordinate_unit,
        value_name=raw.value_name,
        value_unit=value_unit,
        points=points,
        findings=tuple(findings),
        transformation_log=tuple(log),
        canonical_digest=semantic_digest(payload),
        created_at=datetime.now(UTC),
    )


def build_series_features(
    series: CanonicalSeriesRevision,
    contract: SeriesFeatureContract,
) -> SeriesFeaturePreview:
    if series.status in {"quarantined", "blocked"}:
        raise SeriesCurationError("隔離・blocked系列は特徴量へ変換できません")
    grouped: dict[str, list[CanonicalSeriesPoint]] = defaultdict(list)
    for point in series.points:
        grouped[point.channel].append(point)
    names: list[str] = []
    values: list[float] = []
    shape: tuple[int, ...]
    if contract.representation_id == "linear_resample_v1":
        assert contract.sample_count is not None
        for channel in sorted(grouped):
            points = grouped[channel]
            coordinates = np.array([item.coordinate for item in points], dtype=float)
            measured = np.array([item.value for item in points], dtype=float)
            grid = np.linspace(coordinates[0], coordinates[-1], contract.sample_count)
            resampled = np.interp(grid, coordinates, measured)
            for index, (coordinate, value) in enumerate(
                zip(grid, resampled, strict=True)
            ):
                if contract.include_coordinate:
                    names.append(f"{channel}.coordinate.{index}")
                    values.append(float(coordinate))
                names.append(f"{channel}.value.{index}")
                values.append(float(value))
        columns = contract.sample_count * (2 if contract.include_coordinate else 1)
        shape = (len(grouped), columns)
    elif contract.representation_id == "segment_statistics_v1":
        for channel in sorted(grouped):
            points = grouped[channel]
            x = np.array([item.coordinate for item in points], dtype=float)
            y = np.array([item.value for item in points], dtype=float)
            width = x[-1] - x[0]
            statistics = {
                "min": float(np.min(y)),
                "max": float(np.max(y)),
                "mean": float(np.mean(y)),
                "slope": float((y[-1] - y[0]) / width) if width else 0.0,
                "area": float(np.trapezoid(y, x)),
            }
            for name, value in statistics.items():
                names.append(f"{channel}.{name}")
                values.append(value)
        shape = (len(grouped), 5)
    else:
        for channel in sorted(grouped):
            for index, point in enumerate(grouped[channel]):
                if contract.include_coordinate:
                    names.append(f"{channel}.coordinate.{index}")
                    values.append(point.coordinate)
                names.append(f"{channel}.value.{index}")
                values.append(point.value)
        shape = (
            len(series.points),
            2 if contract.include_coordinate else 1,
        )
    contract_digest = semantic_digest(contract.model_dump(mode="json"))
    return SeriesFeaturePreview(
        canonical_series_id=series.id,
        canonical_digest=series.canonical_digest,
        feature_contract=contract,
        feature_contract_digest=contract_digest,
        feature_names=tuple(names),
        values=tuple(values),
        shape=shape,
    )
