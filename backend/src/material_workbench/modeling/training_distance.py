"""Task-runtime distance over one authoritative training cohort.

Output-space evidence must not join two target-specific similarity lists.  This
module resolves the runtime's allow-listed feature space once, restricts it to
the x/y intersection cohort, and returns one axis-order-invariant distance.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal, Sequence

import numpy as np

DISTANCE_CONTRACT_VERSION = "1.0.0"


@dataclass(frozen=True)
class TrainingDistanceEvidence:
    context_ids: tuple[str, ...]
    distances: np.ndarray
    method: str
    version: str
    cohort_digest: str
    supported_threshold: float
    caution_threshold: float


EvidenceContextIdentity = Literal["training_context", "parent_condition"]


def evidence_context_id(
    row: dict[str, Any],
    identity: EvidenceContextIdentity,
) -> str:
    value = (
        row.get("parent_key")
        if identity == "parent_condition"
        else (
            row.get("condition_context_id")
            or row.get("observation_id")
            or row.get("id")
        )
    )
    if value is None or not str(value).strip():
        raise ValueError(f"training distance row has no {identity} identity")
    return str(value)


def _collapse_contexts(
    context_ids: Sequence[str],
    vectors: np.ndarray,
) -> tuple[tuple[str, ...], np.ndarray]:
    grouped: dict[str, list[np.ndarray]] = {}
    for context_id, vector in zip(context_ids, vectors, strict=True):
        grouped.setdefault(context_id, []).append(vector)
    ordered = tuple(sorted(grouped))
    return ordered, np.vstack(
        [np.vstack(grouped[context_id]).mean(axis=0) for context_id in ordered]
    )


def _distance(
    reference: np.ndarray,
    query: np.ndarray,
    groups: dict[str, tuple[int, ...]] | None,
) -> np.ndarray:
    if not groups:
        return np.sqrt(((reference - query) ** 2).mean(axis=1))
    parts = [
        ((reference[:, columns] - query[list(columns)]) ** 2).mean(axis=1)
        for columns in groups.values()
        if columns
    ]
    return np.sqrt(np.vstack(parts).mean(axis=0))


def _thresholds(
    vectors: np.ndarray,
    groups: dict[str, tuple[int, ...]] | None,
) -> tuple[float, float]:
    if len(vectors) < 2:
        return 0.0, 0.0
    nearest: list[float] = []
    for index, vector in enumerate(vectors):
        distances = _distance(vectors, vector, groups)
        distances[index] = np.inf
        nearest.append(float(distances.min()))
    supported, caution = np.quantile(np.asarray(nearest), (0.80, 0.95))
    return float(supported), float(caution)


def _runtime_vectors(
    runtime: Any,
    candidate: Any,
    target_keys: Sequence[str],
    evidence_context: EvidenceContextIdentity,
) -> tuple[tuple[str, ...], np.ndarray, np.ndarray, dict[str, tuple[int, ...]] | None]:
    from material_workbench.modeling.flank_wear import FlankWearRuntime

    groups: dict[str, tuple[int, ...]] | None = None
    if getattr(runtime, "support_reference", None) is not None:
        reference = runtime.support_reference
        context_ids = tuple(
            evidence_context_id(row, evidence_context)
            for row in reference.parent_rows
        )
        vectors = np.asarray(reference.parent_vectors, dtype=float)
        query = reference.normalized(runtime.vector_for_candidate(candidate))
        groups = dict(getattr(runtime, "feature_group_indices", {}))
    elif isinstance(runtime, FlankWearRuntime):
        from material_workbench.modeling.flank_wear_feature_pipeline import (
            build_flank_wear_features_from_observation,
        )

        rows = [row for run_rows in runtime.reference_rows for row in run_rows]
        context_ids = tuple(
            evidence_context_id(row, evidence_context) for row in rows
        )
        raw = np.vstack(
            [
                build_flank_wear_features_from_observation(
                    row, runtime.composition_defaults
                ).values
                for row in rows
            ]
        )
        vectors = (raw - runtime.reference_mean) / runtime.reference_scale
        query = (
            runtime.vector(candidate) - runtime.reference_mean
        ) / runtime.reference_scale
        groups = dict(runtime.feature_group_indices)
    elif hasattr(runtime, "reference_vectors") and hasattr(runtime, "reference_rows"):
        context_ids = tuple(
            evidence_context_id(rows[0], evidence_context)
            for rows in runtime.reference_rows
        )
        vectors = np.asarray(runtime.reference_vectors, dtype=float)
        query = (
            runtime.vector(candidate) - runtime.reference_mean
        ) / runtime.reference_scale
        groups = dict(
            getattr(runtime, "feature_group_indices", None)
            or getattr(runtime, "FEATURE_GROUP_INDICES", {})
        )
    elif hasattr(runtime, "support_references"):
        # Target-specific runtimes use one deterministic family for the common
        # cohort. Sorting makes x/y swap a presentation-only operation.
        distance_target = sorted(target_keys)[0]
        if distance_target not in runtime.support_references:
            raise ValueError(
                f"入力空間の距離を計算できない予測特性です: {distance_target}"
            )
        reference = runtime.support_references[distance_target]
        rows = reference["rows"]
        context_ids = tuple(
            evidence_context_id(row, evidence_context) for row in rows
        )
        vectors = np.asarray(reference["vectors"], dtype=float)
        from material_workbench.modeling.observation_regression import (
            ObservationRegressionRuntime,
            candidate_feature_values,
        )
        from material_workbench.modeling.tabular_regression import (
            TabularRegressionRuntime,
            build_tabular_features,
        )

        if isinstance(runtime, TabularRegressionRuntime):
            raw = build_tabular_features(candidate, runtime.profile).values
        elif isinstance(runtime, ObservationRegressionRuntime):
            values = candidate_feature_values(candidate, runtime.spec)
            raw = np.asarray(
                [values[name] for name in runtime.spec.target_features[distance_target]]
            )
        else:
            raise ValueError(
                f"{runtime.task_id}のtarget別距離feature contractは未登録です"
            )
        query = (raw - reference["mean"]) / reference["scale"]
    else:
        raise ValueError(
            f"{runtime.task_id}のRuntimeは入力空間の距離contractに対応していません"
        )
    return context_ids, vectors, np.asarray(query, dtype=float), groups


def training_context_distances(
    runtime: Any,
    candidate: Any,
    *,
    target_keys: Sequence[str],
    allowed_context_ids: set[str],
    evidence_context: EvidenceContextIdentity = "training_context",
) -> TrainingDistanceEvidence:
    context_ids, vectors, query, groups = _runtime_vectors(
        runtime, candidate, target_keys, evidence_context
    )
    context_ids, vectors = _collapse_contexts(context_ids, vectors)
    indexes = [
        index
        for index, context_id in enumerate(context_ids)
        if context_id in allowed_context_ids
    ]
    context_ids = tuple(context_ids[index] for index in indexes)
    vectors = vectors[indexes]
    if not context_ids:
        raise ValueError("2軸共通cohortに距離を計算できる学習条件がありません")
    supported, caution = _thresholds(vectors, groups)
    method = str(runtime.support_policy_id)
    digest_payload = {
        "contexts": context_ids,
        "targets": sorted(target_keys),
        "method": method,
        "version": DISTANCE_CONTRACT_VERSION,
    }
    cohort_digest = "sha256:" + hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return TrainingDistanceEvidence(
        context_ids=context_ids,
        distances=_distance(vectors, query, groups),
        method=method,
        version=DISTANCE_CONTRACT_VERSION,
        cohort_digest=cohort_digest,
        supported_threshold=supported,
        caution_threshold=caution,
    )
