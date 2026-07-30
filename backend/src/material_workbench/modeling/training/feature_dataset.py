from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
    within_context_sse: np.ndarray
    within_context_df: np.ndarray
    observation_variance: float
    cohort_digest: str
    fold_assignments: tuple[tuple[str, int], ...]
    fold_ids: np.ndarray
    fold_digest: str
    folds: int


def _replicate_context(row: Mapping[str, Any]) -> str:
    value = row.get("condition_context_id") or row.get("observation_id")
    if value is None or not str(value).strip():
        raise ValueError("canonical training row has no replicate context")
    return str(value)


def _semantic_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _fold_order(group: str, *, seed: int) -> bytes:
    return hashlib.sha256(f"{seed}:{group}".encode("utf-8")).digest()


def _evaluation_identity(
    *,
    target: str,
    replicate_contexts: Sequence[str],
    validation_groups: Sequence[str],
    observation_ids: Sequence[Sequence[str]],
    requested_folds: int,
    seed: int,
) -> tuple[str, tuple[tuple[str, int], ...], np.ndarray, str, int]:
    unique_groups = sorted(
        set(validation_groups),
        key=lambda group: (_fold_order(group, seed=seed), group),
    )
    folds = min(requested_folds, len(unique_groups))
    if folds < 2:
        raise ValueError(
            f"{target}: at least two independent validation groups are required"
        )
    assignments = tuple(
        (group, index % folds)
        for index, group in enumerate(unique_groups)
    )
    assignment_by_group = dict(assignments)
    fold_ids = np.asarray(
        [assignment_by_group[group] for group in validation_groups],
        dtype=int,
    )
    cohort_digest = _semantic_digest({
        "target": target,
        "rows": [
            {
                "replicate_context": context,
                "validation_group": group,
                "observation_ids": list(ids),
            }
            for context, group, ids in zip(
                replicate_contexts,
                validation_groups,
                observation_ids,
                strict=True,
            )
        ],
    })
    fold_digest = _semantic_digest({
        "target": target,
        "folds": folds,
        "seed": seed,
        "assignments": [
            {"validation_group": group, "fold": fold}
            for group, fold in assignments
        ],
    })
    fold_ids.setflags(write=False)
    return cohort_digest, assignments, fold_ids, fold_digest, folds


def compile_target_training_set(
    canonical_dataset: Mapping[str, Any],
    *,
    target: str,
    unit: str,
    target_kind: str = "continuous",
    folds: int = 5,
    seed: int = 20260730,
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
    within_context_sse: list[float] = []
    within_context_df: list[int] = []
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
            context_sse = float(np.sum((values - values.mean()) ** 2))
            context_df = len(values) - 1
            within_sse += context_sse
            within_df += context_df
        else:
            context_sse = 0.0
            context_df = 0
        within_context_sse.append(context_sse)
        within_context_df.append(context_df)

    y = np.asarray(y_rows, dtype=float)
    fallback = max(float(np.var(y)) * 0.1, 1e-8)
    observation_variance = (
        max(within_sse / within_df, 1e-8)
        if within_df
        else fallback
    )
    (
        cohort_digest,
        fold_assignments,
        fold_ids,
        fold_digest,
        resolved_folds,
    ) = _evaluation_identity(
        target=target,
        replicate_contexts=replicate_contexts,
        validation_groups=validation_groups,
        observation_ids=observation_ids,
        requested_folds=folds,
        seed=seed,
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
        within_context_sse=np.asarray(within_context_sse, dtype=float),
        within_context_df=np.asarray(within_context_df, dtype=int),
        observation_variance=observation_variance,
        cohort_digest=cohort_digest,
        fold_assignments=fold_assignments,
        fold_ids=fold_ids,
        fold_digest=fold_digest,
        folds=resolved_folds,
    )


def observation_variance_for_rows(
    data: TargetTrainingSet,
    rows: np.ndarray,
) -> float:
    """Estimate repeat noise from only the rows available to one fit."""

    selected_y = data.y[rows]
    if len(selected_y) < 2:
        raise ValueError(f"{data.target}: noise estimation needs at least two rows")
    degrees = int(np.sum(data.within_context_df[rows]))
    if degrees:
        return max(
            float(np.sum(data.within_context_sse[rows])) / degrees,
            1e-8,
        )
    return max(float(np.var(selected_y)) * 0.1, 1e-8)


def feature_vector(
    feature_names: Sequence[str],
    row: Mapping[str, Any],
) -> np.ndarray:
    values = np.asarray([float(row[name]) for name in feature_names], dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("smoke feature row must be finite")
    return values
