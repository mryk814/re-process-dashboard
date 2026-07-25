from __future__ import annotations

from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from material_workbench.api.dependencies import get_store
from material_workbench.application.chain_execution import (
    ChainExecutionError,
    ChainExecutionService,
)
from material_workbench.application.chain_evaluation import (
    ChainEvaluationCatalog,
    ChainEvaluationError,
)
from material_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainRevision,
)
from material_workbench.contracts.chain_execution_contracts import (
    ChainExecution,
    ChainSnapshot,
)
from material_workbench.contracts.chain_evaluation_contracts import (
    ResolvedChainEvaluation,
)
from material_workbench.contracts.blend_contracts import (
    RevisionRef,
    SparseBlendDesignSpace,
)
from material_workbench.contracts.schemas import (
    Candidate,
    CandidateInput,
    CandidateUpdate,
)
from material_workbench.persistence.store import (
    CandidateRevisionConflictError,
    Store,
)


router = APIRouter(prefix="/api/chains", tags=["chains"])
execution_router = APIRouter(prefix="/api/projects", tags=["chain-execution"])
StoreDependency = Annotated[Store, Depends(get_store)]


class ChainApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChainTemplateItem(ChainApiModel):
    definition_id: str
    definition: ChainDefinition
    revisions: tuple[ChainRevision, ...]


class ChainExecutionRequest(ChainApiModel):
    candidate_revision: int = Field(ge=1)
    request_id: str | None = None
    debounce_ms: int = Field(default=250, ge=0, le=1000)


class ChainCandidateContractResponse(ChainApiModel):
    scientific_master: RevisionRef
    commercial_catalog: RevisionRef
    design_space: SparseBlendDesignSpace
    design_space_ref: RevisionRef


def _definition_id(definition: ChainDefinition) -> str:
    return f"{definition.chain_id}@{definition.digest.removeprefix('sha256:')[:12]}"


@router.get(
    "",
    response_model=list[ChainTemplateItem],
    operation_id="listChainTemplates",
)
def list_chain_templates(store: StoreDependency) -> list[ChainTemplateItem]:
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


@router.get(
    "/revisions/{revision_id}",
    response_model=ChainRevision,
    operation_id="getChainRevision",
)
def get_chain_revision(
    revision_id: str,
    store: StoreDependency,
) -> ChainRevision:
    revision = store.get_chain_revision(revision_id)
    if revision is None:
        raise HTTPException(404, "Chain Revisionが見つかりません")
    return revision


def _execution_service(request: Request) -> ChainExecutionService:
    return request.app.state.chain_execution_service


def _evaluation_catalog(request: Request) -> ChainEvaluationCatalog:
    return request.app.state.chain_evaluation_catalog


@execution_router.get(
    "/{project_id}/chain/evaluation",
    response_model=ResolvedChainEvaluation,
    operation_id="getProjectChainEvaluation",
)
def get_project_chain_evaluation(
    project_id: str,
    catalog: Annotated[ChainEvaluationCatalog, Depends(_evaluation_catalog)],
    store: StoreDependency,
) -> ResolvedChainEvaluation:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "Chain Projectが見つかりません")
    identity = project.scientific_identity
    if identity.identity_kind != "chain":
        raise HTTPException(409, "このAPIはChain Project専用です")
    revision = store.get_chain_revision(identity.chain_revision_id)
    if (
        revision is None
        or revision.revision_digest != identity.chain_revision_digest
    ):
        raise HTTPException(409, "固定されたChain Revisionを解決できません")
    try:
        return catalog.resolve(
            revision_id=identity.chain_revision_id,
            revision=revision,
        )
    except ChainEvaluationError as exc:
        raise HTTPException(409, str(exc)) from exc


@execution_router.get(
    "/{project_id}/chain/candidate-contract",
    response_model=ChainCandidateContractResponse,
    operation_id="getChainCandidateContract",
)
def get_chain_candidate_contract(
    project_id: str,
    service: Annotated[ChainExecutionService, Depends(_execution_service)],
) -> ChainCandidateContractResponse:
    try:
        contracts = service.candidate_contracts(project_id)
    except ChainExecutionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return ChainCandidateContractResponse(
        scientific_master=contracts.design_space.scientific_master,
        commercial_catalog=contracts.commercial_catalog.ref,
        design_space=contracts.design_space,
        design_space_ref=contracts.design_space.ref,
    )


@execution_router.get(
    "/{project_id}/chain/candidates",
    response_model=list[Candidate],
    operation_id="listChainCandidates",
)
def list_chain_candidates(
    project_id: str,
    service: Annotated[ChainExecutionService, Depends(_execution_service)],
    store: StoreDependency,
) -> list[Candidate]:
    service.candidate_contracts(project_id)
    return store.list_candidates(project_id)


@execution_router.post(
    "/{project_id}/chain/candidates",
    response_model=Candidate,
    status_code=201,
    operation_id="createChainCandidate",
)
def create_chain_candidate(
    project_id: str,
    payload: CandidateInput,
    request: Request,
    store: StoreDependency,
) -> Candidate:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "Chain Projectが見つかりません")
    if project.scientific_identity.identity_kind != "chain":
        raise HTTPException(409, "このAPIはChain Project専用です")
    try:
        prepared = request.app.state.chain_execution_service.prepare_candidate(
            project_id, payload
        )
    except ChainExecutionError as exc:
        raise HTTPException(422, str(exc)) from exc
    return store.create_candidate(prepared, project_id)


@execution_router.put(
    "/{project_id}/chain/candidates/{candidate_id}",
    response_model=Candidate,
    operation_id="updateChainCandidate",
)
def update_chain_candidate(
    project_id: str,
    candidate_id: str,
    payload: CandidateUpdate,
    service: Annotated[ChainExecutionService, Depends(_execution_service)],
    store: StoreDependency,
) -> Candidate:
    try:
        prepared = service.prepare_candidate(
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
        raise HTTPException(422, str(exc)) from exc
    except CandidateRevisionConflictError as exc:
        raise HTTPException(
            409,
            {
                "message": str(exc),
                "current": exc.current.model_dump(mode="json"),
            },
        ) from exc
    if updated is None:
        raise HTTPException(404, "Chain候補が見つかりません")
    scope_id = store.chain_execution_scope(project_id, candidate_id)
    service.coordinator.begin(scope_id, invalidation_request_id)
    service.mark_candidate_changed(
        project_id=project_id,
        candidate_id=candidate_id,
        candidate_revision=updated.revision,
        request_id=invalidation_request_id,
        generation=generation,
    )
    return updated


@execution_router.get(
    "/{project_id}/chain/candidates/{candidate_id}/revisions/{revision}",
    response_model=Candidate,
    operation_id="getChainCandidateRevision",
)
def get_chain_candidate_revision(
    project_id: str,
    candidate_id: str,
    revision: int,
    service: Annotated[ChainExecutionService, Depends(_execution_service)],
    store: StoreDependency,
) -> Candidate:
    service.candidate_contracts(project_id)
    candidate = store.get_candidate_revision(candidate_id, revision, project_id)
    if candidate is None:
        raise HTTPException(404, "Chain candidate revisionが見つかりません")
    return candidate


@execution_router.post(
    "/{project_id}/chain/candidates/{candidate_id}/executions",
    response_model=ChainExecution,
    operation_id="executeProjectChain",
)
def execute_project_chain(
    project_id: str,
    candidate_id: str,
    payload: ChainExecutionRequest,
    service: Annotated[ChainExecutionService, Depends(_execution_service)],
) -> ChainExecution:
    try:
        return service.execute(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=payload.candidate_revision,
            request_id=payload.request_id,
            debounce_ms=payload.debounce_ms,
        )
    except ChainExecutionError as exc:
        raise HTTPException(409, str(exc)) from exc


@execution_router.get(
    "/{project_id}/chain/candidates/{candidate_id}/execution",
    response_model=ChainExecution,
    operation_id="getProjectChainExecution",
)
def get_project_chain_execution(
    project_id: str,
    candidate_id: str,
    store: StoreDependency,
) -> ChainExecution:
    execution = store.get_chain_execution(project_id, candidate_id)
    if execution is None:
        raise HTTPException(404, "Chain実行結果がありません")
    return execution


@execution_router.post(
    "/{project_id}/chain/candidates/{candidate_id}/snapshots",
    response_model=ChainSnapshot,
    status_code=201,
    operation_id="createProjectChainSnapshot",
)
def create_project_chain_snapshot(
    project_id: str,
    candidate_id: str,
    payload: ChainExecutionRequest,
    service: Annotated[ChainExecutionService, Depends(_execution_service)],
) -> ChainSnapshot:
    try:
        return service.snapshot(
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=payload.candidate_revision,
        )
    except ChainExecutionError as exc:
        raise HTTPException(409, str(exc)) from exc


@execution_router.get(
    "/chain-snapshots/{snapshot_id}",
    response_model=ChainSnapshot,
    operation_id="getChainSnapshot",
)
def get_chain_snapshot(
    snapshot_id: str,
    store: StoreDependency,
) -> ChainSnapshot:
    snapshot = store.get_chain_snapshot(snapshot_id)
    if snapshot is None:
        raise HTTPException(404, "Chain snapshotが見つかりません")
    return snapshot
