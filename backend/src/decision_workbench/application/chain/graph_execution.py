"""Dependency-correct serial execution for Prediction Graph v1."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping
import time
import uuid

from decision_workbench.application.chain.execution import (
    ChainExecutionCoordinator,
)
from decision_workbench.application.chain.graph_plan import (
    PredictionGraphPlanningUseCase,
)
from decision_workbench.application.chain.plan import ChainExecutionError
from decision_workbench.application.chain.stage_execution import (
    ChainStageExecutor,
)
from decision_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapter,
)
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.chain_contracts import (
    ChainStageRevision,
    DecisionOutput,
    PredictionGraphDefinition,
    PredictionGraphProjectIdentity,
    PredictionGraphRevision,
)
from decision_workbench.contracts.chain_execution_contracts import (
    PredictionGraphExecution,
    PredictionGraphStageExecution,
    PredictionGraphTerminalOutput,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import Store


def _now() -> datetime:
    return datetime.now(UTC)


class PredictionGraphExecutionUseCase:
    """Run one immutable Graph Revision without assuming tuple dependencies."""

    def __init__(
        self,
        planning: PredictionGraphPlanningUseCase,
        stage_executor: ChainStageExecutor,
        coordinator: ChainExecutionCoordinator,
    ) -> None:
        self.planning = planning
        self.stage_executor = stage_executor
        self.coordinator = coordinator
        self.store: Store = planning.store

    def execute(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        request_id: str | None = None,
        debounce_ms: int = 250,
    ) -> PredictionGraphExecution:
        (
            candidate,
            definition,
            revision,
            identity,
            adapter,
            external,
        ) = self.planning.resolve(
            project_id,
            candidate_id,
            candidate_revision,
        )
        if candidate.blend_validation.status == "invalid":
            reasons = " / ".join(
                issue.message for issue in candidate.blend_validation.issues
            )
            raise ChainExecutionError(
                "配合がDesign Spaceを満たしていないためGraphを実行できません: "
                f"{reasons}"
            )
        request_id = request_id or str(uuid.uuid4())
        scope_id = self.store.chain_execution_scope(project_id, candidate_id)
        generation = self.store.claim_chain_execution(
            project_id,
            candidate_id,
            candidate.revision,
            request_id,
        )
        previous = self.store.get_prediction_graph_execution(
            project_id,
            candidate_id,
        )
        if generation is None:
            return self._superseded(
                project_id,
                candidate,
                identity,
                revision,
                request_id,
                previous,
            )
        self.coordinator.begin(scope_id, request_id)
        if debounce_ms:
            time.sleep(debounce_ms / 1000)
        if (
            self.store.chain_execution_generation(
                project_id,
                candidate_id,
                request_id,
            )
            != generation
        ):
            return self._superseded(
                project_id,
                candidate,
                identity,
                revision,
                request_id,
                previous,
            )

        previous_by_stage = (
            {stage.stage_id: stage for stage in previous.stages}
            if previous is not None
            else {}
        )
        created_at = _now()
        stage_by_id = {stage.stage_id: stage for stage in revision.stages}
        stage_order = tuple(
            stage_id
            for layer in definition.topology.topological_layers
            for stage_id in layer
        )
        stages: list[PredictionGraphStageExecution] = []
        evidence_by_stage: dict[str, PredictionGraphStageExecution] = {}
        outputs_by_stage: dict[str, dict[str, Any]] = {}
        for stage_id in stage_order:
            stage = stage_by_id[stage_id]
            dependencies = definition.topology.direct_dependencies[stage_id]
            blockers = tuple(
                dependency
                for dependency in dependencies
                if evidence_by_stage[dependency].status
                in {"failed", "blocked_by_upstream", "unavailable"}
            )
            if blockers:
                blocked = self._retained(
                    previous_by_stage.get(stage_id),
                    stage=stage,
                    status="blocked_by_upstream",
                    requested_input_digest=self._pending_digest(
                        stage_id,
                        candidate.revision,
                        blockers,
                        identity.project_binding.digest,
                    ),
                    canonical_input=(
                        previous_by_stage[stage_id].canonical_input
                        if stage_id in previous_by_stage
                        else {}
                    ),
                    blocked_by_stage_ids=blockers,
                )
                stages.append(blocked)
                evidence_by_stage[stage_id] = blocked
                continue
            try:
                canonical_input = self.stage_executor._canonical_input(
                    definition,
                    stage_id,
                    external,
                    outputs_by_stage,
                )
            except ChainExecutionError as exc:
                failed = self._retained(
                    previous_by_stage.get(stage_id),
                    stage=stage,
                    status="failed",
                    requested_input_digest=semantic_digest(
                        {
                            "stage_id": stage_id,
                            "binding_error": str(exc),
                            "available_upstream": sorted(outputs_by_stage),
                        }
                    ),
                    canonical_input={},
                    error=str(exc),
                    started_at=_now(),
                )
                stages.append(failed)
                evidence_by_stage[stage_id] = failed
                continue

            input_digest = self.stage_executor.input_digest(stage, canonical_input)
            previous_stage = previous_by_stage.get(stage_id)
            running = self._retained(
                previous_stage,
                stage=stage,
                status="running",
                requested_input_digest=input_digest,
                canonical_input=canonical_input,
                started_at=_now(),
            )
            stages.append(running)
            evidence_by_stage[stage_id] = running
            running_execution = self._execution(
                request_id=request_id,
                project_id=project_id,
                candidate=candidate,
                identity=identity,
                definition=definition,
                revision=revision,
                stages=self._with_pending(
                    stages,
                    stage_order[len(stages):],
                    stage_by_id,
                    previous_by_stage,
                    candidate.revision,
                    identity.project_binding.digest,
                ),
                outputs_by_stage=outputs_by_stage,
                status="running",
                created_at=created_at,
            )
            if not self.store.save_prediction_graph_execution_if_current(
                running_execution,
                generation,
            ):
                return self._superseded(
                    project_id,
                    candidate,
                    identity,
                    revision,
                    request_id,
                    self.store.get_prediction_graph_execution(
                        project_id,
                        candidate_id,
                    ),
                )

            memo_key = self.stage_executor._memo_key(stage, input_digest)
            memo = self.store.get_chain_stage_memo(memo_key)
            try:
                self.stage_executor._assert_runtime_identity(
                    stage,
                    candidate,
                    adapter,
                )
                if memo is None:
                    payload, outputs = self.stage_executor._run_stage(
                        stage,
                        canonical_input,
                        candidate,
                        adapter,
                    )
                    self.store.put_chain_stage_memo(
                        memo_key=memo_key,
                        stage_id=stage.stage_id,
                        input_digest=input_digest,
                        contract_digest=stage.contract_digest,
                        package_manifest_digest=stage.package_manifest_digest,
                        canonical_input=canonical_input,
                        result={"payload": payload, "outputs": outputs},
                    )
                    cache_hit = False
                else:
                    payload = memo["result"]["payload"]
                    outputs = memo["result"]["outputs"]
                    cache_hit = True
            except Exception as exc:
                failed = self._retained(
                    previous_stage,
                    stage=stage,
                    status="failed",
                    requested_input_digest=input_digest,
                    canonical_input=canonical_input,
                    error=str(exc),
                    started_at=running.started_at,
                )
                stages[-1] = failed
                evidence_by_stage[stage_id] = failed
                continue
            if (
                self.store.chain_execution_generation(
                    project_id,
                    candidate_id,
                    request_id,
                )
                != generation
            ):
                return self._superseded(
                    project_id,
                    candidate,
                    identity,
                    revision,
                    request_id,
                    self.store.get_prediction_graph_execution(
                        project_id,
                        candidate_id,
                    ),
                )
            latest = PredictionGraphStageExecution(
                stage_id=stage_id,
                output_definitions=self.stage_executor._output_definitions(stage),
                status="latest",
                requested_input_digest=input_digest,
                result_input_digest=input_digest,
                contract_digest=stage.contract_digest,
                package_manifest_digest=stage.package_manifest_digest,
                canonical_input=canonical_input,
                result=payload,
                cache_hit=cache_hit,
                started_at=running.started_at,
                completed_at=_now(),
            )
            stages[-1] = latest
            evidence_by_stage[stage_id] = latest
            outputs_by_stage[stage_id] = dict(outputs)

        execution = self._execution(
            request_id=request_id,
            project_id=project_id,
            candidate=candidate,
            identity=identity,
            definition=definition,
            revision=revision,
            stages=tuple(stages),
            outputs_by_stage=outputs_by_stage,
            created_at=created_at,
        )
        if self.store.save_prediction_graph_execution_if_current(
            execution,
            generation,
        ):
            return execution
        return self._superseded(
            project_id,
            candidate,
            identity,
            revision,
            request_id,
            self.store.get_prediction_graph_execution(project_id, candidate_id),
        )

    def mark_candidate_changed(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        request_id: str,
        generation: int,
    ) -> PredictionGraphExecution | None:
        previous = self.store.get_prediction_graph_execution(
            project_id,
            candidate_id,
        )
        if previous is None:
            return None
        (
            candidate,
            definition,
            revision,
            identity,
            adapter,
            external,
        ) = self.planning.resolve(
            project_id,
            candidate_id,
            candidate_revision,
        )
        previous_by_stage = {stage.stage_id: stage for stage in previous.stages}
        stage_by_id = {stage.stage_id: stage for stage in revision.stages}
        stage_order = tuple(
            stage_id
            for layer in definition.topology.topological_layers
            for stage_id in layer
        )
        evidence: dict[str, PredictionGraphStageExecution] = {}
        outputs: dict[str, dict[str, Any]] = {}
        stages: list[PredictionGraphStageExecution] = []
        for stage_id in stage_order:
            stage = stage_by_id[stage_id]
            dependencies = definition.topology.direct_dependencies[stage_id]
            stale_dependencies = tuple(
                dependency
                for dependency in dependencies
                if evidence[dependency].status != "latest"
            )
            previous_stage = previous_by_stage.get(stage_id)
            if stale_dependencies:
                retained = self._retained(
                    previous_stage,
                    stage=stage,
                    status="stale",
                    requested_input_digest=self._pending_digest(
                        stage_id,
                        candidate_revision,
                        stale_dependencies,
                        identity.project_binding.digest,
                    ),
                    canonical_input=(
                        previous_stage.canonical_input
                        if previous_stage is not None
                        else {}
                    ),
                )
            else:
                canonical_input = self.stage_executor._canonical_input(
                    definition,
                    stage_id,
                    external,
                    outputs,
                )
                input_digest = self.stage_executor.input_digest(stage, canonical_input)
                if (
                    previous_stage is not None
                    and previous_stage.result is not None
                    and previous_stage.result_input_digest == input_digest
                ):
                    retained = PredictionGraphStageExecution(
                        stage_id=stage_id,
                        output_definitions=(
                            previous_stage.output_definitions
                            or self.stage_executor._output_definitions(stage)
                        ),
                        status="latest",
                        requested_input_digest=input_digest,
                        result_input_digest=input_digest,
                        contract_digest=stage.contract_digest,
                        package_manifest_digest=stage.package_manifest_digest,
                        canonical_input=canonical_input,
                        result=previous_stage.result,
                        started_at=previous_stage.started_at,
                        completed_at=previous_stage.completed_at,
                    )
                    outputs[stage_id] = (
                        self.stage_executor._outputs_from_payload(
                            stage,
                            previous_stage.result,
                            adapter,
                        )
                    )
                else:
                    retained = self._retained(
                        previous_stage,
                        stage=stage,
                        status="stale",
                        requested_input_digest=input_digest,
                        canonical_input=canonical_input,
                    )
            stages.append(retained)
            evidence[stage_id] = retained
        execution = self._execution(
            request_id=request_id,
            project_id=project_id,
            candidate=candidate,
            identity=identity,
            definition=definition,
            revision=revision,
            stages=tuple(stages),
            outputs_by_stage=outputs,
            created_at=previous.created_at,
        )
        return (
            execution
            if self.store.save_prediction_graph_execution_if_current(
                execution,
                generation,
            )
            else self.store.get_prediction_graph_execution(
                project_id,
                candidate_id,
            )
        )

    def _execution(
        self,
        *,
        request_id: str,
        project_id: str,
        candidate: Candidate,
        identity: PredictionGraphProjectIdentity,
        definition: PredictionGraphDefinition,
        revision: PredictionGraphRevision,
        stages: tuple[PredictionGraphStageExecution, ...],
        outputs_by_stage: Mapping[str, Mapping[str, Any]],
        created_at: datetime,
        status: str | None = None,
    ) -> PredictionGraphExecution:
        by_stage = {stage.stage_id: stage for stage in stages}
        terminal_outputs = tuple(
            self._terminal_output(item, by_stage, outputs_by_stage)
            for item in definition.decision_outputs
        )
        required_latest = all(
            item.status == "latest"
            for item in terminal_outputs
            if item.required_for_complete_result
        )
        any_latest = any(item.status == "latest" for item in terminal_outputs)
        summary_status = (
            status
            or (
                "complete"
                if required_latest
                else "partial"
                if any_latest
                else "unavailable"
            )
        )
        return PredictionGraphExecution(
            request_id=request_id,
            project_id=project_id,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
            graph_revision_id=identity.graph_revision_id,
            graph_revision_digest=identity.graph_revision_digest,
            project_binding_revision=identity.project_binding.revision,
            project_binding_digest=identity.project_binding.digest,
            status=summary_status,
            stages=stages,
            terminal_outputs=terminal_outputs,
            failed_stage_ids=tuple(
                item.stage_id for item in stages if item.status == "failed"
            ),
            blocked_stage_ids=tuple(
                item.stage_id
                for item in stages
                if item.status == "blocked_by_upstream"
            ),
            created_at=created_at,
            updated_at=_now(),
        )

    @staticmethod
    def _terminal_output(
        output: DecisionOutput,
        stages: Mapping[str, PredictionGraphStageExecution],
        outputs_by_stage: Mapping[str, Mapping[str, Any]],
    ) -> PredictionGraphTerminalOutput:
        stage = stages[output.source_stage_id]
        common = {
            "output_id": output.output_id,
            "source_stage_id": output.source_stage_id,
            "source_output_key": output.source_output_key,
            "role": output.role,
            "required_for_complete_result": output.required_for_complete_result,
        }
        if stage.status == "latest":
            value = outputs_by_stage.get(output.source_stage_id, {}).get(
                output.source_output_key
            )
            if value is None:
                return PredictionGraphTerminalOutput(
                    **common,
                    status="unavailable",
                    error="latest Stage result does not contain the terminal output",
                )
            return PredictionGraphTerminalOutput(
                **common,
                status="latest",
                value=value,
                result_input_digest=stage.result_input_digest,
            )
        return PredictionGraphTerminalOutput(
            **common,
            status=stage.status,
            error=stage.error,
            blocked_by_stage_ids=stage.blocked_by_stage_ids,
        )

    def _retained(
        self,
        previous: PredictionGraphStageExecution | None,
        *,
        stage: ChainStageRevision,
        status: str,
        requested_input_digest: str,
        canonical_input: dict[str, Any],
        error: str | None = None,
        blocked_by_stage_ids: tuple[str, ...] = (),
        started_at: datetime | None = None,
    ) -> PredictionGraphStageExecution:
        return PredictionGraphStageExecution(
            stage_id=stage.stage_id,
            output_definitions=self.stage_executor._output_definitions(stage),
            status=status,
            requested_input_digest=requested_input_digest,
            result_input_digest=(
                previous.result_input_digest if previous is not None else None
            ),
            contract_digest=stage.contract_digest,
            package_manifest_digest=stage.package_manifest_digest,
            canonical_input=canonical_input,
            result=previous.result if previous is not None else None,
            error=error,
            blocked_by_stage_ids=blocked_by_stage_ids,
            started_at=started_at,
            completed_at=_now() if status == "failed" else None,
        )

    def _with_pending(
        self,
        completed: list[PredictionGraphStageExecution],
        pending_stage_ids: tuple[str, ...],
        stage_by_id: Mapping[str, ChainStageRevision],
        previous_by_stage: Mapping[str, PredictionGraphStageExecution],
        candidate_revision: int,
        project_binding_digest: str,
    ) -> tuple[PredictionGraphStageExecution, ...]:
        pending = [
            self._retained(
                previous_by_stage.get(stage_id),
                stage=stage_by_id[stage_id],
                status="stale",
                requested_input_digest=self._pending_digest(
                    stage_id,
                    candidate_revision,
                    (),
                    project_binding_digest,
                ),
                canonical_input=(
                    previous_by_stage[stage_id].canonical_input
                    if stage_id in previous_by_stage
                    else {}
                ),
            )
            for stage_id in pending_stage_ids
        ]
        return tuple([*completed, *pending])

    @staticmethod
    def _pending_digest(
        stage_id: str,
        candidate_revision: int,
        dependencies: tuple[str, ...],
        project_binding_digest: str,
    ) -> str:
        return semantic_digest(
            {
                "state": "pending_graph_dependencies",
                "stage_id": stage_id,
                "candidate_revision": candidate_revision,
                "project_binding_digest": project_binding_digest,
                "dependencies": dependencies,
            }
        )

    def _superseded(
        self,
        project_id: str,
        candidate: Candidate,
        identity: PredictionGraphProjectIdentity,
        revision: PredictionGraphRevision,
        request_id: str,
        previous: PredictionGraphExecution | None,
    ) -> PredictionGraphExecution:
        if previous is not None:
            stages = previous.stages
            terminal_outputs = previous.terminal_outputs
        else:
            stages = tuple(
                PredictionGraphStageExecution(
                    stage_id=stage.stage_id,
                    output_definitions=self.stage_executor._output_definitions(stage),
                    status="stale",
                    requested_input_digest=semantic_digest(
                        {"superseded_stage": stage.stage_id}
                    ),
                    contract_digest=stage.contract_digest,
                    package_manifest_digest=stage.package_manifest_digest,
                    canonical_input={},
                )
                for stage in revision.stages
            )
            terminal_outputs = tuple(
                PredictionGraphTerminalOutput(
                    output_id=f"unresolved.{stage.stage_id}",
                    source_stage_id=stage.stage_id,
                    source_output_key="unresolved",
                    role="diagnostic",
                    required_for_complete_result=False,
                    status="stale",
                )
                for stage in revision.stages
            )
        now = _now()
        return PredictionGraphExecution(
            request_id=request_id,
            project_id=project_id,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
            graph_revision_id=identity.graph_revision_id,
            graph_revision_digest=identity.graph_revision_digest,
            project_binding_revision=identity.project_binding.revision,
            project_binding_digest=identity.project_binding.digest,
            status="superseded",
            stages=stages,
            terminal_outputs=terminal_outputs,
            failed_stage_ids=tuple(
                stage.stage_id for stage in stages if stage.status == "failed"
            ),
            blocked_stage_ids=tuple(
                stage.stage_id
                for stage in stages
                if stage.status == "blocked_by_upstream"
            ),
            created_at=now,
            updated_at=now,
        )
