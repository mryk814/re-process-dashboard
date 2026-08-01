from __future__ import annotations

import math
from typing import Any, Mapping

from .candidates import CandidateService
from .projects import ProjectService
from decision_workbench.domain.goal_targets import serialize_target_values
from decision_workbench.execution.inference_work_graph import InferenceKey, InferenceWorkGraph
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    Project,
)
from decision_workbench.contracts.prediction_catalog_contracts import (
    Prediction,
    Support,
)
from decision_workbench.modeling.response_curve_errors import (
    ResponseCurveNotApplicableError,
    ResponseCurveTrainingRangeUnavailableError,
)
from decision_workbench.modeling.curve_grid import numeric_domain_grid, use_numeric_domain
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError, TaskUnavailableError


class InferenceValidationError(ValueError):
    pass


class InferenceResponseCurveNotApplicableError(InferenceValidationError):
    pass


class InferenceResponseCurveTrainingRangeUnavailableError(InferenceValidationError):
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
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        self.require_operation(project.task_id, "preview")
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
        try:
            self.registry.candidate_family_for(
                project.task_id
            ).validate_response_axis(
                variable,
                stage_name=stage_name,
                stage_position_m=stage_position_m,
            )
        except ValueError as exc:
            raise InferenceValidationError(str(exc)) from exc
        axis_range = None
        if range_min is not None and range_max is not None:
            if not math.isfinite(range_min) or not math.isfinite(range_max) or range_min >= range_max:
                raise InferenceValidationError("応答曲線の範囲は有限の数値で、最小値 < 最大値にしてください")
            axis_range = (range_min, range_max)
        try:
            runtime = self.resolver.runtime_for(project)
            handler = self.registry.response_curve_for(project.task_id)
            field = next(
                (
                    item
                    for group in definition.input_groups
                    for item in group.fields
                    if item.path == variable and item.kind == "number"
                ),
                None,
            )
            return self.graph.execute(
                self.key(project, candidate, "curve", parameters={"target": target, "variable": variable, "points": points, "range_min": range_min, "range_max": range_max, "stage_name": stage_name, "stage_position_m": stage_position_m, "policy_id": "anchored-grid-v1"}, uses_package=True),
                lambda: self._response_curve_with_domain(
                    handler,
                    runtime,
                    candidate,
                    target,
                    variable,
                    points,
                    axis_range,
                    stage_name,
                    stage_position_m,
                    field,
                ),
            )
        except ResponseCurveNotApplicableError as exc:
            raise InferenceResponseCurveNotApplicableError(str(exc)) from exc
        except ResponseCurveTrainingRangeUnavailableError as exc:
            raise InferenceResponseCurveTrainingRangeUnavailableError(str(exc)) from exc
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
            axis_field = next(
                (
                    item
                    for group in contract.task_definition.input_groups
                    for item in group.fields
                    if item.path == contract.task_definition.curve_axis_path and item.kind == "number"
                ),
                None,
            )
            return self.graph.execute(
                self.key(project, candidate, "curve_family", parameters={"target": target, "vary": vary, "levels": levels, "points": points, "policy_id": "anchored-axis-grid-v1"}, uses_package=True),
                lambda: self._curve_family_with_domain(
                    handler, runtime, candidate, target, vary or None, levels, points, axis_field
                ),
            )
        except ValueError as exc:
            raise InferenceValidationError(str(exc)) from exc

    def response_contour(
        self,
        project_id: str,
        candidate_id: str,
        revision: int,
        target: str,
        x_variable: str,
        y_variable: str,
        points: int,
    ) -> dict[str, Any]:
        project = self.projects.require(project_id)
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        contract = self.registry.contract_for(project.task_id)
        definition = contract.task_definition
        if target not in {item.key for item in definition.outputs}:
            raise InferenceValidationError("この予測タスクにない予測特性です")
        if x_variable == y_variable:
            raise InferenceValidationError("コンターの横軸と縦軸には異なる変数を指定してください")
        surface = next(
            (
                item
                for item in self.registry.module_for(project.task_id).application.workbench_surfaces
                if item.kind == "response_contour"
            ),
            None,
        )
        if surface is None:
            raise InferenceValidationError("この予測タスクは予測コンターに対応していません")
        allowed_axes = set(surface.axis_paths)
        if x_variable not in allowed_axes or y_variable not in allowed_axes:
            raise InferenceValidationError("この予測タスクでコンター軸にできない変数です")
        family = self.registry.candidate_family_for(project.task_id)
        try:
            family.validate_independent_axes(
                (x_variable, y_variable),
                definition,
            )
        except ValueError as exc:
            raise InferenceValidationError(str(exc)) from exc
        self.registry.require_available(project.task_id)
        try:
            runtime = self.resolver.runtime_for(project)
            curve_handler = self.registry.response_curve_for(project.task_id)

            def axis_metadata(variable: str) -> dict[str, Any]:
                payload = curve_handler(
                    runtime, candidate, target, variable, 3, None, None, None
                )
                metadata = dict(payload["variable"])
                training_range = metadata.get("training_range")
                if training_range is None:
                    raise InferenceValidationError(
                        f"{metadata.get('label', variable)}の学習範囲を確認できません"
                    )
                metadata["min"] = float(training_range["min"])
                metadata["max"] = float(training_range["max"])
                return metadata

            x_axis = axis_metadata(x_variable)
            y_axis = axis_metadata(y_variable)
            fields = {
                item.path: item
                for group in definition.input_groups
                for item in group.fields
                if item.kind == "number"
            }
            x_field = fields.get(x_variable)
            y_field = fields.get(y_variable)
            x_values = self._domain_grid(x_axis["min"], x_axis["max"], points, x_field)
            y_values = self._domain_grid(y_axis["min"], y_axis["max"], points, y_field)
            if len(x_values) < 2 or len(y_values) < 2:
                raise InferenceValidationError("コンター軸の数値domainに異なる候補値が2点以上必要です")

            def compute() -> dict[str, Any]:
                cells: list[dict[str, Any]] = []
                output_values: list[float] = []
                for y_value in y_values:
                    for x_value in x_values:
                        adjusted = family.update(
                            candidate,
                            {x_variable: x_value, y_variable: y_value},
                            definition,
                            balance=True,
                        )
                        try:
                            self.registry.validate_candidate(project.task_id, adjusted)
                        except ValueError as exc:
                            cells.append(
                                {
                                    "x": x_value,
                                    "y": y_value,
                                    "displayable": False,
                                    "invalid_reason": str(exc),
                                }
                            )
                            continue
                        result = runtime.predict_core(
                            adjusted,
                            detailed=False,
                            target_values=project.target_values,
                        )
                        prediction = Prediction.model_validate(
                            result["predictions"][target]
                        )
                        support = Support.model_validate(
                            runtime.support_by_target(adjusted)[target]  # type: ignore[attr-defined]
                        )
                        if support.status != "extrapolated":
                            output_values.append(prediction.value)
                        cells.append(
                            {
                                "x": x_value,
                                "y": y_value,
                                "prediction": prediction,
                                "support": support,
                                "displayable": support.status != "extrapolated",
                            }
                        )
                return {
                    "task_id": project.task_id,
                    "candidate_id": candidate.id,
                    "candidate_revision": candidate.revision,
                    "model_package_manifest_digest": project.model_package_manifest_digest,
                    "target": target,
                    "x_axis": x_axis,
                    "y_axis": y_axis,
                    "x_values": x_values,
                    "y_values": y_values,
                    "cells": cells,
                    "output_range": (
                        None
                        if not output_values
                        else {"min": min(output_values), "max": max(output_values)}
                    ),
                    "grid_shape": (len(y_values), len(x_values)),
                    "policy_id": "training-range-supported-grid-v1",
                }

            return self.graph.execute(
                self.key(
                    project,
                    candidate,
                    "response_contour",
                    parameters={
                        "target": target,
                        "x_variable": x_variable,
                        "y_variable": y_variable,
                        "points": points,
                        "candidate_revision": candidate.revision,
                        "policy_id": "training-range-supported-grid-v1",
                    },
                    uses_package=True,
                    uses_support=True,
                ),
                compute,
            )
        except InferenceValidationError:
            raise
        except ValueError as exc:
            raise InferenceValidationError(str(exc)) from exc

    @staticmethod
    def _uniform_grid(minimum: float, maximum: float, points: int) -> list[float]:
        if not math.isfinite(minimum) or not math.isfinite(maximum) or minimum >= maximum:
            raise InferenceValidationError("コンター軸の学習範囲が不正です")
        step = (maximum - minimum) / (points - 1)
        return [round(minimum + step * index, 8) for index in range(points)]

    def _domain_grid(self, minimum: float, maximum: float, points: int, field: Any) -> list[float]:
        if field is None:
            return self._uniform_grid(minimum, maximum, points)
        return numeric_domain_grid(minimum, maximum, points, field=field)

    @staticmethod
    def _response_curve_with_domain(
        handler: Any,
        runtime: Any,
        candidate: Candidate,
        target: str,
        variable: str,
        points: int,
        axis_range: tuple[float, float] | None,
        stage_name: str | None,
        stage_position_m: float | None,
        field: Any,
    ) -> dict[str, Any]:
        with use_numeric_domain(field):
            return handler(
                runtime, candidate, target, variable, points, axis_range, stage_name, stage_position_m
            )

    @staticmethod
    def _curve_family_with_domain(
        handler: Any,
        runtime: Any,
        candidate: Candidate,
        target: str,
        vary: str | None,
        levels: int,
        points: int,
        axis_field: Any,
    ) -> dict[str, Any]:
        with use_numeric_domain(axis_field):
            return handler(runtime, candidate, target, vary, levels, points)

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
        except TaskUnavailableError:
            raise
        except TaskRegistryError as exc:
            raise InferenceValidationError(str(exc)) from exc

    def key(self, project: Project, candidate: Candidate, operation: str, *, parameters: Mapping[str, Any] | None = None, uses_package: bool = False, uses_support: bool = False) -> InferenceKey:
        if candidate.blend_validation.status == "invalid":
            reasons = " / ".join(issue.message for issue in candidate.blend_validation.issues)
            raise InferenceValidationError(
                f"配合がDesign Spaceを満たしていないため推論できません: {reasons}"
            )
        resolved = self.resolver.resolve(project)
        identity = resolved.identity
        canonical = self.registry.validate_candidate(project.task_id, candidate).model_dump(mode="json", exclude={"provenance"})
        if candidate.blend is not None:
            canonical["blend"] = candidate.blend.model_input_payload()
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
