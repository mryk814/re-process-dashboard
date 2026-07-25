from __future__ import annotations

import math
from typing import Any, Mapping

from .candidates import CandidateService
from .projects import ProjectService
from material_workbench.domain.goal_targets import serialize_target_values
from material_workbench.execution.inference_work_graph import InferenceKey, InferenceWorkGraph
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver
from material_workbench.contracts.schemas import Candidate, Project
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError


class InferenceValidationError(ValueError):
    pass


class InferenceService:
    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        graph: InferenceWorkGraph,
        resolver: ProjectRuntimeResolver,
    ) -> None:
        self.registry = registry
        self.graph = graph
        self.resolver = resolver
        self.projects = ProjectService(store, registry)
        self.candidates = CandidateService(store, registry, resolver)

    def preview(self, project_id: str, candidate_id: str, revision: int) -> dict[str, Any]:
        project = self.projects.require(project_id)
        self.require_operation(project.task_id, "preview")
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        runtime = self.resolver.runtime_for(project)
        prediction = self.graph.execute(
            self.key(project, candidate, "preview", parameters={"target_values": serialize_target_values(project.target_values)}, uses_package=True),
            lambda: runtime.predict_core(candidate, detailed=False, target_values=project.target_values),
        )
        support = self.graph.execute(
            self.key(project, candidate, "support", uses_support=True),
            lambda: runtime.support_summary(candidate),  # type: ignore[attr-defined]
        )
        prediction["candidate_id"] = candidate.id
        prediction["support"] = support
        prediction["model_support"] = runtime.support_by_target(candidate)  # type: ignore[attr-defined]
        prediction["similar"] = []
        prediction.setdefault("model_meta", {})["application_data"] = {
            "dataset_view_revision_id": project.dataset_view_revision_id,
            "source_sha256": self.resolver.context_runtime_for(project).data.source_sha256,
        }
        if support.status != "supported" and support.message not in prediction["warnings"]:
            prediction["warnings"].append(support.message)
        return prediction

    def detailed_for(self, project: Project, candidate: Candidate) -> dict[str, Any]:
        self.require_operation(project.task_id, "detailed_prediction")
        runtime = self.resolver.runtime_for(project)
        result = self.graph.execute(
            self.key(project, candidate, "detailed", parameters={"target_values": serialize_target_values(project.target_values), "policy_id": "detailed-v1"}, uses_package=True, uses_support=True),
            lambda: runtime.predict(candidate, detailed=True, include_curve=False, target_values=project.target_values),
        )
        result["candidate_id"] = candidate.id
        result["model_support"] = runtime.support_by_target(candidate)  # type: ignore[attr-defined]
        result["similar"] = self._context_similarity(project, candidate, 6)
        result.setdefault("model_meta", {})["application_data"] = {
            "dataset_view_revision_id": project.dataset_view_revision_id,
            "source_sha256": self.resolver.context_runtime_for(project).data.source_sha256,
        }
        return result

    def response_curve(self, project_id: str, candidate_id: str, revision: int, target: str, variable: str, points: int, range_min: float | None, range_max: float | None, stage_name: str | None, stage_position_m: float | None) -> dict[str, Any]:
        project = self.projects.require(project_id)
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        definition = self.registry.contract_for(project.task_id).task_definition
        if target not in {item.key for item in definition.outputs}:
            raise InferenceValidationError("この予測タスクにない予測特性です")
        self.require_operation(project.task_id, "response_curve")
        if (range_min is None) != (range_max is None):
            raise InferenceValidationError("応答曲線の範囲は最小値と最大値をセットで指定してください")
        is_stage_temperature = variable == "heat.stage_temperature_c"
        if is_stage_temperature != (stage_name is not None and stage_position_m is not None):
            raise InferenceValidationError("工程温度の応答曲線は工程名と入口からの工程位置をセットで指定してください")
        if stage_name is not None and not stage_name.strip():
            raise InferenceValidationError("工程名は空白以外の文字を指定してください")
        axis_range = None
        if range_min is not None and range_max is not None:
            if not math.isfinite(range_min) or not math.isfinite(range_max) or range_min >= range_max:
                raise InferenceValidationError("応答曲線の範囲は有限の数値で、最小値 < 最大値にしてください")
            axis_range = (range_min, range_max)
        try:
            runtime = self.resolver.runtime_for(project)
            handler = self.registry.response_curve_for(project.task_id)
            return self.graph.execute(
                self.key(project, candidate, "curve", parameters={"target": target, "variable": variable, "points": points, "range_min": range_min, "range_max": range_max, "stage_name": stage_name, "stage_position_m": stage_position_m, "policy_id": "anchored-grid-v1"}, uses_package=True),
                lambda: handler(runtime, candidate, target, variable, points, axis_range, stage_name, stage_position_m),
            )
        except ValueError as exc:
            raise InferenceValidationError(str(exc)) from exc

    def curve_family(self, project_id: str, candidate_id: str, revision: int, target: str, vary: str, levels: int, points: int) -> dict[str, Any]:
        project = self.projects.require(project_id)
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        contract = self.registry.contract_for(project.task_id)
        if target not in {item.key for item in contract.task_definition.outputs}:
            raise InferenceValidationError("この予測タスクにない予測特性です")
        if contract.task_definition.curve_axis_path is None or not contract.runtime_capability.operations.response_curve:
            raise InferenceValidationError("この予測タスクは曲線ビューに対応していません")
        try:
            runtime = self.resolver.runtime_for(project)
            handler = self.registry.curve_family_for(project.task_id)
            return self.graph.execute(
                self.key(project, candidate, "curve_family", parameters={"target": target, "vary": vary, "levels": levels, "points": points, "policy_id": "anchored-axis-grid-v1"}, uses_package=True),
                lambda: handler(runtime, candidate, target, vary or None, levels, points),
            )
        except ValueError as exc:
            raise InferenceValidationError(str(exc)) from exc

    def similar(
        self,
        project_id: str,
        candidate_id: str,
        revision: int,
        limit: int,
        target: str | None = None,
    ) -> list[dict[str, object]]:
        project = self.projects.require(project_id)
        self.require_operation(project.task_id, "similarity")
        definition = self.registry.contract_for(project.task_id).task_definition
        targets = {item.key for item in definition.outputs}
        if target is not None and target not in targets:
            raise InferenceValidationError("この予測タスクにない実測特性です")
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        return self.graph.execute(
            self.key(
                project,
                candidate,
                "similarity",
                parameters={"limit": limit, "target": target},
                uses_support=True,
            ),
            lambda: self._context_similarity(project, candidate, limit, target),
        )

    def _context_similarity(
        self,
        project: Project,
        candidate: Candidate,
        limit: int,
        target: str | None = None,
    ) -> list[dict[str, object]]:
        provider = self.resolver.context_runtime_for(project)
        rows = provider.similarity(candidate, limit, target=target)  # type: ignore[attr-defined]
        return [
            {
                **row,
                "layer": "historical",
                "source_scope": "project_reference_data",
            }
            for row in rows
        ]

    def diagnostics(self) -> dict[str, Any]:
        return self.graph.diagnostics()

    def require_operation(self, task_id: str, operation: str) -> None:
        try:
            self.registry.require_operation(task_id, operation)  # type: ignore[arg-type]
        except TaskRegistryError as exc:
            raise InferenceValidationError(str(exc)) from exc

    def key(self, project: Project, candidate: Candidate, operation: str, *, parameters: Mapping[str, Any] | None = None, uses_package: bool = False, uses_support: bool = False) -> InferenceKey:
        resolved = self.resolver.resolve(project)
        identity = resolved.identity
        canonical = self.registry.validate_candidate(project.task_id, candidate).model_dump(mode="json", exclude={"provenance"})
        return InferenceKey.build(
            task_id=identity.task_id,
            runtime_type=identity.runtime_type,
            canonical_input=canonical,
            package_digest=identity.package_digest if uses_package else "",
            pipeline_digest=identity.pipeline_digest,
            support_digest=identity.support_digest if uses_support else None,
            operation=operation,
            operation_parameters=parameters,
        )
