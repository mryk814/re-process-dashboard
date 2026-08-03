"""Explicit, bounded goal search over safe Prediction Graph design variables."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from decision_workbench.application.chain.graph_execution import (
    PredictionGraphExecutionUseCase,
)
from decision_workbench.application.chain.graph_plan import (
    PredictionGraphPlanningUseCase,
)
from decision_workbench.application.chain.plan import (
    ChainExecutionError,
    ChainPlanningUseCase,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
)
from decision_workbench.contracts.prediction_graph_planning_contracts import (
    PredictionGraphDesignSpace,
    PredictionGraphDesignVariable,
    PredictionGraphGoalSearchOutput,
    PredictionGraphGoalSearchPoint,
    PredictionGraphGoalSearchRequest,
    PredictionGraphGoalSearchRun,
    PredictionGraphObjective,
    PredictionGraphObjectiveInput,
    PredictionGraphObjectiveTerm,
    PredictionGraphPromotionRequest,
)
from decision_workbench.contracts.task_contracts import (
    NumericRange,
    PredictionGraphGoalSearchReference,
    PredictionGraphGoalSearchSourceRef,
)
from decision_workbench.domain.proposal_generation import (
    _latin_hypercube_unit,
    _set_value,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import TaskRegistry


class PredictionGraphGoalSearchUseCase:
    def __init__(
        self,
        store: Store,
        planning: PredictionGraphPlanningUseCase,
        execution: PredictionGraphExecutionUseCase,
        task_registry: TaskRegistry,
    ) -> None:
        self.store = store
        self.planning = planning
        self.stage_executor = execution.stage_executor
        self.task_registry = task_registry

    def _resolved(self, project_id: str):
        return self.planning._resolve_project(project_id)

    def design_space(self, project_id: str) -> PredictionGraphDesignSpace:
        definition, revision, identity, _adapter = self._resolved(project_id)
        stages = {stage.stage_id: stage for stage in definition.stages}
        variables: list[PredictionGraphDesignVariable] = []
        for graph_input in definition.inputs:
            source = graph_input.value_source
            if (
                graph_input.role != "design_variable"
                or source.source_kind != "candidate"
                or graph_input.port.value_kind == "sparse_blend"
            ):
                continue
            fields_and_bindings = []
            for binding in definition.bindings:
                if (
                    binding.source.source_kind != "external"
                    or binding.source.path != graph_input.input_id
                ):
                    continue
                stage = stages[binding.target_stage_id]
                if stage.stage_kind != "task":
                    continue
                task = self.task_registry.contract_for(
                    stage.contract_id
                ).task_definition
                field = next(
                    (
                        item
                        for group in task.input_groups
                        for item in group.fields
                        if item.path == binding.target_input_path
                    ),
                    None,
                )
                if field is not None and field.editable:
                    fields_and_bindings.append((field, binding))
            if not fields_and_bindings:
                continue
            fields = [field for field, _binding in fields_and_bindings]
            affected_nodes = set(
                definition.topology.affected_nodes_by_input[graph_input.input_id]
            )
            affected_outputs = tuple(
                item.output_id
                for item in definition.decision_outputs
                if item.source_stage_id in affected_nodes
            )
            if not affected_outputs:
                continue
            if graph_input.port.value_kind == "number":
                if any(
                    field.numeric_domain_kind != "continuous"
                    or field.search_scale != "linear"
                    or field.step is not None
                    for field in fields
                ):
                    continue
                target_ranges = [field.default_range for field in fields]
                if any(item is None for item in target_ranges):
                    continue
                source_ranges = [
                    ChainPlanningUseCase._external_numeric_range(
                        target_range,
                        binding,
                    )
                    for target_range, (_field, binding) in zip(
                        target_ranges,
                        fields_and_bindings,
                        strict=True,
                    )
                    if target_range is not None
                ]
                lower = max(item.min for item in source_ranges)
                upper = min(item.max for item in source_ranges)
                if lower >= upper:
                    continue
                variables.append(
                    PredictionGraphDesignVariable(
                        input_id=graph_input.input_id,
                        candidate_path=source.candidate_path,
                        label=graph_input.label,
                        kind="number",
                        sampling_policy="continuous_linear",
                        unit=graph_input.port.unit,
                        numeric_range=NumericRange(min=lower, max=upper),
                        affected_output_ids=affected_outputs,
                    )
                )
            else:
                choices = tuple(
                    value
                    for value in fields[0].choices
                    if all(value in field.choices for field in fields[1:])
                )
                if choices:
                    variables.append(
                        PredictionGraphDesignVariable(
                            input_id=graph_input.input_id,
                            candidate_path=source.candidate_path,
                            label=graph_input.label,
                            kind="categorical",
                            sampling_policy="categorical_uniform",
                            choices=choices,
                            affected_output_ids=affected_outputs,
                        )
                    )
        if not variables:
            raise ChainExecutionError(
                "安全に解決できるGraph design variableがありません"
            )
        return PredictionGraphDesignSpace(
            graph_revision_id=identity.graph_revision_id,
            graph_revision_digest=revision.revision_digest,
            variables=tuple(variables),
        )

    def create_objective(
        self, project_id: str, payload: PredictionGraphObjectiveInput
    ) -> PredictionGraphObjective:
        definition, revision, identity, _adapter = self._resolved(project_id)
        candidate = self.store.get_candidate_revision(
            payload.incumbent_candidate_id,
            payload.incumbent_candidate_revision,
            project_id,
        )
        if candidate is None:
            raise ChainExecutionError("incumbent Candidate revisionが見つかりません")
        by_id = {item.output_id: item for item in definition.decision_outputs}
        surfaces = self.store.get_chain_stage_contract_surfaces(
            identity.graph_revision_id
        )

        def resolve(term, expected_role: str) -> PredictionGraphObjectiveTerm:
            output = by_id.get(term.output_id)
            if output is None or output.role != expected_role:
                raise ChainExecutionError(
                    f"{expected_role}として宣言されたDecision Outputを選択してください"
                )
            if output.evidence is None:
                raise ChainExecutionError(
                    "Decision Outputの単位・方向evidenceがありません"
                )
            surface = surfaces.get(output.source_stage_id)
            port = (
                next(
                    (
                        item
                        for item in surface.output_ports
                        if item.path == output.source_output_key
                    ),
                    None,
                )
                if surface is not None
                else None
            )
            if port is None or port.value_kind != "number" or port.unit is None:
                raise ChainExecutionError(
                    "Objectiveには数値単位を持つStage outputだけを選択できます"
                )
            if output.evidence.unit_or_scale != port.unit:
                raise ChainExecutionError(
                    "Decision Output evidenceの単位がStage output portと一致しません"
                )
            if output.evidence.goal_direction != term.direction:
                raise ChainExecutionError(
                    "Objective方向がDecision Output evidenceと一致しません"
                )
            if payload.use_context == "production" and (
                output.evidence.evidence_kind != "measured"
                or output.evidence.production_use == "prohibited"
            ):
                raise ChainExecutionError(
                    "production Objectiveには利用許可済みmeasured outputだけを使えます"
                )
            return PredictionGraphObjectiveTerm(
                **term.model_dump(),
                source_stage_id=output.source_stage_id,
                source_output_key=output.source_output_key,
                unit=port.unit,
                evidence_kind=output.evidence.evidence_kind,
            )

        objective = PredictionGraphObjective(
            objective_id=str(uuid.uuid4()),
            project_id=project_id,
            name=payload.name,
            graph_revision_id=identity.graph_revision_id,
            graph_revision_digest=revision.revision_digest,
            project_binding_revision=identity.project_binding.revision,
            project_binding_digest=identity.project_binding.digest,
            primary=resolve(payload.primary, "primary_objective"),
            hard_constraint=resolve(payload.hard_constraint, "hard_constraint"),
            incumbent_candidate_id=candidate.id,
            incumbent_candidate_revision=candidate.revision,
            use_context=payload.use_context,
            created_at=datetime.now(UTC),
        )
        return self.store.insert_prediction_graph_objective(objective)

    @staticmethod
    def _achieved(value: Any, direction: str, threshold: float) -> bool | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value >= threshold if direction == "at_least" else value <= threshold

    def _evaluate(
        self,
        project_id: str,
        candidate: Candidate,
    ) -> dict[str, PredictionGraphGoalSearchOutput]:
        definition, revision, identity, adapter = self._resolved(project_id)
        prepared = self.planning.prepare_candidate(
            project_id, CandidateInput.model_validate(candidate.model_dump())
        )
        candidate = Candidate.model_validate(
            {
                **candidate.model_dump(),
                **prepared.model_dump(),
            }
        )
        external = self.planning._external_values(
            definition, identity, adapter, candidate
        )
        outputs_by_stage: dict[str, dict[str, Any]] = {}
        payloads_by_stage: dict[str, dict[str, Any]] = {}
        statuses: dict[str, str] = {}
        errors: dict[str, str] = {}
        blockers: dict[str, tuple[str, ...]] = {}
        stage_by_id = {stage.stage_id: stage for stage in revision.stages}
        for layer in definition.topology.topological_layers:
            for stage_id in layer:
                dependencies = definition.topology.direct_dependencies[stage_id]
                blocked_by = tuple(
                    item for item in dependencies if statuses.get(item) != "latest"
                )
                if blocked_by:
                    statuses[stage_id] = "blocked_by_upstream"
                    blockers[stage_id] = blocked_by
                    continue
                stage = stage_by_id[stage_id]
                try:
                    canonical = self.stage_executor._canonical_input(
                        definition, stage_id, external, outputs_by_stage
                    )
                    payload, outputs = self.stage_executor._run_stage(
                        stage, canonical, candidate, adapter
                    )
                    payloads_by_stage[stage_id] = payload
                    outputs_by_stage[stage_id] = outputs
                    statuses[stage_id] = "latest"
                except Exception as exc:  # noqa: BLE001 - branch failure is Run evidence
                    statuses[stage_id] = "failed"
                    errors[stage_id] = str(exc)
        results: dict[str, PredictionGraphGoalSearchOutput] = {}
        for output in definition.decision_outputs:
            status = statuses.get(output.source_stage_id, "unavailable")
            value = None
            prediction = None
            support = None
            if status == "latest":
                value = outputs_by_stage.get(output.source_stage_id, {}).get(
                    output.source_output_key
                )
                if value is None:
                    status = "unavailable"
                payload = payloads_by_stage.get(output.source_stage_id, {})
                predictions = payload.get("predictions")
                if isinstance(predictions, dict):
                    raw_prediction = predictions.get(output.source_output_key)
                    if isinstance(raw_prediction, dict):
                        prediction = raw_prediction
                raw_support = payload.get("support")
                if isinstance(raw_support, dict):
                    support = raw_support
            results[output.output_id] = PredictionGraphGoalSearchOutput(
                status=status,
                value=value,
                prediction=prediction,
                support=support,
                error=errors.get(output.source_stage_id),
                blocked_by_stage_ids=blockers.get(output.source_stage_id, ()),
            )
        return results

    def run(
        self, project_id: str, payload: PredictionGraphGoalSearchRequest
    ) -> PredictionGraphGoalSearchRun:
        objective = self.store.get_prediction_graph_objective(
            project_id, payload.objective_id
        )
        if objective is None:
            raise ChainExecutionError("保存済みGraph Objectiveが見つかりません")
        definition, revision, identity, _adapter = self._resolved(project_id)
        if (
            objective.graph_revision_id != identity.graph_revision_id
            or objective.project_binding_revision != identity.project_binding.revision
            or objective.graph_revision_digest != revision.revision_digest
            or objective.project_binding_digest != identity.project_binding.digest
        ):
            raise ChainExecutionError(
                "Objective identityがProjectの固定Graphと一致しません"
            )
        if (
            payload.base_candidate_id != objective.incumbent_candidate_id
            or payload.base_candidate_revision != objective.incumbent_candidate_revision
        ):
            raise ChainExecutionError(
                "goal-search baseはObjectiveのincumbent Candidate revision"
                "と一致する必要があります"
            )
        base = self.store.get_candidate_revision(
            payload.base_candidate_id, payload.base_candidate_revision, project_id
        )
        if base is None:
            raise ChainExecutionError("base Candidate revisionが見つかりません")
        design_space = self.design_space(project_id)
        unit = _latin_hypercube_unit(
            payload.sample_count, len(design_space.variables), payload.seed
        )
        points: list[PredictionGraphGoalSearchPoint] = []
        required_ids = {
            item.output_id
            for item in definition.decision_outputs
            if item.required_for_complete_result
        }
        for row_index in range(payload.sample_count):
            candidate = base.model_copy(deep=True)
            applied: dict[str, float | str] = {}
            for column, variable in enumerate(design_space.variables):
                position = float(unit[row_index, column])
                if variable.kind == "number":
                    assert variable.numeric_range is not None
                    value: float | str = variable.numeric_range.min + position * (
                        variable.numeric_range.max - variable.numeric_range.min
                    )
                else:
                    value = variable.choices[
                        min(
                            int(position * len(variable.choices)),
                            len(variable.choices) - 1,
                        )
                    ]
                candidate = _set_value(candidate, variable.candidate_path, value)
                applied[variable.input_id] = value
            candidate_input = CandidateInput.model_validate(candidate.model_dump())
            try:
                outputs = self._evaluate(project_id, candidate)
            except Exception as exc:  # noqa: BLE001 - reject only this generated point
                points.append(
                    PredictionGraphGoalSearchPoint(
                        point_index=row_index,
                        candidate=candidate_input,
                        input_values=applied,
                        outputs={},
                        primary_achieved=None,
                        hard_constraint_achieved=None,
                        feasible=False,
                        score=None,
                        rejection_reason=str(exc),
                    )
                )
                continue
            primary_output = outputs[objective.primary.output_id]
            constraint_output = outputs[objective.hard_constraint.output_id]
            required_latest = all(
                outputs[item].status == "latest" for item in required_ids
            )
            primary_value = primary_output.value
            constraint_value = constraint_output.value
            primary_achieved = (
                self._achieved(
                    primary_value,
                    objective.primary.direction,
                    objective.primary.threshold,
                )
                if primary_output.status == "latest"
                else None
            )
            constraint_achieved = (
                self._achieved(
                    constraint_value,
                    objective.hard_constraint.direction,
                    objective.hard_constraint.threshold,
                )
                if constraint_output.status == "latest"
                else None
            )
            feasible = (
                required_latest
                and primary_achieved is not None
                and constraint_achieved is True
            )
            score = (
                float(primary_value) - objective.primary.threshold
                if feasible and objective.primary.direction == "at_least"
                else objective.primary.threshold - float(primary_value)
                if feasible
                else None
            )
            points.append(
                PredictionGraphGoalSearchPoint(
                    point_index=row_index,
                    candidate=candidate_input,
                    input_values=applied,
                    outputs=outputs,
                    primary_achieved=primary_achieved,
                    hard_constraint_achieved=constraint_achieved,
                    feasible=feasible,
                    score=score,
                    rejection_reason=(
                        None
                        if feasible
                        else "required_or_objective_output_unavailable"
                        if (
                            not required_latest
                            or primary_output.status != "latest"
                            or constraint_output.status != "latest"
                        )
                        else "hard_constraint_not_achieved"
                    ),
                )
            )
        selected = tuple(
            item.point_index
            for item in sorted(
                (item for item in points if item.feasible),
                key=lambda item: (-float(item.score or 0), item.point_index),
            )[:3]
        )
        run = PredictionGraphGoalSearchRun(
            run_id=str(uuid.uuid4()),
            project_id=project_id,
            graph_revision_id=identity.graph_revision_id,
            graph_revision_digest=revision.revision_digest,
            project_binding_revision=identity.project_binding.revision,
            project_binding_digest=identity.project_binding.digest,
            base_candidate_id=base.id,
            base_candidate_revision=base.revision,
            base_candidate_digest=semantic_digest(base.model_dump(mode="json")),
            objective=objective,
            objective_digest=objective.digest,
            design_space=design_space,
            design_space_digest=design_space.digest,
            package_manifest_digests={
                stage.stage_id: stage.package_manifest_digest
                for stage in revision.stages
            },
            seed=payload.seed,
            points=tuple(points),
            selected_point_indices=selected,
            created_at=datetime.now(UTC),
        )
        return self.store.insert_prediction_graph_goal_search_run(run)

    def promote(
        self,
        project_id: str,
        run_id: str,
        payload: PredictionGraphPromotionRequest,
    ):
        run = self.store.get_prediction_graph_goal_search_run(project_id, run_id)
        if run is None:
            raise ChainExecutionError("Graph goal-search Runが見つかりません")
        if payload.point_index not in run.selected_point_indices:
            raise ChainExecutionError("選択済みgoal-search resultだけを昇格できます")
        _definition, revision, identity, _adapter = self._resolved(project_id)
        if (
            run.graph_revision_id != identity.graph_revision_id
            or run.graph_revision_digest != revision.revision_digest
            or run.project_binding_revision != identity.project_binding.revision
            or run.project_binding_digest != identity.project_binding.digest
        ):
            raise ChainExecutionError(
                "保存済みRunのGraphまたはProject bindingが現在値と一致しません"
            )
        point = next(
            item for item in run.points if item.point_index == payload.point_index
        )
        candidate = point.candidate.model_copy(
            update={
                "name": payload.name,
                "provenance": PredictionGraphGoalSearchSourceRef(
                    source_kind="prediction_graph_goal_search",
                    source_ref=PredictionGraphGoalSearchReference(
                        run_id=run.run_id,
                        point_index=payload.point_index,
                        objective_digest=run.objective_digest,
                        design_space_digest=run.design_space_digest,
                    ),
                ),
            }
        )
        prepared = self.planning.prepare_candidate(project_id, candidate)
        return self.store.create_candidate(prepared, project_id)
