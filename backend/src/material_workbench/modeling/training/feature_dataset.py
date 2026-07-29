from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class TargetTrainingSet:
    target: str
    unit: str
    target_kind: str
    feature_names: tuple[str, ...]
    x: np.ndarray
    y: np.ndarray
    replicate_contexts: tuple[str, ...]
    validation_groups: tuple[str, ...]
    observation_ids: tuple[tuple[str, ...], ...]
    repeat_counts: tuple[int, ...]
    observation_variance: float


def _replicate_context(row: Mapping[str, Any]) -> str:
    value = row.get("condition_context_id") or row.get("observation_id")
    if value is None or not str(value).strip():
        raise ValueError("canonical training row has no replicate context")
    return str(value)


def compile_target_training_set(
    canonical_dataset: Mapping[str, Any],
    *,
    target: str,
    unit: str,
    target_kind: str = "continuous",
) -> TargetTrainingSet:
    feature_names = tuple(
        str(item["name"])
        for item in canonical_dataset["feature_pipeline"]["features"]
    )
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in canonical_dataset["rows"]:
        if target in row["outputs"]:
            grouped.setdefault(_replicate_context(row), []).append(row)
    if len(grouped) < 3:
        raise ValueError(f"{target}: at least three training contexts are required")

    x_rows: list[np.ndarray] = []
    y_rows: list[float] = []
    replicate_contexts: list[str] = []
    validation_groups: list[str] = []
    observation_ids: list[tuple[str, ...]] = []
    repeat_counts: list[int] = []
    within_sse = 0.0
    within_df = 0
    for replicate_context, rows in sorted(grouped.items()):
        parent_keys = {
            str(row["parent_key"])
            for row in rows
            if str(row.get("parent_key", "")).strip()
        }
        if len(parent_keys) != 1:
            raise ValueError(
                f"{target}/{replicate_context}: replicate context must belong "
                "to one validation group"
            )
        feature_rows = np.asarray(
            [
                [float(row["features"][name]) for name in feature_names]
                for row in rows
            ],
            dtype=float,
        )
        if not np.isfinite(feature_rows).all():
            raise ValueError(
                f"{target}/{replicate_context}: features must be finite"
            )
        if not np.allclose(feature_rows, feature_rows[0], rtol=1e-10, atol=1e-12):
            raise ValueError(
                f"{target}/{replicate_context}: one replicate context has "
                "different feature rows"
            )
        values = np.asarray(
            [float(row["outputs"][target]) for row in rows],
            dtype=float,
        )
        if not np.isfinite(values).all():
            raise ValueError(
                f"{target}/{replicate_context}: outputs must be finite"
            )
        x_rows.append(feature_rows[0])
        y_rows.append(float(values.mean()))
        replicate_contexts.append(replicate_context)
        validation_groups.append(next(iter(parent_keys)))
        observation_ids.append(tuple(str(row["observation_id"]) for row in rows))
        repeat_counts.append(len(values))
        if len(values) > 1:
            within_sse += float(np.sum((values - values.mean()) ** 2))
            within_df += len(values) - 1

    y = np.asarray(y_rows, dtype=float)
    fallback = max(float(np.var(y)) * 0.1, 1e-8)
    observation_variance = (
        max(within_sse / within_df, 1e-8)
        if within_df
        else fallback
    )
    return TargetTrainingSet(
        target=target,
        unit=unit,
        target_kind=target_kind,
        feature_names=feature_names,
        x=np.vstack(x_rows),
        y=y,
        replicate_contexts=tuple(replicate_contexts),
        validation_groups=tuple(validation_groups),
        observation_ids=tuple(observation_ids),
        repeat_counts=tuple(repeat_counts),
        observation_variance=observation_variance,
    )


def feature_vector(
    feature_names: Sequence[str],
    row: Mapping[str, Any],
) -> np.ndarray:
    values = np.asarray([float(row[name]) for name in feature_names], dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("smoke feature row must be finite")
    return values
