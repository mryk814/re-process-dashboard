"""Coordinate Chain execution, stale rejection, and partial recomputation."""
from __future__ import annotations

import threading
import time
from typing import Any
import uuid
from datetime import UTC, datetime

from decision_workbench.application.chain_execution_plan import (
    ChainExecutionError,
    ChainPlanningUseCase,
)
from decision_workbench.application.chain_stage_execution import ChainStageExecutor
from decision_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainProjectIdentity,
    ChainRevision,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ChainExecution,
    ChainStageExecution,
)
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import Store


def _now() -> datetime:
    return datetime.now(UTC)


class ChainExecutionCoordinator:
    """Tracks the newest request per candidate; superseded work is never committed."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, str] = {}

    def begin(self, scope_id: str, request_id: str) -> None:
        with self._lock:
            self._latest[scope_id] = request_id

    def is_latest(self, scope_id: str, request_id: str) -> bool:
        with self._lock:
            return self._latest.get(scope_id) == request_id


class ChainExecutionUseCase:
    def __init__(
        self,
        planning: ChainPlanningUseCase,
        stage_executor: ChainStageExecutor,
        coordinator: ChainExecutionCoordinator,
    ) -> None:
        self.planning = planning
        self.stage_executor = stage_executor
        self.coordinator = coordinator
        self.store: Store = planning.store
        self.registry = planning.registry

    def execute(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        request_id: str | None = None,
        debounce_ms: int = 250,
    ) -> ChainExecution:
        candidate, definition, revision, identity = self.planning.resolve(
            project_id, candidate_id, candidate_revision
        )
        if candidate.blend_validation.status == "invalid":
            reasons = " / ".join(
                issue.message for issue in candidate.blend_validation.issues
            )
            raise ChainExecutionError(
                f"配合がDesign Spaceを満たしていないためChainを実行できません: {reasons}"
            )
        request_id = request_id or str(uuid.uuid4())
        scope_id = self.store.chain_execution_scope(project_id, candidate_id)
        generation = self.store.claim_chain_execution(
            project_id, candidate_id, candidate.revision, request_id
        )
        if generation is None:
            return self._superseded(
                project_id,
                candidate,
                identity,
                revision,
                request_id,
                self.store.get_chain_execution(project_id, candidate_id),
            )
        self.coordinator.begin(scope_id, request_id)
        if debounce_ms:
            time.sleep(debounce_ms / 1000)
        previous_execution = self.store.get_chain_execution(project_id, candidate_id)
        previous_by_stage = {
            item.stage_id: item for item in previous_execution.stages
        } if previous_execution is not None else {}
        created_at = _now()
        if (
            self.store.chain_execution_generation(
                project_id, candidate_id, request_id
            )
            != generation
        ):
            return self._superseded(
                project_id, candidate, identity, revision, request_id, previous_execution
            )

        adapter = self.planning.adapter_for(revision)
        external = adapter.external_values(candidate)
        upstream_outputs: dict[str, dict[str, Any]] = {}
        stages: list[ChainStageExecution] = []
        for stage in revision.stages:
            try:
                canonical_input = self.stage_executor._canonical_input(
                    definition, stage.stage_id, external, upstream_outputs
                )
            except ChainExecutionError as exc:
                canonical_input = {}
                input_digest = semantic_digest(
                    {
                        "stage_id": stage.stage_id,
                        "binding_error": str(exc),
                        "available_upstream": sorted(upstream_outputs),
                    }
                )
                stages.append(
                    self.stage_executor._retained(
                        previous_by_stage.get(stage.stage_id),
                        stage=stage,
                        status="failed",
                        requested_input_digest=input_digest,
                        canonical_input=canonical_input,
                        error=str(exc),
                        started_at=_now(),
                    )
                )
                execution = ChainExecution(
                    request_id=request_id,
                    project_id=project_id,
                    candidate_id=candidate.id,
                    candidate_revision=candidate.revision,
                    chain_revision_id=identity.chain_revision_id,
                    chain_revision_digest=identity.chain_revision_digest,
                    status="failed",
                    stages=tuple(stages + [
                        self.stage_executor._retained(
                            previous_by_stage.get(pending.stage_id),
                            stage=pending,
                            status="stale",
                            requested_input_digest=(
                                self.stage_executor._pending_digest(
                                    pending.stage_id,
                                    candidate.revision,
                                    stage.stage_id,
                                )
                            ),

                            canonical_input=(
                                previous_by_stage[pending.stage_id].canonical_input
                                if pending.stage_id in previous_by_stage
                                else {}
                            ),
                        )
                        for pending in revision.stages[len(stages):]
                    ]),
                    created_at=created_at,
                    updated_at=_now(),
                )
                if self.store.save_chain_execution_if_current(execution, generation):
                    return execution
                return self._superseded(
                    project_id,
                    candidate,
                    identity,
                    revision,
                    request_id,
                    self.store.get_chain_execution(project_id, candidate_id),
                )
            input_digest = semantic_digest(canonical_input)
            previous = previous_by_stage.get(stage.stage_id)
            running = self.stage_executor._retained(
                previous,
                stage=stage,
                status="running",
                requested_input_digest=input_digest,
                canonical_input=canonical_input,
                started_at=_now(),
            )
            stages.append(running)
            running_execution = ChainExecution(
                request_id=request_id,
                project_id=project_id,
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
                chain_revision_id=identity.chain_revision_id,
                chain_revision_digest=identity.chain_revision_digest,
                status="running",
                stages=tuple(
                    stages
                    + [
                        self.stage_executor._retained(
                            previous_by_stage.get(pending.stage_id),
                            stage=pending,
                            status="stale",
                            requested_input_digest=(
                                self.stage_executor._pending_digest(
                                    pending.stage_id,
                                    candidate.revision,
                                    stage.stage_id,
                                )
                            ),
                            canonical_input=(
                                previous_by_stage[pending.stage_id].canonical_input
                                if pending.stage_id in previous_by_stage
                                else {}
                            ),
                        )
                        for pending in revision.stages[len(stages):]
                    ]
                ),
                created_at=created_at,
                updated_at=_now(),
            )
            if not self.store.save_chain_execution_if_current(
                running_execution, generation
            ):
                return self._superseded(
                    project_id,
                    candidate,
                    identity,
                    revision,
                    request_id,
                    self.store.get_chain_execution(project_id, candidate_id),
                )
            memo_key = self.stage_executor._memo_key(stage, input_digest)
            memo = self.store.get_chain_stage_memo(memo_key)
            try:
                self.stage_executor._assert_runtime_identity(stage, candidate, adapter)
                if memo is None:
                    payload, outputs = self.stage_executor._run_stage(
                        stage, canonical_input, candidate, adapter
                    )
                    memo_result = {"payload": payload, "outputs": outputs}
                    self.store.put_chain_stage_memo(
                        memo_key=memo_key,
                        stage_id=stage.stage_id,
                        input_digest=input_digest,
                        contract_digest=stage.contract_digest,
                        package_manifest_digest=stage.package_manifest_digest,
                        canonical_input=canonical_input,
                        result=memo_result,
                    )
                    cache_hit = False
                else:
                    memo_result = memo["result"]
                    payload = memo_result["payload"]
                    outputs = memo_result["outputs"]

                    cache_hit = True
            except Exception as exc:
                failed = self.stage_executor._retained(
                    previous,
                    stage=stage,
                    status="failed",
                    requested_input_digest=input_digest,
                    canonical_input=canonical_input,
                    error=str(exc),
                    started_at=running.started_at,
                )
                stages[-1] = failed
                execution = ChainExecution(
                    request_id=request_id,
                    project_id=project_id,
                    candidate_id=candidate.id,
                    candidate_revision=candidate.revision,
                    chain_revision_id=identity.chain_revision_id,
                    chain_revision_digest=identity.chain_revision_digest,
                    status="failed",
                    stages=tuple(stages + [
                        self.stage_executor._retained(
                            previous_by_stage.get(pending.stage_id),
                            stage=pending,
                            status="stale",
                            requested_input_digest=(
                                self.stage_executor._pending_digest(
                                    pending.stage_id,
                                    candidate.revision,
                                    stage.stage_id,
                                )
                            ),
                            canonical_input=(
                                previous_by_stage[pending.stage_id].canonical_input
                                if pending.stage_id in previous_by_stage
                                else {}
                            ),
                        )
                        for pending in revision.stages[len(stages):]
                    ]),
                    created_at=created_at,
                    updated_at=_now(),
                )
                if self.store.save_chain_execution_if_current(execution, generation):
                    return execution
                return self._superseded(
                    project_id,
                    candidate,
                    identity,
                    revision,
                    request_id,
                    self.store.get_chain_execution(project_id, candidate_id),
                )

            if (
                self.store.chain_execution_generation(
                    project_id, candidate_id, request_id
                )
                != generation
            ):
                return self._superseded(
                    project_id, candidate, identity, revision, request_id,
                    self.store.get_chain_execution(project_id, candidate_id),
                )
            stages[-1] = ChainStageExecution(
                stage_id=stage.stage_id,
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
            upstream_outputs[stage.stage_id] = dict(outputs)

        execution = ChainExecution(
            request_id=request_id,
            project_id=project_id,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
            chain_revision_id=identity.chain_revision_id,
            chain_revision_digest=identity.chain_revision_digest,
            status="latest",
            stages=tuple(stages),
            created_at=created_at,
            updated_at=_now(),
        )
        if self.store.save_chain_execution_if_current(execution, generation):
            return execution
        return self._superseded(
            project_id,
            candidate,
            identity,
            revision,
            request_id,

            self.store.get_chain_execution(project_id, candidate_id),
        )

    def mark_candidate_changed(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        request_id: str,
        generation: int,
    ) -> ChainExecution | None:
        """Reclassify retained results immediately after a candidate revision changes."""

        previous_execution = self.store.get_chain_execution(project_id, candidate_id)
        if previous_execution is None:
            return None
        candidate, definition, revision, identity = self.planning.resolve(
            project_id, candidate_id, candidate_revision
        )
        adapter = self.planning.adapter_for(revision)
        external = adapter.external_values(candidate)
        previous_by_stage = {
            item.stage_id: item for item in previous_execution.stages
        }
        upstream_outputs: dict[str, dict[str, Any]] = {}
        stages: list[ChainStageExecution] = []
        upstream_stale = False
        for stage in revision.stages:
            previous = previous_by_stage.get(stage.stage_id)
            if upstream_stale:
                if previous is None:
                    canonical_input: dict[str, Any] = {}
                    requested = self.stage_executor._pending_digest(
                        stage.stage_id,
                        candidate_revision,
                        stages[-1].stage_id if stages else "candidate",
                    )
                else:
                    canonical_input = previous.canonical_input
                    requested = self.stage_executor._pending_digest(
                        stage.stage_id,
                        candidate_revision,
                        stages[-1].stage_id if stages else "candidate",
                    )
                stages.append(
                    self.stage_executor._retained(
                        previous,
                        stage=stage,
                        status="stale",
                        requested_input_digest=requested,
                        canonical_input=canonical_input,
                    )
                )
                continue
            canonical_input = self.stage_executor._canonical_input(
                definition, stage.stage_id, external, upstream_outputs
            )
            input_digest = semantic_digest(canonical_input)
            if (
                previous is not None
                and previous.result is not None
                and previous.result_input_digest == input_digest
            ):
                stages.append(
                    ChainStageExecution(
                        stage_id=stage.stage_id,
                        output_definitions=self.stage_executor._output_definitions(stage),
                        status="latest",
                        requested_input_digest=input_digest,
                        result_input_digest=input_digest,
                        contract_digest=stage.contract_digest,
                        package_manifest_digest=stage.package_manifest_digest,
                        canonical_input=canonical_input,
                        result=previous.result,
                        cache_hit=False,
                        started_at=previous.started_at,
                        completed_at=previous.completed_at,
                    )
                )
                upstream_outputs[stage.stage_id] = self.stage_executor._outputs_from_payload(
                    stage, previous.result, adapter
                )
            else:
                stages.append(
                    self.stage_executor._retained(
                        previous,
                        stage=stage,
                        status="stale",
                        requested_input_digest=input_digest,
                        canonical_input=canonical_input,
                    )
                )
                upstream_stale = True
        now = _now()
        execution = ChainExecution(
            request_id=request_id,
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=candidate_revision,

            chain_revision_id=identity.chain_revision_id,
            chain_revision_digest=identity.chain_revision_digest,
            status=(
                "latest"
                if all(stage.status == "latest" for stage in stages)
                else "stale"
            ),
            stages=tuple(stages),
            created_at=previous_execution.created_at,
            updated_at=now,
        )
        return (
            execution
            if self.store.save_chain_execution_if_current(execution, generation)
            else self.store.get_chain_execution(project_id, candidate_id)
        )

    def _superseded(
        self,
        project_id: str,
        candidate: Candidate,
        identity: ChainProjectIdentity,
        revision: ChainRevision,
        request_id: str,
        previous: ChainExecution | None,
    ) -> ChainExecution:
        if previous is None:
            stages = tuple(
                ChainStageExecution(
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
        else:
            stages = previous.stages
        now = _now()
        return ChainExecution(
            request_id=request_id,
            project_id=project_id,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
            chain_revision_id=identity.chain_revision_id,
            chain_revision_digest=identity.chain_revision_digest,
            status="superseded",
            stages=stages,
            created_at=now,
            updated_at=now,
        )
