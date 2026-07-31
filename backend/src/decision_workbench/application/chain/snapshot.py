"""Create immutable Chain snapshots and actual-conditioned variants."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
import uuid

from decision_workbench.application.chain_candidate_adapters import ChainCandidateAdapterError
from decision_workbench.application.chain.plan import (
    ChainExecutionError,
    ChainPlanningUseCase,
)
from decision_workbench.application.chain.stage_execution import ChainStageExecutor
from decision_workbench.contracts.chain_contracts import ChainSnapshotIdentityV2
from decision_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ActualConditionedVariantIdentity,
    ChainSnapshot,
    IntermediateActualRecord,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import CandidateRevisionConflictError, Store


def _now() -> datetime:
    return datetime.now(UTC)


class ChainSnapshotUseCase:
    def __init__(
        self,
        planning: ChainPlanningUseCase,
        stage_executor: ChainStageExecutor,
    ) -> None:
        self.planning = planning
        self.stage_executor = stage_executor
        self.store: Store = planning.store

    def snapshot(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
    ) -> ChainSnapshot:
        candidate, _definition, revision, identity = self.planning.resolve(
            project_id, candidate_id, candidate_revision
        )
        execution = self.store.get_chain_execution(project_id, candidate_id)
        if (
            execution is None
            or execution.status != "latest"
            or execution.candidate_revision != candidate_revision
        ):
            raise ChainExecutionError(
                "全Stageが最新のChain結果を先に実行してください"
            )
        adapter = self.planning.adapter_for(revision)
        try:
            domain_references = adapter.snapshot_domain_references(candidate)
        except ChainCandidateAdapterError as exc:
            raise ChainExecutionError(str(exc)) from exc
        stage_revisions = {stage.stage_id: stage for stage in revision.stages}
        snapshot_stages = tuple(
            stage
            if stage.output_definitions
            else stage.model_copy(
                update={
                    "output_definitions": self.stage_executor._output_definitions(
                        stage_revisions[stage.stage_id]
                    )
                }
            )
            for stage in execution.stages
        )
        snapshot = ChainSnapshot(
            snapshot_id=str(uuid.uuid4()),
            identity=ChainSnapshotIdentityV2(
                chain_revision_id=identity.chain_revision_id,
                chain_revision_digest=identity.chain_revision_digest,
                candidate_id=candidate.id,
                candidate_revision=candidate.revision,
                candidate_adapter_id=adapter.adapter_id,
                domain_references=domain_references,
            ),
            request_id=execution.request_id,
            external_input=adapter.external_values(candidate),
            stages=snapshot_stages,
            created_at=_now(),
        )
        try:
            return self.store.insert_chain_snapshot(project_id, snapshot)
        except CandidateRevisionConflictError as exc:
            raise ChainExecutionError(
                "候補は更新済みのため、過去revisionからChain snapshotを作成できません"
            ) from exc

    def actual_conditioned_variant(
        self,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        comparison_snapshot_id: str,
        actual_records: tuple[IntermediateActualRecord, ...],
    ) -> ActualConditionedVariant:
        """Run Stage C with complete measured B outputs without mutating normal Chain state."""

        candidate, definition, revision, identity = self.planning.resolve(
            project_id, candidate_id, candidate_revision
        )
        snapshot = self.store.get_chain_snapshot(comparison_snapshot_id)
        if snapshot is None:
            raise ChainExecutionError("比較元Chain snapshotが見つかりません")
        if (
            snapshot.identity.chain_revision_id != identity.chain_revision_id
            or snapshot.identity.chain_revision_digest != identity.chain_revision_digest
            or snapshot.identity.candidate_id != candidate_id
            or snapshot.identity.candidate_revision != candidate_revision
        ):
            raise ChainExecutionError(
                "比較元snapshotはこのChain candidate revisionの結果ではありません"
            )
        if not actual_records:
            raise ChainExecutionError("Stage B実測を1件以上指定してください")
        actual_ids = [record.actual_id for record in actual_records]
        if len(actual_ids) != len(set(actual_ids)):
            raise ChainExecutionError("実測IDは重複できません")

        stage_c = revision.stages[-1]
        if stage_c.stage_kind != "task":
            raise ChainExecutionError("actual-conditioned variantの終端はTask Stageが必要です")
        stage_c_snapshot = next(
            (stage for stage in snapshot.stages if stage.stage_id == stage_c.stage_id),
            None,
        )

        if (
            stage_c_snapshot is None
            or stage_c_snapshot.package_manifest_digest
            != stage_c.package_manifest_digest
        ):
            raise ChainExecutionError("比較元snapshotのStage C Packageが一致しません")

        adapter = self.planning.adapter_for(revision)
        try:
            conditioning = adapter.apply_actual_measurements(
                definition,
                stage_c,
                stage_c_snapshot.canonical_input,
                actual_records,
            )
        except ChainCandidateAdapterError as exc:
            raise ChainExecutionError(str(exc)) from exc
        canonical_input = conditioning.canonical_input
        payload, _outputs = self.stage_executor._run_stage(
            stage_c, canonical_input, candidate, adapter
        )
        measurement_payload = [
            {
                "actual_id": record.actual_id,
                "values": {
                    key: record.values[key] for key in sorted(record.values)
                },
            }
            for record in sorted(actual_records, key=lambda item: item.actual_id)
        ]
        variant = ActualConditionedVariant(
            variant_id=str(uuid.uuid4()),
            project_id=project_id,
            identity=ActualConditionedVariantIdentity(
                base_chain_revision_id=identity.chain_revision_id,
                base_chain_revision_digest=identity.chain_revision_digest,
                base_candidate_id=candidate_id,
                base_candidate_revision=candidate_revision,
                comparison_snapshot_id=comparison_snapshot_id,
                actual_ids=tuple(sorted(actual_ids)),
                measurement_digest=semantic_digest(measurement_payload),
                coverage=conditioning.coverage,
                stage_c_package_manifest_digest=stage_c.package_manifest_digest,
            ),
            measured_stage_b=conditioning.measured_values,
            stage_c_input=canonical_input,
            stage_c_result=payload,
            created_at=_now(),
        )
        return self.store.insert_chain_analysis_variant(variant)
