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


@dataclass(frozen=True)
class TrainingMetricSpace:
    """One versioned Task-distance space shared by training and query points."""

    context_ids: tuple[str, ...]
    vectors: np.ndarray
    groups: dict[str, tuple[int, ...]] | None
    feature_order: tuple[str, ...]
    method: str
    version: str
    cohort_digest: str
    vector_space_digest: str
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


def metric_distances(
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


def _runtime_metric_source(
    runtime: Any,
    target_keys: Sequence[str],
    evidence_context: EvidenceContextIdentity,
) -> tuple[
    tuple[str, ...],
    np.ndarray,
    dict[str, tuple[int, ...]] | None,
    tuple[float, float],
    tuple[str, ...],
]:
    from material_workbench.modeling.flank_wear import FlankWearRuntime

    groups: dict[str, tuple[int, ...]] | None = None
    if getattr(runtime, "support_reference", None) is not None:
        reference = runtime.support_reference
        context_ids = tuple(
            evidence_context_id(row, evidence_context)
            for row in reference.parent_rows
        )
        vectors = np.asarray(reference.parent_vectors, dtype=float)
        groups = dict(getattr(runtime, "feature_group_indices", {}))
        thresholds = (
            float(reference.supported_threshold),
            float(reference.caution_threshold),
        )
        feature_order = tuple(runtime.feature_names)
    elif isinstance(runtime, FlankWearRuntime):
        from material_workbench.modeling.flank_wear_feature_pipeline import (
            FEATURE_NAMES,
        )

        context_ids = tuple(
            evidence_context_id(rows[0], evidence_context)
            for rows in runtime.reference_rows
        )
        vectors = np.asarray(runtime.reference_vectors, dtype=float)
        groups = dict(runtime.feature_group_indices)
        thresholds = (
            float(runtime.supported_threshold),
            float(runtime.caution_threshold),
        )
        feature_order = tuple(FEATURE_NAMES)
    elif hasattr(runtime, "reference_vectors") and hasattr(runtime, "reference_rows"):
        context_ids = tuple(
            evidence_context_id(rows[0], evidence_context)
            for rows in runtime.reference_rows
        )
        vectors = np.asarray(runtime.reference_vectors, dtype=float)
        groups = dict(
            getattr(runtime, "feature_group_indices", None)
            or getattr(runtime, "FEATURE_GROUP_INDICES", {})
        )
        thresholds = (
            float(runtime.supported_threshold),
            float(runtime.caution_threshold),
        )
        manifest = runtime.model_package.manifest
        feature_order = tuple(manifest.feature_pipeline.output_features)
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
        thresholds = (
            float(reference["supported_threshold"]),
            float(reference["caution_threshold"]),
        )
        from material_workbench.modeling.observation_regression import (
            ObservationRegressionRuntime,
        )

        if isinstance(runtime, ObservationRegressionRuntime):
            feature_order = tuple(runtime.spec.target_features[distance_target])
        else:
            feature_order = tuple(
                runtime.model_package.manifest.feature_pipeline.output_features
            )
    else:
        raise ValueError(
            f"{runtime.task_id}のRuntimeは入力空間の距離contractに対応していません"
        )
    return context_ids, vectors, groups, thresholds, feature_order


def candidate_metric_query(
    runtime: Any,
    candidate: Any,
    *,
    target_keys: Sequence[str],
) -> np.ndarray:
    """Transform one candidate into the Runtime-owned normalized metric space."""

    from material_workbench.modeling.flank_wear import FlankWearRuntime
    from material_workbench.modeling.observation_regression import (
        ObservationRegressionRuntime,
        candidate_feature_values,
    )
    from material_workbench.modeling.tabular.features import build_tabular_features
    from material_workbench.modeling.tabular.runtime import TabularRegressionRuntime

    if getattr(runtime, "support_reference", None) is not None:
        reference = runtime.support_reference
        query = reference.normalized(runtime.vector_for_candidate(candidate))
    elif isinstance(runtime, FlankWearRuntime):
        query = (
            runtime.vector(candidate) - runtime.reference_mean
        ) / runtime.reference_scale
    elif hasattr(runtime, "reference_vectors") and hasattr(runtime, "reference_rows"):
        query = (
            runtime.vector(candidate) - runtime.reference_mean
        ) / runtime.reference_scale
    elif hasattr(runtime, "support_references"):
        distance_target = sorted(target_keys)[0]
        reference = runtime.support_references[distance_target]
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
    return np.asarray(query, dtype=float)


def _vector_space_identity(runtime: Any) -> dict[str, Any]:
    model_package = getattr(runtime, "model_package", None)
    if model_package is None:
        return {
            "runtime": f"{type(runtime).__module__}.{type(runtime).__qualname__}",
            "task_id": str(runtime.task_id),
        }
    manifest = model_package.manifest
    return {
        "package_id": manifest.package_id,
        "package_version": manifest.package_version,
        "package_manifest_sha256": model_package.manifest_sha256,
        "feature_pipeline_id": manifest.feature_pipeline.id,
        "feature_pipeline_version": manifest.feature_pipeline.version,
    }


def _vector_space_digest(
    *,
    runtime: Any,
    context_ids: tuple[str, ...],
    vectors: np.ndarray,
    groups: dict[str, tuple[int, ...]] | None,
    feature_order: tuple[str, ...],
) -> str:
    metadata = {
        "contexts": context_ids,
        "feature_order": feature_order,
        "groups": {
            name: tuple(columns)
            for name, columns in sorted((groups or {}).items())
        },
        "identity": _vector_space_identity(runtime),
        "shape": tuple(vectors.shape),
    }
    digest = hashlib.sha256(
        json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    digest.update(np.ascontiguousarray(vectors, dtype="<f8").tobytes())
    return "sha256:" + digest.hexdigest()


def training_context_distances(
    runtime: Any,
    candidate: Any,
    *,
    target_keys: Sequence[str],
    allowed_context_ids: set[str],
    evidence_context: EvidenceContextIdentity = "training_context",
) -> TrainingDistanceEvidence:
    space = resolve_training_metric_space(
        runtime,
        target_keys=target_keys,
        allowed_context_ids=allowed_context_ids,
        evidence_context=evidence_context,
    )
    query = candidate_metric_query(
        runtime,
        candidate,
        target_keys=target_keys,
    )
    return TrainingDistanceEvidence(
        context_ids=space.context_ids,
        distances=metric_distances(space.vectors, query, space.groups),
        method=space.method,
        version=space.version,
        cohort_digest=space.cohort_digest,
        supported_threshold=space.supported_threshold,
        caution_threshold=space.caution_threshold,
    )


def resolve_training_metric_space(
    runtime: Any,
    *,
    target_keys: Sequence[str],
    allowed_context_ids: set[str],
    evidence_context: EvidenceContextIdentity = "training_context",
) -> TrainingMetricSpace:
    context_ids, vectors, groups, thresholds, feature_order = (
        _runtime_metric_source(
            runtime,
            target_keys,
            evidence_context,
        )
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
        raise ValueError("指定cohortに距離を計算できる学習条件がありません")
    supported, caution = thresholds
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
    vector_space_digest = _vector_space_digest(
        runtime=runtime,
        context_ids=context_ids,
        vectors=vectors,
        groups=groups,
        feature_order=feature_order,
    )
    return TrainingMetricSpace(
        context_ids=context_ids,
        vectors=vectors,
        groups=groups,
        feature_order=feature_order,
        method=method,
        version=DISTANCE_CONTRACT_VERSION,
        cohort_digest=cohort_digest,
        vector_space_digest=vector_space_digest,
        supported_threshold=supported,
        caution_threshold=caution,
    )
