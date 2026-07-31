from __future__ import annotations

import uuid

from decision_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapterError,
)
from decision_workbench.application.chain_execution_plan import (
    ChainExecutionError,
    ChainPlanningUseCase,
)
from decision_workbench.application.chain_execution_use_case import (
    ChainExecutionUseCase,
)
from decision_workbench.application.chain_snapshot_use_case import (
    ChainSnapshotUseCase,
)
from decision_workbench.application.chain_uncertainty import ChainUncertaintyService
from decision_workbench.application.chain_evaluation import (
    ChainEvaluationCatalog,
    ChainEvaluationError,
)
from decision_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainRevision,
)
from decision_workbench.contracts.chain_api_contracts import (
    ActualConditionedVariantRequest,
    ChainCandidateContractResponse,
    ChainDistributionRequest,
    ChainExecutionRequest,
    ChainTemplateItem,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ChainCandidateCapability,
    ChainCandidateInputDefinition,
    ChainExecution,
    ChainSnapshot,
    IntermediateActualRecord,
)
from decision_workbench.contracts.chain_uncertainty_contracts import (
    ChainDistributionCapability,
    ChainDistributionRun,
)
from decision_workbench.contracts.chain_evaluation_contracts import (
    ResolvedChainEvaluation,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    CandidateUpdate,
)
from decision_workbench.persistence.store import (
    CandidateRevisionConflictError,
    Store,
)
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog
from decision_workbench.contracts.subsystem_availability import (
    WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
    WELDING_CHAIN_SUBSYSTEM_ID,
    SubsystemAvailabilityRegistry,
)


class ChainUseCaseError(ValueError):
    """Base Chain application error translated only at the transport boundary."""


class ChainNotFoundError(ChainUseCaseError):
    pass


class ChainConflictError(ChainUseCaseError):
    pass


class ChainValidationError(ChainUseCaseError):
    pass


class ChainCandidateRevisionError(ChainConflictError):
    def __init__(self, message: str, current: Candidate) -> None:
        super().__init__(message)
        self.current = current


def _definition_id(definition: ChainDefinition) -> str:
    return f"{definition.chain_id}@{definition.digest.removeprefix('sha256:')[:12]}"


def list_chain_templates(store: Store) -> list[ChainTemplateItem]:
    revisions = store.list_chain_revisions()
    return [
        ChainTemplateItem(
            definition_id=_definition_id(definition),
            definition=definition,
            revisions=tuple(
                revision
                for revision in revisions
                if revision.chain_id == definition.chain_id
                and revision.chain_definition_digest == definition.digest
            ),
        )
        for definition in store.list_chain_definitions()
    ]


def get_chain_revision(
    revision_id: str,
    store: Store,
) -> ChainRevision:
    revision = store.get_chain_revision(revision_id)
    if revision is None:
        raise ChainNotFoundError("Chain Revisionが見つかりません")
    return revision


def _require_stored_chain_project(store: Store, project_id: str) -> None:
    project = store.get_project(project_id)
    if project is None:
        raise ChainNotFoundError("Chain Projectが見つかりません")
    if project.scientific_identity.identity_kind != "chain":
        raise ChainConflictError("このAPIはChain Project専用です")


def get_project_chain_evaluation(
    project_id: str,
    catalog: ChainEvaluationCatalog,
    store: Store,
    workspace_catalog: WorkspaceCatalog,
) -> ResolvedChainEvaluation:
    project = store.get_project(project_id)
    if project is None:
        raise ChainNotFoundError("Chain Projectが見つかりません")
    identity = project.scientific_identity
    if identity.identity_kind != "chain":
        raise ChainConflictError("このAPIはChain Project専用です")
    revision = store.get_chain_revision(identity.chain_revision_id)
    if (
        revision is None
        or revision.revision_digest != identity.chain_revision_digest
    ):
        raise ChainConflictError("固定されたChain Revisionを解決できません")
    stage_source_digests: dict[str, set[str]] = {}
    for stage in revision.stages:
        if stage.dataset_view_revision_id is None:
            continue
        view = workspace_catalog.get_dataset_view_revision(
            stage.dataset_view_revision_id, include_archived=True
        )
        if view is None:
            raise ChainConflictError(
                f"Stage {stage.stage_id}のDataset Viewを解決できません"
            )
        digests: set[str] = set()
        for member in view.members:
            dataset = workspace_catalog.get_dataset_revision(
                member.dataset_revision_id, include_archived=True
            )
            asset = (
                workspace_catalog.get_data_asset(
                    dataset.data_asset_id, include_archived=True
                )
                if dataset is not None
                else None
            )
            if asset is None:
                raise ChainConflictError(
                    f"Stage {stage.stage_id}のsource identityを解決できません"
                )
            digests.add(f"sha256:{asset.sha256}")
        stage_source_digests[stage.stage_id] = digests
    try:
        return catalog.resolve(
            revision_id=identity.chain_revision_id,
            revision=revision,
            stage_source_digests=stage_source_digests,
        )
    except ChainEvaluationError as exc:
        raise ChainConflictError(str(exc)) from exc


def get_chain_candidate_inputs(
    project_id: str,
    planning: ChainPlanningUseCase,
) -> tuple[ChainCandidateInputDefinition, ...]:
    """Read-only input surface derived from the exact pinned Chain revision."""

    try:
        return planning.candidate_input_definitions(
            project_id,
            require_runtime_identity=False,
        )
    except ChainExecutionError as exc:
        raise ChainConflictError(str(exc)) from exc


def get_chain_candidate_capability(
    project_id: str,
    planning: ChainPlanningUseCase,
) -> ChainCandidateCapability:
    """Which candidate surface this Chain needs, before any editor is rendered."""

    try:
        return planning.candidate_capability(project_id)
    except ChainExecutionError as exc:
        raise ChainConflictError(str(exc)) from exc


def get_chain_candidate_contract(
    project_id: str,
    planning: ChainPlanningUseCase,
) -> ChainCandidateContractResponse:
    try:
        adapter = planning.sparse_blend_adapter(project_id)
        contracts = adapter.resolved_contracts()
        external_inputs = planning.candidate_input_definitions(project_id)
        starter = planning.starter_candidate(project_id)
    except (ChainExecutionError, ChainCandidateAdapterError) as exc:
        raise ChainConflictError(str(exc)) from exc
    return ChainCandidateContractResponse(
        transform_id=adapter.transform_id,
        scientific_master=contracts.design_space.scientific_master,
        commercial_catalog=contracts.commercial_catalog.ref,
        design_space=contracts.design_space,
        design_space_ref=contracts.design_space.ref,
        external_inputs=external_inputs,
        starter_candidate=starter,
    )


def list_chain_candidates(
    project_id: str,
    store: Store,
) -> list[Candidate]:
    _require_stored_chain_project(store, project_id)
    return store.list_candidates(project_id)


def create_chain_candidate(
    project_id: str,
    payload: CandidateInput,
    planning: ChainPlanningUseCase,
    store: Store,
) -> Candidate:
    project = store.get_project(project_id)
    if project is None:
        raise ChainNotFoundError("Chain Projectが見つかりません")
    if project.scientific_identity.identity_kind != "chain":
        raise ChainConflictError("このAPIはChain Project専用です")
    try:
        prepared = planning.prepare_candidate(project_id, payload)
    except ChainExecutionError as exc:
        raise ChainValidationError(str(exc)) from exc
    return store.create_candidate(prepared, project_id)


def update_chain_candidate(
    project_id: str,
    candidate_id: str,
    payload: CandidateUpdate,
    planning: ChainPlanningUseCase,
    execution: ChainExecutionUseCase,
    store: Store,
) -> Candidate:
    try:
        prepared = planning.prepare_candidate(
            project_id,
            CandidateInput.model_validate(
                payload.model_dump(exclude={"expected_revision"})
            ),
        )
        invalidation_request_id = f"candidate-revision:{uuid.uuid4()}"
        updated, generation = store.update_chain_candidate(
            candidate_id,
            project_id,
            prepared,
            payload.expected_revision,
            invalidation_request_id,
        )
    except ChainExecutionError as exc:
        raise ChainValidationError(str(exc)) from exc
    except CandidateRevisionConflictError as exc:
        raise ChainCandidateRevisionError(str(exc), exc.current) from exc
    if updated is None:
        raise ChainNotFoundError("Chain候補が見つかりません")
    scope_id = store.chain_execution_scope(project_id, candidate_id)
    execution.coordinator.begin(scope_id, invalidation_request_id)
    execution.mark_candidate_changed(
        project_id=project_id,
        candidate_id=candidate_id,
        candidate_revision=updated.revision,
        request_id=invalidation_request_id,
        generation=generation,
    )
    return updated


def get_chain_candidate_revision(
    project_id: str,
    candidate_id: str,
    revision: int,
    store: Store,
) -> Candidate:
    _require_stored_chain_project(store, project_id)
    candidate = store.get_candidate_revision(candidate_id, revision, project_id)
    if candidate is None:
        raise ChainNotFoundError("Chain candidate revisionが見つかりません")
    return candidate


def execute_project_chain(
    project_id: str,
    candidate_id: str,
    payload: ChainExecutionRequest,
    execution: ChainExecutionUseCase,
) -> ChainExecution:
    try:
        return execution.execute(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=payload.candidate_revision,
            request_id=payload.request_id,
            debounce_ms=payload.debounce_ms,
        )
    except ChainExecutionError as exc:
        raise ChainConflictError(str(exc)) from exc


def list_project_chain_snapshots(
    project_id: str,
    candidate_id: str,
    store: Store,
) -> list[ChainSnapshot]:
    _require_stored_chain_project(store, project_id)
    return store.list_chain_snapshots(project_id, candidate_id)


def get_project_chain_execution(
    project_id: str,
    candidate_id: str,
    store: Store,
) -> ChainExecution:
    execution = store.get_chain_execution(project_id, candidate_id)
    if execution is None:
        raise ChainNotFoundError("Chain実行結果がありません")
    return execution


def get_project_chain_distribution_capability(
    project_id: str,
    service: ChainUncertaintyService,
) -> ChainDistributionCapability:
    try:
        return service.capability(project_id)
    except ChainExecutionError as exc:
        raise ChainConflictError(str(exc)) from exc


def run_project_chain_distribution(
    project_id: str,
    candidate_id: str,
    payload: ChainDistributionRequest,
    service: ChainUncertaintyService,
) -> ChainDistributionRun:
    try:
        return service.run(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=payload.candidate_revision,
            seed=payload.seed,
            sample_count=payload.sample_count,
        )
    except ChainExecutionError as exc:
        raise ChainConflictError(str(exc)) from exc


def get_latest_project_chain_distribution(
    project_id: str,
    candidate_id: str,
    store: Store,
) -> ChainDistributionRun:
    run = store.latest_chain_distribution_run(project_id, candidate_id)
    if run is None:
        raise ChainNotFoundError("Chain分布実行結果がありません")
    return run


def get_chain_distribution_run(
    run_id: str,
    store: Store,
) -> ChainDistributionRun:
    run = store.get_chain_distribution_run(run_id)
    if run is None:
        raise ChainNotFoundError("Chain分布実行結果が見つかりません")
    return run


def create_project_chain_snapshot(
    project_id: str,
    candidate_id: str,
    payload: ChainExecutionRequest,
    snapshots: ChainSnapshotUseCase,
) -> ChainSnapshot:
    try:
        return snapshots.snapshot(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=payload.candidate_revision,
        )
    except ChainExecutionError as exc:
        raise ChainConflictError(str(exc)) from exc


def get_chain_snapshot(
    project_id: str,
    snapshot_id: str,
    store: Store,
) -> ChainSnapshot:
    _require_stored_chain_project(store, project_id)
    snapshot = store.get_chain_snapshot(snapshot_id, project_id=project_id)
    if snapshot is None:
        raise ChainNotFoundError("Chain snapshotが見つかりません")
    return snapshot


def list_project_chain_analysis_variants(
    project_id: str,
    candidate_id: str,
    store: Store,
) -> list[ActualConditionedVariant]:
    _require_stored_chain_project(store, project_id)
    return store.list_chain_analysis_variants(project_id, candidate_id)


def create_project_chain_analysis_variant(
    project_id: str,
    candidate_id: str,
    payload: ActualConditionedVariantRequest,
    snapshots: ChainSnapshotUseCase,
) -> ActualConditionedVariant:
    try:
        return snapshots.actual_conditioned_variant(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=payload.candidate_revision,
            comparison_snapshot_id=payload.comparison_snapshot_id,
            actual_records=payload.actual_records,
        )
    except ChainExecutionError as exc:
        raise ChainConflictError(str(exc)) from exc


class ChainUseCases:
    def __init__(
        self,
        *,
        store: Store,
        workspace_catalog: WorkspaceCatalog,
        planning_use_case: ChainPlanningUseCase | None,
        execution_use_case: ChainExecutionUseCase | None,
        snapshot_use_case: ChainSnapshotUseCase | None,
        uncertainty_service: ChainUncertaintyService | None,
        evaluation_catalog: ChainEvaluationCatalog | None,
        subsystem_registry: SubsystemAvailabilityRegistry,
    ) -> None:
        self.store = store
        self.workspace_catalog = workspace_catalog
        self._planning_use_case = planning_use_case
        self._execution_use_case = execution_use_case
        self._snapshot_use_case = snapshot_use_case
        self._uncertainty_service = uncertainty_service
        self._evaluation_catalog = evaluation_catalog
        self.subsystem_registry = subsystem_registry

    def _planning(
        self, *, require_available: bool = True
    ) -> ChainPlanningUseCase:
        if require_available:
            self.subsystem_registry.require(WELDING_CHAIN_SUBSYSTEM_ID)
        assert self._planning_use_case is not None
        return self._planning_use_case

    def _execution(self) -> ChainExecutionUseCase:
        self.subsystem_registry.require(WELDING_CHAIN_SUBSYSTEM_ID)
        assert self._execution_use_case is not None
        return self._execution_use_case

    def _snapshots(self) -> ChainSnapshotUseCase:
        self.subsystem_registry.require(WELDING_CHAIN_SUBSYSTEM_ID)
        assert self._snapshot_use_case is not None
        return self._snapshot_use_case

    def _uncertainty(self) -> ChainUncertaintyService:
        self.subsystem_registry.require(WELDING_CHAIN_SUBSYSTEM_ID)
        assert self._uncertainty_service is not None
        return self._uncertainty_service

    def _evaluation(self) -> ChainEvaluationCatalog:
        self.subsystem_registry.require(WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID)
        assert self._evaluation_catalog is not None
        return self._evaluation_catalog

    def list_templates(self) -> list[ChainTemplateItem]:
        return list_chain_templates(self.store)

    def get_revision(self, revision_id: str) -> ChainRevision:
        return get_chain_revision(revision_id, self.store)

    def project_evaluation(self, project_id: str) -> ResolvedChainEvaluation:
        return get_project_chain_evaluation(
            project_id,
            self._evaluation(),
            self.store,
            self.workspace_catalog,
        )

    def candidate_inputs(
        self,
        project_id: str,
    ) -> tuple[ChainCandidateInputDefinition, ...]:
        return get_chain_candidate_inputs(
            project_id,
            self._planning(require_available=False),
        )

    def candidate_capability(self, project_id: str) -> ChainCandidateCapability:
        return get_chain_candidate_capability(project_id, self._planning())

    def candidate_contract(
        self,
        project_id: str,
    ) -> ChainCandidateContractResponse:
        return get_chain_candidate_contract(project_id, self._planning())

    def list_candidates(self, project_id: str) -> list[Candidate]:
        return list_chain_candidates(project_id, self.store)

    def create_candidate(
        self,
        project_id: str,
        payload: CandidateInput,
    ) -> Candidate:
        return create_chain_candidate(
            project_id,
            payload,
            self._planning(),
            self.store,
        )

    def update_candidate(
        self,
        project_id: str,
        candidate_id: str,
        payload: CandidateUpdate,
    ) -> Candidate:
        return update_chain_candidate(
            project_id,
            candidate_id,
            payload,
            self._planning(),
            self._execution(),
            self.store,
        )

    def candidate_revision(
        self,
        project_id: str,
        candidate_id: str,
        revision: int,
    ) -> Candidate:
        return get_chain_candidate_revision(
            project_id,
            candidate_id,
            revision,
            self.store,
        )

    def execute(
        self,
        project_id: str,
        candidate_id: str,
        payload: ChainExecutionRequest,
    ) -> ChainExecution:
        return execute_project_chain(
            project_id,
            candidate_id,
            payload,
            self._execution(),
        )

    def list_snapshots(
        self,
        project_id: str,
        candidate_id: str,
    ) -> list[ChainSnapshot]:
        return list_project_chain_snapshots(project_id, candidate_id, self.store)

    def latest_execution(
        self,
        project_id: str,
        candidate_id: str,
    ) -> ChainExecution:
        return get_project_chain_execution(project_id, candidate_id, self.store)

    def distribution_capability(
        self,
        project_id: str,
    ) -> ChainDistributionCapability:
        return get_project_chain_distribution_capability(
            project_id,
            self._uncertainty(),
        )

    def run_distribution(
        self,
        project_id: str,
        candidate_id: str,
        payload: ChainDistributionRequest,
    ) -> ChainDistributionRun:
        return run_project_chain_distribution(
            project_id,
            candidate_id,
            payload,
            self._uncertainty(),
        )

    def latest_distribution(
        self,
        project_id: str,
        candidate_id: str,
    ) -> ChainDistributionRun:
        return get_latest_project_chain_distribution(
            project_id,
            candidate_id,
            self.store,
        )

    def distribution_run(self, run_id: str) -> ChainDistributionRun:
        return get_chain_distribution_run(run_id, self.store)

    def create_snapshot(
        self,
        project_id: str,
        candidate_id: str,
        payload: ChainExecutionRequest,
    ) -> ChainSnapshot:
        return create_project_chain_snapshot(
            project_id,
            candidate_id,
            payload,
            self._snapshots(),
        )

    def snapshot(self, project_id: str, snapshot_id: str) -> ChainSnapshot:
        return get_chain_snapshot(project_id, snapshot_id, self.store)

    def analysis_variants(
        self,
        project_id: str,
        candidate_id: str,
    ) -> list[ActualConditionedVariant]:
        return list_project_chain_analysis_variants(
            project_id,
            candidate_id,
            self.store,
        )

    def create_analysis_variant(
        self,
        project_id: str,
        candidate_id: str,
        payload: ActualConditionedVariantRequest,
    ) -> ActualConditionedVariant:
        return create_project_chain_analysis_variant(
            project_id,
            candidate_id,
            payload,
            self._snapshots(),
        )
