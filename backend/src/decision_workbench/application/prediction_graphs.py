"""Application facade for the dedicated Prediction Graph runtime."""
from __future__ import annotations

import uuid

from decision_workbench.application.chain.graph_execution import (
    PredictionGraphExecutionUseCase,
)
from decision_workbench.application.chain.graph_plan import (
    PredictionGraphPlanningUseCase,
)
from decision_workbench.application.chain.graph_snapshot import (
    PredictionGraphSnapshotUseCase,
)
from decision_workbench.application.chain.plan import ChainExecutionError
from decision_workbench.application.chains import (
    ChainCandidateRevisionError,
    ChainConflictError,
    ChainNotFoundError,
    ChainValidationError,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    CandidateUpdate,
    Project,
    ProjectCreateInput,
)
from decision_workbench.contracts.chain_api_contracts import (
    ChainExecutionRequest,
    PredictionGraphProjectCreateRequest,
)
from decision_workbench.contracts.chain_contracts import (
    PredictionGraphProjectBinding,
    PredictionGraphProjectIdentity,
)
from decision_workbench.contracts.chain_execution_contracts import (
    PredictionGraphExecution,
    PredictionGraphSnapshot,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import (
    CandidateRevisionConflictError,
    ChainCatalogConflictError,
    Store,
)


class PredictionGraphUseCases:
    """Real application entry points; legacy Chain v1 remains a separate facade."""

    def __init__(
        self,
        *,
        store: Store,
        planning: PredictionGraphPlanningUseCase,
        execution: PredictionGraphExecutionUseCase,
        snapshots: PredictionGraphSnapshotUseCase,
    ) -> None:
        self.store = store
        self.planning = planning
        self.execution = execution
        self.snapshots = snapshots

    def create_project(
        self,
        payload: PredictionGraphProjectCreateRequest,
    ) -> Project:
        if (
            payload.project.scientific_identity is not None
            or payload.project.initial_candidate is not None
        ):
            raise ChainValidationError(
                "Prediction Graph Projectのidentityと候補は専用項目/APIで指定してください"
            )
        binding_payload = {
            "schema_version": "prediction-graph-project-binding/v1",
            "revision": payload.project_binding_revision,
            "values": payload.project_binding_values,
        }
        identity = PredictionGraphProjectIdentity(
            identity_kind="prediction_graph",
            graph_revision_id=payload.graph_revision_id,
            graph_revision_digest=payload.graph_revision_digest,
            project_binding=PredictionGraphProjectBinding(
                **binding_payload,
                digest=semantic_digest(binding_payload),
            ),
        )
        project_payload = ProjectCreateInput.model_validate(
            {
                **payload.project.model_dump(
                    exclude={"scientific_identity", "initial_candidate"}
                ),
                "task_id": "",
                "scientific_identity": identity,
            }
        )
        try:
            return self.store.create_prediction_graph_project(
                project_payload,
                identity,
            )
        except ChainCatalogConflictError as exc:
            raise ChainConflictError(str(exc)) from exc

    def create_candidate(
        self,
        project_id: str,
        payload: CandidateInput,
    ) -> Candidate:
        try:
            prepared = self.planning.prepare_candidate(project_id, payload)
        except ChainExecutionError as exc:
            raise ChainValidationError(str(exc)) from exc
        return self.store.create_candidate(prepared, project_id)

    def update_candidate(
        self,
        project_id: str,
        candidate_id: str,
        payload: CandidateUpdate,
    ) -> Candidate:
        try:
            prepared = self.planning.prepare_candidate(
                project_id,
                CandidateInput.model_validate(
                    payload.model_dump(exclude={"expected_revision"})
                ),
            )
            request_id = f"candidate-revision:{uuid.uuid4()}"
            updated, generation = self.store.update_chain_candidate(
                candidate_id,
                project_id,
                prepared,
                payload.expected_revision,
                request_id,
            )
        except ChainExecutionError as exc:
            raise ChainValidationError(str(exc)) from exc
        except CandidateRevisionConflictError as exc:
            raise ChainCandidateRevisionError(str(exc), exc.current) from exc
        if updated is None:
            raise ChainNotFoundError("Prediction Graph candidateが見つかりません")
        self.execution.coordinator.begin(
            self.store.chain_execution_scope(project_id, candidate_id),
            request_id,
        )
        self.execution.mark_candidate_changed(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=updated.revision,
            request_id=request_id,
            generation=generation,
        )
        return updated

    def execute(
        self,
        project_id: str,
        candidate_id: str,
        payload: ChainExecutionRequest,
    ) -> PredictionGraphExecution:
        try:
            return self.execution.execute(
                project_id=project_id,
                candidate_id=candidate_id,
                candidate_revision=payload.candidate_revision,
                request_id=payload.request_id,
                debounce_ms=payload.debounce_ms,
            )
        except ChainExecutionError as exc:
            raise ChainConflictError(str(exc)) from exc

    def latest_execution(
        self,
        project_id: str,
        candidate_id: str,
    ) -> PredictionGraphExecution:
        result = self.store.get_prediction_graph_execution(
            project_id,
            candidate_id,
        )
        if result is None:
            raise ChainNotFoundError("Prediction Graph executionが見つかりません")
        return result

    def create_snapshot(
        self,
        project_id: str,
        candidate_id: str,
        payload: ChainExecutionRequest,
    ) -> PredictionGraphSnapshot:
        try:
            return self.snapshots.snapshot(
                project_id=project_id,
                candidate_id=candidate_id,
                candidate_revision=payload.candidate_revision,
            )
        except ChainExecutionError as exc:
            raise ChainConflictError(str(exc)) from exc

    def list_snapshots(
        self,
        project_id: str,
        candidate_id: str,
    ) -> list[PredictionGraphSnapshot]:
        return self.store.list_prediction_graph_snapshots(
            project_id,
            candidate_id,
        )

    def snapshot(
        self,
        project_id: str,
        snapshot_id: str,
    ) -> PredictionGraphSnapshot:
        result = self.store.get_prediction_graph_snapshot(
            snapshot_id,
            project_id=project_id,
        )
        if result is None:
            raise ChainNotFoundError("Prediction Graph snapshotが見つかりません")
        return result
