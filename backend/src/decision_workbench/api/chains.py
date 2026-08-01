from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from decision_workbench.application.chains import (
    ChainCandidateRevisionError,
    ChainConflictError,
    ChainNotFoundError,
    ChainUseCases,
    ChainValidationError,
)
from decision_workbench.contracts.chain_api_contracts import (
    ActualConditionedVariantRequest,
    ChainCandidateContractResponse,
    ChainDistributionRequest,
    ChainExecutionRequest,
    ChainGraphResponse,
    ChainStudioCatalogResponse,
    ChainStudioDraftRequest,
    ChainStudioDraftValidation,
    ChainTemplateItem,
)
from decision_workbench.contracts.chain_contracts import ChainRevision
from decision_workbench.contracts.chain_evaluation_contracts import (
    ResolvedChainEvaluation,
)
from decision_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ChainCandidateCapability,
    ChainCandidateInputDefinition,
    ChainExecution,
    ChainSnapshot,
)
from decision_workbench.contracts.chain_uncertainty_contracts import (
    ChainDistributionCapability,
    ChainDistributionRun,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
    CandidateUpdate,
)

from .dependencies import get_chain_use_cases


router = APIRouter(prefix="/api/chains", tags=["chains"])
execution_router = APIRouter(prefix="/api/projects", tags=["chain-execution"])
ChainDependency = Annotated[ChainUseCases, Depends(get_chain_use_cases)]


def _translate_chain_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ChainCandidateRevisionError):
        return HTTPException(
            409,
            {
                "message": str(exc),
                "current": exc.current.model_dump(mode="json"),
            },
        )
    if isinstance(exc, ChainNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ChainValidationError):
        return HTTPException(422, str(exc))
    if isinstance(exc, ChainConflictError):
        return HTTPException(409, str(exc))
    raise exc


def _call(operation):
    try:
        return operation()
    except (
        ChainCandidateRevisionError,
        ChainNotFoundError,
        ChainValidationError,
        ChainConflictError,
    ) as exc:
        raise _translate_chain_error(exc) from exc


@router.get("", response_model=list[ChainTemplateItem], operation_id="listChainTemplates")
def list_chain_templates(use_cases: ChainDependency) -> list[ChainTemplateItem]:
    return use_cases.list_templates()


@router.get(
    "/studio/catalog",
    response_model=ChainStudioCatalogResponse,
    operation_id="getChainStudioCatalog",
)
def get_chain_studio_catalog(
    use_cases: ChainDependency,
) -> ChainStudioCatalogResponse:
    return _call(use_cases.studio_catalog)


@router.post(
    "/studio/validate",
    response_model=ChainStudioDraftValidation,
    operation_id="validateChainStudioDraft",
)
def validate_chain_studio_draft(
    payload: ChainStudioDraftRequest,
    use_cases: ChainDependency,
) -> ChainStudioDraftValidation:
    return _call(lambda: use_cases.validate_studio_draft(payload))


@router.post(
    "/studio/publish",
    response_model=ChainTemplateItem,
    status_code=201,
    operation_id="publishChainStudioDraft",
)
def publish_chain_studio_draft(
    payload: ChainStudioDraftRequest,
    use_cases: ChainDependency,
) -> ChainTemplateItem:
    return _call(lambda: use_cases.publish_studio_draft(payload))


@router.get(
    "/revisions/{revision_id}",
    response_model=ChainRevision,
    operation_id="getChainRevision",
)
def get_chain_revision(
    revision_id: str,
    use_cases: ChainDependency,
) -> ChainRevision:
    return _call(lambda: use_cases.get_revision(revision_id))


@execution_router.get(
    "/{project_id}/chain/graph",
    response_model=ChainGraphResponse,
    operation_id="getProjectChainGraph",
)
def get_project_chain_graph(
    project_id: str,
    use_cases: ChainDependency,
) -> ChainGraphResponse:
    return _call(lambda: use_cases.graph(project_id))


@execution_router.get(
    "/{project_id}/chain/evaluation",
    response_model=ResolvedChainEvaluation,
    operation_id="getProjectChainEvaluation",
)
def get_project_chain_evaluation(
    project_id: str,
    use_cases: ChainDependency,
) -> ResolvedChainEvaluation:
    return _call(lambda: use_cases.project_evaluation(project_id))


@execution_router.get(
    "/{project_id}/chain/candidate-inputs",
    response_model=tuple[ChainCandidateInputDefinition, ...],
    operation_id="getChainCandidateInputs",
)
def get_chain_candidate_inputs(
    project_id: str,
    use_cases: ChainDependency,
) -> tuple[ChainCandidateInputDefinition, ...]:
    """Read-only input surface derived from the exact pinned Chain revision."""

    return _call(lambda: use_cases.candidate_inputs(project_id))


@execution_router.get(
    "/{project_id}/chain/candidate-capability",
    response_model=ChainCandidateCapability,
    operation_id="getChainCandidateCapability",
)
def get_chain_candidate_capability(
    project_id: str,
    use_cases: ChainDependency,
) -> ChainCandidateCapability:
    """Which candidate surface this Chain needs, before any editor is rendered."""

    return _call(lambda: use_cases.candidate_capability(project_id))


@execution_router.get(
    "/{project_id}/chain/candidate-contract",
    response_model=ChainCandidateContractResponse,
    operation_id="getChainCandidateContract",
)
def get_chain_candidate_contract(
    project_id: str,
    use_cases: ChainDependency,
) -> ChainCandidateContractResponse:
    return _call(lambda: use_cases.candidate_contract(project_id))


@execution_router.get(
    "/{project_id}/chain/starter-candidate",
    response_model=CandidateInput,
    operation_id="getChainStarterCandidate",
)
def get_chain_starter_candidate(
    project_id: str,
    use_cases: ChainDependency,
) -> CandidateInput:
    """Resolve a usable initial draft through the fixed candidate adapter."""

    return _call(lambda: use_cases.starter_candidate(project_id))


@execution_router.get(
    "/{project_id}/chain/candidates",
    response_model=list[Candidate],
    operation_id="listChainCandidates",
)
def list_chain_candidates(
    project_id: str,
    use_cases: ChainDependency,
) -> list[Candidate]:
    return _call(lambda: use_cases.list_candidates(project_id))


@execution_router.post(
    "/{project_id}/chain/candidates",
    response_model=Candidate,
    status_code=201,
    operation_id="createChainCandidate",
)
def create_chain_candidate(
    project_id: str,
    payload: CandidateInput,
    use_cases: ChainDependency,
) -> Candidate:
    return _call(lambda: use_cases.create_candidate(project_id, payload))


@execution_router.put(
    "/{project_id}/chain/candidates/{candidate_id}",
    response_model=Candidate,
    operation_id="updateChainCandidate",
)
def update_chain_candidate(
    project_id: str,
    candidate_id: str,
    payload: CandidateUpdate,
    use_cases: ChainDependency,
) -> Candidate:
    return _call(
        lambda: use_cases.update_candidate(project_id, candidate_id, payload)
    )


@execution_router.get(
    "/{project_id}/chain/candidates/{candidate_id}/revisions/{revision}",
    response_model=Candidate,
    operation_id="getChainCandidateRevision",
)
def get_chain_candidate_revision(
    project_id: str,
    candidate_id: str,
    revision: int,
    use_cases: ChainDependency,
) -> Candidate:
    return _call(
        lambda: use_cases.candidate_revision(
            project_id,
            candidate_id,
            revision,
        )
    )


@execution_router.post(
    "/{project_id}/chain/candidates/{candidate_id}/executions",
    response_model=ChainExecution,
    operation_id="executeProjectChain",
)
def execute_project_chain(
    project_id: str,
    candidate_id: str,
    payload: ChainExecutionRequest,
    use_cases: ChainDependency,
) -> ChainExecution:
    return _call(lambda: use_cases.execute(project_id, candidate_id, payload))


@execution_router.get(
    "/{project_id}/chain/candidates/{candidate_id}/snapshots",
    response_model=list[ChainSnapshot],
    operation_id="listProjectChainSnapshots",
)
def list_project_chain_snapshots(
    project_id: str,
    candidate_id: str,
    use_cases: ChainDependency,
) -> list[ChainSnapshot]:
    return _call(lambda: use_cases.list_snapshots(project_id, candidate_id))


@execution_router.get(
    "/{project_id}/chain/candidates/{candidate_id}/execution",
    response_model=ChainExecution,
    operation_id="getProjectChainExecution",
)
def get_project_chain_execution(
    project_id: str,
    candidate_id: str,
    use_cases: ChainDependency,
) -> ChainExecution:
    return _call(lambda: use_cases.latest_execution(project_id, candidate_id))


@execution_router.get(
    "/{project_id}/chain/distribution-capability",
    response_model=ChainDistributionCapability,
    operation_id="getProjectChainDistributionCapability",
)
def get_project_chain_distribution_capability(
    project_id: str,
    use_cases: ChainDependency,
) -> ChainDistributionCapability:
    return _call(lambda: use_cases.distribution_capability(project_id))


@execution_router.post(
    "/{project_id}/chain/candidates/{candidate_id}/distribution-runs",
    response_model=ChainDistributionRun,
    status_code=201,
    operation_id="runProjectChainDistribution",
)
def run_project_chain_distribution(
    project_id: str,
    candidate_id: str,
    payload: ChainDistributionRequest,
    use_cases: ChainDependency,
) -> ChainDistributionRun:
    return _call(
        lambda: use_cases.run_distribution(project_id, candidate_id, payload)
    )


@execution_router.get(
    "/{project_id}/chain/candidates/{candidate_id}/distribution-runs/latest",
    response_model=ChainDistributionRun,
    operation_id="getLatestProjectChainDistribution",
)
def get_latest_project_chain_distribution(
    project_id: str,
    candidate_id: str,
    use_cases: ChainDependency,
) -> ChainDistributionRun:
    return _call(lambda: use_cases.latest_distribution(project_id, candidate_id))


@execution_router.get(
    "/chain-distribution-runs/{run_id}",
    response_model=ChainDistributionRun,
    operation_id="getChainDistributionRun",
)
def get_chain_distribution_run(
    run_id: str,
    use_cases: ChainDependency,
) -> ChainDistributionRun:
    return _call(lambda: use_cases.distribution_run(run_id))


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
    use_cases: ChainDependency,
) -> ChainSnapshot:
    return _call(
        lambda: use_cases.create_snapshot(project_id, candidate_id, payload)
    )


@execution_router.get(
    "/{project_id}/chain-snapshots/{snapshot_id}",
    response_model=ChainSnapshot,
    operation_id="getChainSnapshot",
)
def get_chain_snapshot(
    project_id: str,
    snapshot_id: str,
    use_cases: ChainDependency,
) -> ChainSnapshot:
    return _call(lambda: use_cases.snapshot(project_id, snapshot_id))


@execution_router.get(
    "/{project_id}/chain/candidates/{candidate_id}/analysis-variants",
    response_model=list[ActualConditionedVariant],
    operation_id="listProjectChainAnalysisVariants",
)
def list_project_chain_analysis_variants(
    project_id: str,
    candidate_id: str,
    use_cases: ChainDependency,
) -> list[ActualConditionedVariant]:
    return _call(lambda: use_cases.analysis_variants(project_id, candidate_id))


@execution_router.post(
    "/{project_id}/chain/candidates/{candidate_id}/analysis-variants",
    response_model=ActualConditionedVariant,
    status_code=201,
    operation_id="createProjectChainAnalysisVariant",
)
def create_project_chain_analysis_variant(
    project_id: str,
    candidate_id: str,
    payload: ActualConditionedVariantRequest,
    use_cases: ChainDependency,
) -> ActualConditionedVariant:
    return _call(
        lambda: use_cases.create_analysis_variant(
            project_id,
            candidate_id,
            payload,
        )
    )
