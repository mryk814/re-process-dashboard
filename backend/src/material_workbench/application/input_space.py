"""Build the Task-declared input-space evidence surface."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import fmean, pstdev
from threading import RLock
from typing import Any, Sequence
from weakref import WeakKeyDictionary

import numpy as np

from material_workbench.contracts.candidate_project_contracts import Candidate
from material_workbench.contracts.task_contracts import (
    InputSpaceSurfaceDefinition,
)
from material_workbench.modeling.input_space_embedding import (
    EMBEDDING_METHOD,
    EMBEDDING_VERSION,
    deterministic_display_indexes,
    fit_landmark_mds,
    LandmarkMdsTransform,
)
from material_workbench.modeling.training_distance import (
    candidate_metric_query,
    evidence_context_id,
    metric_distances,
    resolve_training_metric_space,
)

_CACHE_LOCK = RLock()


@dataclass(frozen=True)
class _TrainingEmbedding:
    context_ids: tuple[str, ...]
    vectors: np.ndarray
    groups: dict[str, tuple[int, ...]] | None
    metadata: dict[str, dict[str, Any]]
    method: str
    version: str
    cohort_digest: str
    vector_space_digest: str
    supported_threshold: float
    caution_threshold: float
    transform: LandmarkMdsTransform
    training_coordinates: np.ndarray
    display_indexes: tuple[int, ...]


_TRAINING_EMBEDDINGS: WeakKeyDictionary[
    Any, dict[tuple[Any, ...], _TrainingEmbedding]
] = WeakKeyDictionary()


def _context_metadata(
    rows: list[dict[str, Any]],
    surface: InputSpaceSurfaceDefinition,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if surface.distance_target_key not in row["outputs"]:
            continue
        grouped[evidence_context_id(row, surface.evidence_context)].append(row)
    result: dict[str, dict[str, Any]] = {}
    for context_id, context_rows in grouped.items():
        parent_keys = sorted({str(row["parent_key"]) for row in context_rows})
        if len(parent_keys) != 1:
            raise ValueError(
                f"入力空間の1条件が複数validation groupにまたがっています: {context_id}"
            )
        composition_keys = sorted(
            {
                str(row["composition_key"])
                for row in context_rows
                if row.get("composition_key")
            }
        )
        repeat_summary: dict[str, dict[str, float | int]] = {}
        output_targets = sorted(
            {
                target
                for row in context_rows
                for target in row["outputs"]
            }
        )
        for target in output_targets:
            values = [
                float(row["outputs"][target])
                for row in context_rows
                if target in row["outputs"]
            ]
            if values:
                repeat_summary[target] = {
                    "mean": fmean(values),
                    "std": pstdev(values),
                    "n": len(values),
                }
        result[context_id] = {
            "context_id": context_id,
            "parent_key": parent_keys[0],
            "process_key": parent_keys[0],
            "composition_key": (
                composition_keys[0] if len(composition_keys) == 1 else None
            ),
            "relation_context_ids": sorted(
                {
                    str(relation_id)
                    for row in context_rows
                    for relation_id in row.get("relation_context_ids", [])
                }
            ),
            "observation_ids": sorted(
                {str(row["observation_id"]) for row in context_rows}
            ),
            "repeat_summary": repeat_summary,
        }
    return result


def build_input_space_embedding(
    *,
    runtime: Any,
    canonical: dict[str, Any],
    candidates: Sequence[Candidate],
    selected_candidate: Candidate,
    surface: InputSpaceSurfaceDefinition,
) -> dict[str, Any]:
    if EMBEDDING_METHOD != surface.embedding_method:
        raise ValueError("未登録の入力空間embedding methodです")
    if EMBEDDING_VERSION != surface.embedding_version:
        raise ValueError("未登録の入力空間embedding versionです")
    ordered_candidates = sorted(candidates, key=lambda item: item.id)
    cache_key = (
        canonical["source_data_digest"],
        runtime.model_package.manifest_sha256,
        surface.distance_target_key,
        surface.evidence_context,
        surface.embedding_method,
        surface.embedding_version,
        surface.seed,
        surface.landmark_limit,
        surface.historical_limit,
    )
    with _CACHE_LOCK:
        runtime_cache = _TRAINING_EMBEDDINGS.setdefault(runtime, {})
        fixed = runtime_cache.get(cache_key)
        if fixed is None:
            metadata = _context_metadata(canonical["rows"], surface)
            allowed_context_ids = set(metadata)
            if not allowed_context_ids:
                raise ValueError(
                    f"{surface.distance_target_key}の学習cohortに配置できる条件がありません"
                )
            base = resolve_training_metric_space(
                runtime,
                target_keys=(surface.distance_target_key,),
                allowed_context_ids=allowed_context_ids,
                evidence_context=surface.evidence_context,
            )
            distance = lambda reference, query: metric_distances(  # noqa: E731
                reference,
                query,
                base.groups,
            )
            transform = fit_landmark_mds(
                base.vectors,
                distance,
                landmark_limit=surface.landmark_limit,
                seed=surface.seed,
            )
            display_indexes = deterministic_display_indexes(
                base.vectors,
                limit=surface.historical_limit,
                seed=surface.seed,
                required=transform.landmark_indexes,
            )
            training_coordinates = transform.transform(
                base.vectors[list(display_indexes)],
                distance,
            )
            fixed = _TrainingEmbedding(
                context_ids=base.context_ids,
                vectors=base.vectors,
                groups=base.groups,
                metadata=metadata,
                method=base.method,
                version=base.version,
                cohort_digest=base.cohort_digest,
                vector_space_digest=base.vector_space_digest,
                supported_threshold=base.supported_threshold,
                caution_threshold=base.caution_threshold,
                transform=transform,
                training_coordinates=training_coordinates,
                display_indexes=display_indexes,
            )
            runtime_cache[cache_key] = fixed
    distance = lambda reference, query: metric_distances(  # noqa: E731
        reference,
        query,
        fixed.groups,
    )
    candidate_vectors = np.vstack(
        [
            candidate_metric_query(
                runtime,
                candidate,
                target_keys=(surface.distance_target_key,),
            )
            for candidate in ordered_candidates
        ]
    )
    if candidate_vectors.shape[1] != fixed.vectors.shape[1]:
        raise ValueError("候補と学習cohortの入力空間feature orderが一致しません")
    candidate_coordinates = fixed.transform.transform(candidate_vectors, distance)
    landmark_indexes = set(fixed.transform.landmark_indexes)
    training_points = []
    for display_index, index in enumerate(fixed.display_indexes):
        context_id = fixed.context_ids[index]
        training_points.append(
            {
                **fixed.metadata[context_id],
                "x": float(fixed.training_coordinates[display_index, 0]),
                "y": float(fixed.training_coordinates[display_index, 1]),
                "landmark": index in landmark_indexes,
            }
        )
    candidate_points = []
    for index, candidate in enumerate(ordered_candidates):
        query = candidate_vectors[index]
        training_distances = distance(fixed.vectors, query)
        nearest_training_index = int(np.argmin(training_distances))
        island_distance = float(training_distances[nearest_training_index])
        island_status = (
            "supported"
            if island_distance <= fixed.supported_threshold
            else "caution"
            if island_distance <= fixed.caution_threshold
            else "extrapolated"
        )
        novelty: float | None = None
        nearest_candidate_id: str | None = None
        if len(ordered_candidates) > 1:
            candidate_distances = distance(candidate_vectors, query)
            candidate_distances[index] = np.inf
            nearest_candidate_index = int(np.argmin(candidate_distances))
            novelty = float(candidate_distances[nearest_candidate_index])
            nearest_candidate_id = ordered_candidates[nearest_candidate_index].id
        candidate_points.append(
            {
                "candidate_id": candidate.id,
                "candidate_revision": candidate.revision,
                "label": candidate.name,
                "x": float(candidate_coordinates[index, 0]),
                "y": float(candidate_coordinates[index, 1]),
                "island_distance": island_distance,
                "island_status": island_status,
                "nearest_training_context_id": fixed.context_ids[
                    nearest_training_index
                ],
                "candidate_novelty": novelty,
                "nearest_candidate_id": nearest_candidate_id,
            }
        )
    return {
        "source_data_digest": canonical["source_data_digest"],
        "distance_target_key": surface.distance_target_key,
        "evidence_context": surface.evidence_context,
        "distance_method": fixed.method,
        "distance_version": fixed.version,
        "cohort_digest": fixed.cohort_digest,
        "vector_space_digest": fixed.vector_space_digest,
        "supported_threshold": fixed.supported_threshold,
        "caution_threshold": fixed.caution_threshold,
        "embedding_method": surface.embedding_method,
        "embedding_version": surface.embedding_version,
        "seed": surface.seed,
        "landmark_count": len(fixed.transform.landmark_indexes),
        "total_training_contexts": len(fixed.context_ids),
        "displayed_training_contexts": len(training_points),
        "captured_positive_eigenvalue_ratio": (
            fixed.transform.captured_positive_eigenvalue_ratio
        ),
        "selected_candidate_id": selected_candidate.id,
        "selected_candidate_revision": selected_candidate.revision,
        "training_points": training_points,
        "candidate_points": candidate_points,
    }
