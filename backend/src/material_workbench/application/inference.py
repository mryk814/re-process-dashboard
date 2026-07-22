from __future__ import annotations

import math
from typing import Any, Mapping

from .candidates import CandidateService
from .projects import ProjectService
from ..inference_work_graph import InferenceKey, InferenceWorkGraph
from ..schemas import Candidate, Project
from ..store import Store
from ..task_registry import TaskRegistry, TaskRegistryError


class InferenceValidationError(ValueError):
    pass


class InferenceService:
    def __init__(self, store: Store, registry: TaskRegistry, graph: InferenceWorkGraph) -> None:
        self.registry = registry
        self.graph = graph
        self.projects = ProjectService(store, registry)
        self.candidates = CandidateService(store, registry)

    def preview(self, project_id: str, candidate_id: str, revision: int) -> dict[str, Any]:
        project = self.projects.require(project_id)
        self.require_operation(project.task_id, "preview")
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        runtime = self.registry.runtime_for(project.task_id)
        prediction = self.graph.execute(
            self.key(project.task_id, candidate, "preview", parameters={"target_values": project.target_values}, uses_package=True),
            lambda: runtime.predict_core(candidate, detailed=False, target_values=project.target_values),
        )
        support = self.graph.execute(
            self.key(project.task_id, candidate, "support", uses_support=True),
            lambda: self.registry.entry_for(project.task_id).support_provider.support_summary(candidate),
        )
        prediction["candidate_id"] = candidate.id
        prediction["support"] = support
        prediction["similar"] = []
        if support.status != "supported" and support.message not in prediction["warnings"]:
            prediction["warnings"].append(support.message)
        return prediction

    def detailed_for(self, project: Project, candidate: Candidate) -> dict[str, Any]:
        self.require_operation(project.task_id, "detailed_prediction")
        runtime = self.registry.runtime_for(project.task_id)
        result = self.graph.execute(
            self.key(project.task_id, candidate, "detailed", parameters={"target_values": project.target_values, "policy_id": "detailed-v1"}, uses_package=True, uses_support=True),
            lambda: runtime.predict(candidate, detailed=True, include_curve=False, target_values=project.target_values),
        )
        result["candidate_id"] = candidate.id
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
            runtime = self.registry.runtime_for(project.task_id)
            handler = self.registry.response_curve_for(project.task_id)
            return self.graph.execute(
                self.key(project.task_id, candidate, "curve", parameters={"target": target, "variable": variable, "points": points, "range_min": range_min, "range_max": range_max, "stage_name": stage_name, "stage_position_m": stage_position_m, "policy_id": "fixed-grid-v2"}, uses_package=True),
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
            runtime = self.registry.runtime_for(project.task_id)
            handler = self.registry.curve_family_for(project.task_id)
            return self.graph.execute(
                self.key(project.task_id, candidate, "curve_family", parameters={"target": target, "vary": vary, "levels": levels, "points": points, "policy_id": "axis-grid-v1"}, uses_package=True),
                lambda: handler(runtime, candidate, target, vary or None, levels, points),
            )
        except ValueError as exc:
            raise InferenceValidationError(str(exc)) from exc

    def similar(self, project_id: str, candidate_id: str, revision: int, limit: int) -> list[dict[str, object]]:
        project = self.projects.require(project_id)
        self.require_operation(project.task_id, "similarity")
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        provider = self.registry.entry_for(project.task_id).support_provider
        return self.graph.execute(
            self.key(project.task_id, candidate, "similarity", parameters={"limit": limit}, uses_support=True),
            lambda: provider.similarity(candidate, limit),
        )

    def diagnostics(self) -> dict[str, Any]:
        return self.graph.diagnostics()

    def require_operation(self, task_id: str, operation: str) -> None:
        try:
            self.registry.require_operation(task_id, operation)  # type: ignore[arg-type]
        except TaskRegistryError as exc:
            raise InferenceValidationError(str(exc)) from exc

    def key(self, task_id: str, candidate: Candidate, operation: str, *, parameters: Mapping[str, Any] | None = None, uses_package: bool = False, uses_support: bool = False) -> InferenceKey:
        entry = self.registry.entry_for(task_id)
        canonical = self.registry.validate_candidate(task_id, candidate).model_dump(mode="json", exclude={"provenance"})
        return InferenceKey.build(
            task_id=task_id,
            runtime_type=entry.runtime_type,
            canonical_input=canonical,
            package_digest=entry.package_digest if uses_package else "",
            pipeline_digest=entry.pipeline_digest,
            support_digest=entry.support_digest if uses_support else None,
            operation=operation,
            operation_parameters=parameters,
        )
