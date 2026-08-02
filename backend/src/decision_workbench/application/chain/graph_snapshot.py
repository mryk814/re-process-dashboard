"""Create immutable Prediction Graph snapshots from complete required outputs."""
from __future__ import annotations

from datetime import UTC, datetime
import uuid

from decision_workbench.application.chain.graph_plan import (
    PredictionGraphPlanningUseCase,
)
from decision_workbench.application.chain.plan import ChainExecutionError
from decision_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapterError,
)
from decision_workbench.contracts.chain_execution_contracts import (
    PredictionGraphSnapshot,
    PredictionGraphSnapshotIdentity,
)
from decision_workbench.persistence.store import (
    CandidateRevisionConflictError,
    Store,
)


def _now() -> datetime:
    return datetime.now(UTC)


class PredictionGraphSnapshotUseCase:
    def __init__(self, planning: PredictionGraphPlanningUseCase) -> None:
        self.planning = planning
        self.store: Store = planning.store

    def snapshot(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
    ) -> PredictionGraphSnapshot:
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
        execution = self.store.get_prediction_graph_execution(
            project_id,
            candidate_id,
        )
        if (
            execution is None
            or execution.status != "complete"
            or execution.candidate_revision != candidate_revision
            or execution.graph_revision_id != identity.graph_revision_id
            or execution.graph_revision_digest != identity.graph_revision_digest
            or execution.project_binding_revision
            != identity.project_binding.revision
            or execution.project_binding_digest != identity.project_binding.digest
        ):
            raise ChainExecutionError(
                "required terminal outputsが最新のGraph結果を先に実行してください"
            )
        required_output_ids = tuple(
            output.output_id
            for output in definition.decision_outputs
            if output.required_for_complete_result
        )
        terminal_by_id = {
            output.output_id: output for output in execution.terminal_outputs
        }
        incomplete = [
            output_id
            for output_id in required_output_ids
            if output_id not in terminal_by_id
            or terminal_by_id[output_id].status != "latest"
        ]
        if incomplete:
            raise ChainExecutionError(
                "required terminal outputsが不足しています: "
                + ", ".join(incomplete)
            )
        try:
            domain_references = adapter.snapshot_domain_references(candidate)
        except ChainCandidateAdapterError as exc:
            raise ChainExecutionError(str(exc)) from exc
        snapshot = PredictionGraphSnapshot(
            snapshot_id=str(uuid.uuid4()),
            identity=PredictionGraphSnapshotIdentity(
                graph_revision_id=identity.graph_revision_id,
                graph_revision_digest=revision.revision_digest,
                project_binding_revision=identity.project_binding.revision,
                project_binding_digest=identity.project_binding.digest,
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
                candidate_adapter_id=adapter.adapter_id,
                domain_references=domain_references,
            ),
            request_id=execution.request_id,
            external_input=external,
            stages=execution.stages,
            terminal_outputs=execution.terminal_outputs,
            required_output_ids=required_output_ids,
            created_at=_now(),
        )
        try:
            return self.store.insert_prediction_graph_snapshot(
                project_id,
                snapshot,
            )
        except CandidateRevisionConflictError as exc:
            raise ChainExecutionError(
                "候補は更新済みのため、過去revisionからGraph snapshotを作成できません"
            ) from exc
