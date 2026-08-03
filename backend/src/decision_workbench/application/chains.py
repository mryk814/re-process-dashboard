from __future__ import annotations

import uuid

from decision_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapterError,
    ScalarChainAdapter,
)
from decision_workbench.application.chain.plan import (
    ChainExecutionError,
    ChainPlanningUseCase,
)
from decision_workbench.application.chain.execution import (
    ChainExecutionUseCase,
)
from decision_workbench.application.chain.snapshot import (
    ChainSnapshotUseCase,
)
from decision_workbench.application.chain_uncertainty import ChainUncertaintyService
from decision_workbench.application.chain_evaluation import (
    ChainEvaluationCatalog,
    ChainEvaluationError,
)
from decision_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainStageLock,
    GraphDefinitionRef,
    GraphRevisionRef,
    StageContractSurface,
    build_chain_revision,
    project_prediction_graph,
    task_contract_surface,
    validate_chain_definition,
)
from decision_workbench.contracts.chain_api_contracts import (
    ActualConditionedVariantRequest,
    ChainCandidateContractResponse,
    ChainDistributionRequest,
    ChainExecutionRequest,
    ChainGraphResponse,
    ChainGraphStageContract,
    ChainStudioCatalogResponse,
    ChainStudioDraftRequest,
    ChainStudioDraftValidation,
    ChainStudioStageCatalogItem,
    ChainTemplateItem,
)
from decision_workbench.contracts.task_contracts import persisted_task_definition_payload
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
from decision_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError
from decision_workbench.execution.inference_work_graph import semantic_digest
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


def _definition_id(definition: GraphDefinitionRef) -> str:
    return f"{definition.chain_id}@{definition.digest.removeprefix('sha256:')[:12]}"


def list_chain_templates(store: Store) -> list[ChainTemplateItem]:
    revisions = store.list_chain_revisions()
    latest_by_chain = {
        chain_id: max(
            (
                revision
                for revision in revisions
                if revision.chain_id == chain_id
            ),
            key=lambda revision: revision.revision,
        )
        for chain_id in {revision.chain_id for revision in revisions}
    }
    templates: list[ChainTemplateItem] = []
    for definition in store.list_chain_definitions():
        latest = latest_by_chain.get(definition.chain_id)
        templates.append(
            ChainTemplateItem(
                definition_id=_definition_id(definition),
                definition=definition,
                revisions=tuple(
                    revision
                    for revision in revisions
                    if revision.chain_id == definition.chain_id
                    and revision.chain_definition_digest == definition.digest
                ),
                is_default=(
                    latest is not None
                    and latest.chain_definition_digest == definition.digest
                ),
                default_revision_id=(
                    f"{latest.chain_id}:r{latest.revision}"
                    if latest is not None
                    else None
                ),
                latest_revision_id=(
                    f"{latest.chain_id}:r{latest.revision}"
                    if latest is not None
                    else None
                ),
            )
        )
    return sorted(
        templates,
        key=lambda item: (
            item.definition.chain_id,
            not item.is_default,
            item.definition_id,
        ),
    )


def get_chain_revision(
    revision_id: str,
    store: Store,
) -> GraphRevisionRef:
    revision = store.get_chain_revision(revision_id)
    if revision is None:
        raise ChainNotFoundError("Chain Revisionが見つかりません")
    return revision


def resolve_task_stage_surface(
    registry: TaskRegistry,
    task_id: str,
) -> StageContractSurface:
    definition = registry.contract_for(task_id).task_definition
    return task_contract_surface(
        definition,
        contract_digest=semantic_digest(persisted_task_definition_payload(definition)),
    )


def resolve_task_stage_lock(
    catalog: WorkspaceCatalog,
    registry: TaskRegistry,
    surface: StageContractSurface,
) -> ChainStageLock:
    """Resolve the exact existing Package and Dataset identity for one Task.

    The editor never accepts a client-supplied package or dataset reference.
    It can only publish a Task whose currently loaded runtime has one matching
    catalog record and a single canonical Dataset View.
    """

    entry = registry.entry_for(surface.contract_id)
    package = entry.model_package
    package_refs = [
        item
        for item in catalog.list_model_package_refs()
        if item.task_id == surface.contract_id
        and item.manifest_digest == package.manifest_sha256
        and item.task_contract_digest == surface.contract_digest
    ]
    if len(package_refs) != 1:
        raise ChainValidationError(
            f"Task {surface.contract_id} のModel Package参照を一意に固定できません"
        )
    profile_digest = package.manifest.provenance.dataset_profile_id
    if not profile_digest.startswith("sha256:"):
        raise ChainValidationError(
            f"Task {surface.contract_id} のDataset Profile digestが不正です"
        )
    dataset_ids = {
        dataset.id
        for dataset in catalog.list_dataset_revisions()
        if (profile := catalog.get_profile_revision(dataset.profile_revision_id)) is not None
        and (asset := catalog.get_data_asset(dataset.data_asset_id)) is not None
        and profile.profile_digest == profile_digest
        and asset.sha256 == entry.predictor_runtime.data.source_sha256
    }
    view_ids = {
        view.id
        for view in catalog.list_dataset_view_revisions()
        if view.kind == "single"
        and len(view.members) == 1
        and view.members[0].dataset_revision_id in dataset_ids
        and view.view_id == f"single-{view.members[0].dataset_revision_id}"
    }
    if len(view_ids) != 1:
        raise ChainValidationError(
            f"Task {surface.contract_id} のDataset Viewを一意に固定できません"
        )
    return ChainStageLock(
        contract_digest=surface.contract_digest,
        package_manifest_digest=f"sha256:{package.manifest_sha256}",
        dataset_view_revision_id=next(iter(view_ids)),
        dataset_profile_digest=profile_digest,
    )


def scalar_chain_catalog(
    registry: TaskRegistry,
    workspace_catalog: WorkspaceCatalog,
) -> ChainStudioCatalogResponse:
    items: list[ChainStudioStageCatalogItem] = []
    for task_id in registry.task_ids:
        try:
            registry.require_available(task_id)
            surface = resolve_task_stage_surface(registry, task_id)
            lock = resolve_task_stage_lock(
                workspace_catalog,
                registry,
                surface,
            )
            items.append(ChainStudioStageCatalogItem(
                contract_id=task_id,
                label=registry.contract_for(task_id).task_definition.label,
                status="available",
                surface=surface,
                stage_lock=lock,
            ))
        except (ChainValidationError, TaskRegistryError, ValueError) as exc:
            items.append(ChainStudioStageCatalogItem(
                contract_id=task_id,
                label=registry.contract_for(task_id).task_definition.label,
                status="unavailable",
                reason=str(exc),
            ))
    return ChainStudioCatalogResponse(stages=tuple(items))


def _validate_scalar_draft(
    payload: ChainStudioDraftRequest,
    *,
    registry: TaskRegistry,
    workspace_catalog: WorkspaceCatalog,
) -> tuple[dict[tuple[str, str], StageContractSurface], dict[str, ChainStageLock]]:
    definition = payload.definition
    if not 2 <= len(definition.stages) <= 4:
        raise ChainValidationError("scalar ChainのStage数は2〜4にしてください")
    if any(stage.stage_kind != "task" for stage in definition.stages):
        raise ChainValidationError("scalar/v1 editorはTask Stageだけを公開できます")
    catalog = scalar_chain_catalog(registry, workspace_catalog)
    available = {
        item.contract_id: item
        for item in catalog.stages
        if item.status == "available" and item.surface is not None
        and item.stage_lock is not None
    }
    contracts: dict[tuple[str, str], StageContractSurface] = {}
    locks: dict[str, ChainStageLock] = {}
    for stage in definition.stages:
        item = available.get(stage.contract_id)
        if item is None:
            unavailable = next((entry for entry in catalog.stages if entry.contract_id == stage.contract_id), None)
            reason = unavailable.reason if unavailable is not None else "Stage catalogにありません"
            raise ChainValidationError(
                f"Task Stageを公開できません: {stage.contract_id}（{reason}）"
            )
        contracts[("task", stage.contract_id)] = item.surface
        locks[stage.stage_id] = item.stage_lock
    try:
        validate_chain_definition(definition, contracts=contracts)
        adapter = ScalarChainAdapter()
        for port in definition.external_inputs:
            adapter.candidate_path(port.path, port.value_kind, port.quantity)
    except (ValueError, ChainCandidateAdapterError) as exc:
        raise ChainValidationError(str(exc)) from exc
    return contracts, locks


def validate_scalar_chain_draft(
    payload: ChainStudioDraftRequest,
    *,
    registry: TaskRegistry,
    workspace_catalog: WorkspaceCatalog,
) -> ChainStudioDraftValidation:
    _validate_scalar_draft(
        payload, registry=registry, workspace_catalog=workspace_catalog
    )
    return ChainStudioDraftValidation(
        valid=True,
        definition_digest=payload.definition.digest,
        message="Task contract、binding、unit／basis、Package／Dataset固定を確認しました。",
    )


def publish_scalar_chain_draft(
    payload: ChainStudioDraftRequest,
    *,
    store: Store,
    registry: TaskRegistry,
    workspace_catalog: WorkspaceCatalog,
) -> ChainTemplateItem:
    contracts, locks = _validate_scalar_draft(
        payload, registry=registry, workspace_catalog=workspace_catalog
    )
    existing = [
        revision for revision in store.list_chain_revisions()
        if revision.chain_id == payload.definition.chain_id
    ]
    revision = build_chain_revision(
        payload.definition,
        revision=max((item.revision for item in existing), default=0) + 1,
        contracts=contracts,
        stage_locks=locks,
    )
    store.register_chain_definition(payload.definition)
    store.register_chain_revision(revision, contracts=contracts)
    return ChainTemplateItem(
        definition_id=_definition_id(payload.definition),
        definition=payload.definition,
        revisions=(revision,),
        is_default=True,
        default_revision_id=f"{revision.chain_id}:r{revision.revision}",
        latest_revision_id=f"{revision.chain_id}:r{revision.revision}",
    )


def get_project_chain_graph(project_id: str, store: Store) -> ChainGraphResponse:
    project = store.get_project(project_id)
    if project is None:
        raise ChainNotFoundError("Chain Projectが見つかりません")
    identity = project.scientific_identity
    if identity.identity_kind == "chain":
        revision_id = identity.chain_revision_id
        revision_digest = identity.chain_revision_digest
    elif identity.identity_kind == "prediction_graph":
        revision_id = identity.graph_revision_id
        revision_digest = identity.graph_revision_digest
    else:
        raise ChainConflictError("このAPIはGraph Project専用です")
    revision = store.get_chain_revision(revision_id)
    if revision is None or revision.revision_digest != revision_digest:
        raise ChainConflictError("固定されたGraph Revisionを解決できません")
    definition = store.get_chain_definition(
        revision.chain_id, revision.chain_definition_digest
    )
    if definition is None:
        raise ChainConflictError("固定されたChain Definitionを解決できません")
    surfaces = store.get_chain_stage_contract_surfaces(revision_id)
    resolved: list[ChainGraphStageContract] = []
    for stage in revision.stages:
        surface = surfaces.get(stage.stage_id)
        if surface is None:
            resolved.append(ChainGraphStageContract(
                stage_id=stage.stage_id,
                status="unavailable",
                reason="この固定RevisionのStage contract surfaceは保存されていません",
            ))
        elif (
            surface.stage_kind != stage.stage_kind
            or surface.contract_id != stage.contract_id
            or surface.contract_digest != stage.contract_digest
        ):
            resolved.append(ChainGraphStageContract(
                stage_id=stage.stage_id,
                status="unavailable",
                reason="保存済みStage contract surfaceが固定Revisionと一致しません",
            ))
        else:
            resolved.append(ChainGraphStageContract(
                stage_id=stage.stage_id,
                status="available",
                surface=surface,
            ))
    return ChainGraphResponse(
        definition=definition,
        revision=revision,
        prediction_graph=project_prediction_graph(
            definition,
            contracts={
                (surface.stage_kind, surface.contract_id): surface
                for surface in surfaces.values()
            },
        ),
        stage_contracts=tuple(resolved),
    )


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
        task_registry: TaskRegistry,
        planning_use_case: ChainPlanningUseCase | None,
        execution_use_case: ChainExecutionUseCase | None,
        snapshot_use_case: ChainSnapshotUseCase | None,
        uncertainty_service: ChainUncertaintyService | None,
        evaluation_catalog: ChainEvaluationCatalog | None,
        subsystem_registry: SubsystemAvailabilityRegistry,
    ) -> None:
        self.store = store
        self.workspace_catalog = workspace_catalog
        self.task_registry = task_registry
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

    def get_revision(self, revision_id: str) -> GraphRevisionRef:
        return get_chain_revision(revision_id, self.store)

    def studio_catalog(self) -> ChainStudioCatalogResponse:
        return scalar_chain_catalog(self.task_registry, self.workspace_catalog)

    def validate_studio_draft(
        self, payload: ChainStudioDraftRequest
    ) -> ChainStudioDraftValidation:
        return validate_scalar_chain_draft(
            payload,
            registry=self.task_registry,
            workspace_catalog=self.workspace_catalog,
        )

    def publish_studio_draft(
        self, payload: ChainStudioDraftRequest
    ) -> ChainTemplateItem:
        return publish_scalar_chain_draft(
            payload,
            store=self.store,
            registry=self.task_registry,
            workspace_catalog=self.workspace_catalog,
        )

    def graph(self, project_id: str) -> ChainGraphResponse:
        return get_project_chain_graph(project_id, self.store)

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
        return get_chain_candidate_capability(
            project_id, self._planning(require_available=False)
        )

    def candidate_contract(
        self,
        project_id: str,
    ) -> ChainCandidateContractResponse:
        return get_chain_candidate_contract(project_id, self._planning())

    def starter_candidate(self, project_id: str) -> CandidateInput:
        """Build the initial candidate through the revision-selected adapter."""

        try:
            return self._planning().starter_candidate(project_id)
        except (ChainExecutionError, ChainCandidateAdapterError) as exc:
            raise ChainConflictError(str(exc)) from exc

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
