"""Use cases for feature-space and output-space evidence."""
from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean, pstdev
from threading import RLock
from typing import Any, Literal
from weakref import WeakKeyDictionary

from material_workbench.application.catalog.errors import (
    CatalogConflictError,
    CatalogNotFoundError,
    CatalogValidationError,
    lifecycle_profile,
    require_project,
)
from material_workbench.application.input_space import build_input_space_embedding
from material_workbench.application.project_runtime import ProjectRuntimeResolver
from material_workbench.contracts.prediction_catalog_contracts import (
    InputSpaceEmbeddingResponse,
)
from material_workbench.modeling.model_lifecycle import (
    canonical_training_dataset,
    validate_lifecycle_metadata,
)
from material_workbench.modeling.training_distance import (
    EvidenceContextIdentity,
    evidence_context_id,
    training_context_distances,
)
from material_workbench.persistence.store import Store
from material_workbench.task_composition.ports import TrainingInspectorAdapter
from material_workbench.tasks.task_registry import TaskRegistry


_INPUT_SPACE_CANONICAL_LOCK = RLock()
_INPUT_SPACE_CANONICAL: WeakKeyDictionary[
    Any, dict[str, dict[str, Any]]
] = WeakKeyDictionary()


def _cached_input_space_canonical(
    *,
    runtime: Any,
    task_id: str,
    contract: Any,
    pipeline_version: str,
) -> dict[str, Any]:
    cache_key = f"{task_id}:{pipeline_version}:{runtime.model_package.manifest_sha256}"
    with _INPUT_SPACE_CANONICAL_LOCK:
        runtime_cache = _INPUT_SPACE_CANONICAL.setdefault(runtime, {})
        canonical = runtime_cache.get(cache_key)
        if canonical is None:
            canonical = canonical_training_dataset(
                task_id,
                runtime.data,
                contract,
                pipeline_version=pipeline_version,
            )
            runtime_cache[cache_key] = canonical
        return canonical


@dataclass(frozen=True)
class FeatureInspector:
    store: Store
    registry: TaskRegistry
    resolver: ProjectRuntimeResolver

    def output_space_evidence(
        self,
        project_id: str,
        *,
        x_target: str,
        y_target: str,
        candidate_id: str,
        expected_revision: int,
        distance_filter: Literal["supported", "caution", "all"] = "supported",
        limit: int = 200,
    ) -> dict[str, Any]:
        if x_target == y_target:
            raise CatalogValidationError("output space axes must be different")
        project = require_project(self.store, project_id)
        candidate = self.store.get_candidate(candidate_id, project_id)
        if candidate is None:
            raise CatalogNotFoundError("candidate not found")
        if candidate.revision != expected_revision:
            raise CatalogConflictError("candidate revision changed")
        resolved = self.resolver.resolve(project)
        package = resolved.runtime.model_package
        assert package is not None
        contract = self.registry.contract_for(project.task_id)
        definition = self.registry.resolved_definition_for(project.task_id)
        adapter = self.registry.training_inspector_for(project.task_id)
        prediction_space = next(
            (
                surface
                for surface in definition.application.workbench_surfaces
                if surface.kind == "prediction_space"
            ),
            None,
        )
        if (
            prediction_space is None
            or x_target not in prediction_space.target_keys
            or y_target not in prediction_space.target_keys
        ):
            raise CatalogValidationError(
                "output space axes must be declared by the task surface"
            )
        evidence_context = prediction_space.evidence_context
        available_targets = {item.target for item in package.manifest.predictors}
        if x_target not in available_targets or y_target not in available_targets:
            raise CatalogValidationError(
                "output space axes must be predicted by the model package"
            )
        data = resolved.runtime.data
        validate_lifecycle_metadata(
            package,
            contract,
            profile_path=lifecycle_profile(data),
        )
        canonical = canonical_training_dataset(
            project.task_id,
            data,
            contract,
            pipeline_version=package.manifest.feature_pipeline.version,
        )
        points = _output_space_evidence_points(
            canonical["rows"],
            adapter=adapter,
            x_target=x_target,
            y_target=y_target,
            evidence_context=evidence_context,
        )
        try:
            distance_evidence = training_context_distances(
                resolved.runtime,
                candidate,
                target_keys=(x_target, y_target),
                allowed_context_ids={point["context_id"] for point in points},
                evidence_context=evidence_context,
            )
        except ValueError as exc:
            raise CatalogValidationError(str(exc)) from exc
        distance_by_context = dict(
            zip(
                distance_evidence.context_ids,
                distance_evidence.distances,
                strict=True,
            )
        )
        enriched = []
        for point in points:
            distance = float(distance_by_context[point["context_id"]])
            status = (
                "supported"
                if distance <= distance_evidence.supported_threshold
                else "caution"
                if distance <= distance_evidence.caution_threshold
                else "extrapolated"
            )
            enriched.append({
                **point,
                "distance": distance,
                "distance_status": status,
            })
        eligible = [
            point
            for point in enriched
            if distance_filter == "all"
            or point["distance_status"] == "supported"
            or (
                distance_filter == "caution"
                and point["distance_status"] in {"supported", "caution"}
            )
        ]
        eligible.sort(key=lambda point: (point["distance"], point["context_id"]))
        visible = eligible[:limit]
        return {
            "x_target": x_target,
            "y_target": y_target,
            "evidence_context": evidence_context,
            "source_data_digest": canonical["source_data_digest"],
            "candidate_id": candidate.id,
            "candidate_revision": candidate.revision,
            "distance_method": distance_evidence.method,
            "distance_version": distance_evidence.version,
            "cohort_digest": distance_evidence.cohort_digest,
            "supported_threshold": distance_evidence.supported_threshold,
            "caution_threshold": distance_evidence.caution_threshold,
            "filter": distance_filter,
            "eligible_contexts": len(eligible),
            "sampling_policy": "task_distance",
            "total_contexts": len(points),
            "returned_contexts": len(visible),
            "truncated": len(visible) < len(eligible),
            "points": visible,
        }

    def input_space_embedding(
        self,
        project_id: str,
        *,
        candidate_id: str,
        expected_revision: int,
    ) -> dict[str, Any]:
        project = require_project(self.store, project_id)
        candidate = self.store.get_candidate(candidate_id, project_id)
        if candidate is None:
            raise CatalogNotFoundError("candidate not found")
        if candidate.revision != expected_revision:
            raise CatalogConflictError("candidate revision changed")
        resolved = self.resolver.resolve(project)
        package = resolved.runtime.model_package
        assert package is not None
        contract = self.registry.contract_for(project.task_id)
        surface = next(
            (
                item
                for item in self.registry.resolved_definition_for(
                    project.task_id
                ).application.workbench_surfaces
                if item.kind == "input_space"
            ),
            None,
        )
        if surface is None:
            raise CatalogValidationError(
                "このPrediction Taskは入力空間Surfaceを宣言していません"
            )
        available_targets = {item.target for item in package.manifest.predictors}
        if surface.distance_target_key not in available_targets:
            raise CatalogValidationError(
                "入力空間の距離基準がModel Packageにありません"
            )
        data = resolved.runtime.data
        validate_lifecycle_metadata(
            package,
            contract,
            profile_path=lifecycle_profile(data),
        )
        canonical = _cached_input_space_canonical(
            runtime=resolved.runtime,
            task_id=project.task_id,
            contract=contract,
            pipeline_version=package.manifest.feature_pipeline.version,
        )
        candidates = self.store.list_candidates(project_id)
        try:
            payload = build_input_space_embedding(
                runtime=resolved.runtime,
                canonical=canonical,
                candidates=candidates,
                selected_candidate=candidate,
                surface=surface,
            )
            return InputSpaceEmbeddingResponse.model_validate(payload).model_dump(
                mode="json"
            )
        except ValueError as exc:
            raise CatalogValidationError(str(exc)) from exc


def _output_space_evidence_points(
    rows: list[dict[str, Any]],
    *,
    adapter: TrainingInspectorAdapter,
    x_target: str,
    y_target: str,
    evidence_context: EvidenceContextIdentity = "training_context",
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if x_target not in row["outputs"] and y_target not in row["outputs"]:
            continue
        context_id = evidence_context_id(row, evidence_context)
        grouped.setdefault(context_id, []).append(row)
    points = []
    for context_id, context_rows in sorted(grouped.items()):
        x_observations = [
            (str(row["observation_id"]), float(row["outputs"][x_target]))
            for row in context_rows
            if x_target in row["outputs"]
        ]
        y_observations = [
            (str(row["observation_id"]), float(row["outputs"][y_target]))
            for row in context_rows
            if y_target in row["outputs"]
        ]
        if not x_observations or not y_observations:
            continue
        parent_keys = {str(row["parent_key"]) for row in context_rows}
        if len(parent_keys) != 1:
            raise CatalogValidationError(
                "output-space context spans multiple validation groups: "
                f"{context_id}"
            )
        x_ids = {observation_id for observation_id, _ in x_observations}
        y_ids = {observation_id for observation_id, _ in y_observations}
        relationship = (
            "same_observations"
            if x_ids == y_ids
            else "overlapping_observations"
            if x_ids & y_ids
            else "distinct_observations"
        )
        points.append({
            "context_id": context_id,
            "parent_key": next(iter(parent_keys)),
            **dict(adapter.output_space_context(context_rows)),
            "relation_context_ids": sorted(
                {
                    str(relation_id)
                    for row in context_rows
                    for relation_id in row.get("relation_context_ids", [])
                }
            ),
            "pairing_relationship": relationship,
            "x": {
                "mean": fmean(value for _, value in x_observations),
                "std": pstdev(value for _, value in x_observations),
                "min": min(value for _, value in x_observations),
                "max": max(value for _, value in x_observations),
                "count": len(x_observations),
                "observation_ids": sorted(x_ids),
            },
            "y": {
                "mean": fmean(value for _, value in y_observations),
                "std": pstdev(value for _, value in y_observations),
                "min": min(value for _, value in y_observations),
                "max": max(value for _, value in y_observations),
                "count": len(y_observations),
                "observation_ids": sorted(y_ids),
            },
        })
    return points
