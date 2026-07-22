from __future__ import annotations

from .projects import ProjectService
from ..schemas import Candidate, CandidateImportResponse, CandidateInput, CandidateUpdate
from ..services import candidates_xlsx, import_candidates_xlsx
from ..store import (
    CandidateArchivedError,
    CandidateLimitError,
    CandidateRevisionConflictError,
    ProjectNotFoundError,
    Store,
    StoreDataIntegrityError,
)
from ..task_registry import TaskRegistry, TaskRegistryError
from ..project_runtime_resolver import ProjectRuntimeResolver


class CandidateNotFoundError(LookupError):
    pass


class CandidateValidationError(ValueError):
    pass


class CandidateProvenanceImmutableError(ValueError):
    pass


class CandidateService:
    def __init__(self, store: Store, registry: TaskRegistry, resolver: ProjectRuntimeResolver) -> None:
        self.store = store
        self.registry = registry
        self.resolver = resolver
        self.projects = ProjectService(store, registry)

    def list(self, project_id: str, *, include_archived: bool = False) -> list[Candidate]:
        self.projects.require(project_id)
        return self.store.list_candidates(project_id, include_archived=include_archived)

    def create(self, project_id: str, payload: CandidateInput) -> Candidate:
        project = self.projects.require(project_id)
        if payload.provenance.source_kind == "copy":
            reference = payload.provenance.source_ref
            source_candidate = self.store.get_candidate(
                reference.candidate_id,
                reference.project_id,
                include_archived=True,
            )
            if source_candidate is None:
                raise CandidateValidationError("コピー元候補が見つかりません")
            if source_candidate.revision != reference.candidate_revision:
                raise CandidateValidationError("コピー元候補のrevisionが一致しません")
            source_project = self.projects.require(reference.project_id)
            if source_project.task_id != project.task_id:
                raise CandidateValidationError("異なる予測タスクの候補はコピーできません")
        self._validate(project.task_id, payload)
        return self.store.create_candidate(payload, project_id)

    def import_xlsx(self, project_id: str, contents: bytes) -> CandidateImportResponse:
        project = self.projects.require(project_id)
        entry = self.registry.entry_for(project.task_id)
        if not entry.application_capability.candidate_excel_import:
            raise CandidateValidationError("Excel候補importはこの予測タスクでは利用できません")
        runtime = self.resolver.runtime_for(project)
        payloads, errors = import_candidates_xlsx(
            contents,
            task_id=project.task_id,
            profile=getattr(runtime.data, "profile", None),
        )
        created = self.store.create_candidates(payloads, project_id)
        return CandidateImportResponse(created=len(created), errors=errors, candidates=created)

    def export_xlsx(self, project_id: str) -> bytes:
        project = self.projects.require(project_id)
        entry = self.registry.entry_for(project.task_id)
        if not entry.application_capability.candidate_excel_export:
            raise CandidateValidationError("Excel候補exportはこの予測タスクでは利用できません")
        return candidates_xlsx(
            self.store.list_candidates(project_id),
            self.resolver.runtime_for(project),
            task_id=project.task_id,
        )

    def get(self, project_id: str, candidate_id: str, *, include_archived: bool = False) -> Candidate:
        self.projects.require(project_id)
        candidate = self.store.get_candidate(candidate_id, project_id, include_archived=include_archived)
        if candidate is None:
            raise CandidateNotFoundError(candidate_id)
        return candidate

    def update(self, project_id: str, candidate_id: str, payload: CandidateUpdate) -> Candidate:
        project = self.projects.require(project_id)
        existing = self.store.get_candidate(candidate_id, project_id, include_archived=True)
        if existing is None:
            raise CandidateNotFoundError(candidate_id)
        if existing.archived_at is not None:
            raise CandidateArchivedError("archive済み候補は編集できません")
        candidate_input = CandidateInput.model_validate(payload.model_dump(exclude={"expected_revision"}))
        if existing.provenance != candidate_input.provenance:
            raise CandidateProvenanceImmutableError("候補の作成元は変更できません")
        self._validate(project.task_id, candidate_input)
        candidate = self.store.update_candidate(candidate_id, project_id, candidate_input, payload.expected_revision)
        if candidate is None:
            raise CandidateNotFoundError(candidate_id)
        return candidate

    def delete(self, project_id: str, candidate_id: str, expected_revision: int) -> None:
        self.projects.require(project_id)
        if self.store.get_candidate(candidate_id, project_id, include_archived=True) is None:
            raise CandidateNotFoundError(candidate_id)
        if not self.store.delete_candidate(candidate_id, project_id, expected_revision):
            raise CandidateNotFoundError(candidate_id)

    def at_revision(self, project_id: str, candidate_id: str, expected_revision: int) -> Candidate:
        candidate = self.get(project_id, candidate_id)
        if candidate.revision != expected_revision:
            raise CandidateRevisionConflictError(candidate)
        return candidate

    def _validate(self, task_id: str, payload: CandidateInput) -> None:
        try:
            self.registry.validate_candidate(task_id, payload)
        except (TaskRegistryError, ValueError) as exc:
            raise CandidateValidationError(str(exc)) from exc
