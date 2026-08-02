from __future__ import annotations

from .projects import ProjectService
from decision_workbench.contracts.blend_contracts import (
    BlendMaterialDescriptor,
    BlendContractRegistry,
    BlendStructuralError,
    BlendValidationState,
    validate_sparse_blend,
)
from decision_workbench.domain.design_space_validation import (
    validate_candidate_in_design_space,
)
from decision_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateImportResponse,
    CandidateInput,
    CandidateUpdate,
    Project,
)
from decision_workbench.application.candidate_spreadsheet import (
    candidate_template_xlsx,
    candidates_xlsx,
    import_candidates_xlsx,
)
from decision_workbench.persistence.store import (
    CandidateArchivedError,
    CandidateLimitError,
    CandidateRevisionConflictError,
    ProjectNotFoundError,
    Store,
    StoreDataIntegrityError,
)
from decision_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from decision_workbench.modeling.missingness import require_runtime_operation_allowed


class CandidateNotFoundError(LookupError):
    pass


class CandidateValidationError(ValueError):
    pass


class CandidateProvenanceImmutableError(ValueError):
    pass


class CandidateProjectKindError(ValueError):
    pass


class CandidateService:
    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        resolver: ProjectRuntimeResolver,
        blend_contracts: BlendContractRegistry | None = None,
        transform_catalog: DeterministicTransformCatalog | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.resolver = resolver
        self.blend_contracts = blend_contracts or BlendContractRegistry()
        self.transform_catalog = transform_catalog
        self.projects = ProjectService(store, registry)

    def _require_single_project(self, project_id: str) -> Project:
        project = self.projects.require(project_id)
        if project.scientific_identity.identity_kind == "chain":
            raise CandidateProjectKindError(
                "Chain Projectの候補はChain候補APIを使用してください"
            )
        return project

    def list(self, project_id: str, *, include_archived: bool = False) -> list[Candidate]:
        self._require_single_project(project_id)
        return self.store.list_candidates(project_id, include_archived=include_archived)

    def create(self, project_id: str, payload: CandidateInput) -> Candidate:
        project = self._require_single_project(project_id)
        self.registry.require_available(project.task_id)
        if payload.provenance.source_kind == "copy":
            reference = payload.provenance.source_ref
            source_candidate = self.store.get_candidate_revision(
                reference.candidate_id,
                reference.candidate_revision,
                reference.project_id,
            )
            if source_candidate is None:
                current_source = self.store.get_candidate(
                    reference.candidate_id,
                    reference.project_id,
                    include_archived=True,
                )
                if current_source is None:
                    raise CandidateValidationError("コピー元候補が見つかりません")
                raise CandidateValidationError("コピー元候補のrevisionが一致しません")
            source_project = self._require_single_project(reference.project_id)
            if source_project.task_id != project.task_id:
                raise CandidateValidationError("異なる予測タスクの候補はコピーできません")
        prepared = self._prepare(
            project.task_id,
            payload,
            project.design_space,
        )
        return self.store.create_candidate(prepared, project_id)

    def import_xlsx(self, project_id: str, contents: bytes) -> CandidateImportResponse:
        project = self._require_single_project(project_id)
        entry = self.registry.entry_for(project.task_id)
        if not entry.application_capability.candidate_excel_import:
            raise CandidateValidationError("Excel候補importはこの予測タスクでは利用できません")
        runtime = self.resolver.runtime_for(project)
        payloads, errors = import_candidates_xlsx(
            contents,
            task_id=project.task_id,
            profile=getattr(runtime.data, "profile", None),
            validate_candidate=lambda payload: self._validate(
                project.task_id,
                payload,
                project.design_space,
            ),
        )
        created = self.store.create_candidates(payloads, project_id)
        return CandidateImportResponse(created=len(created), errors=errors, candidates=created)

    def export_xlsx(self, project_id: str) -> bytes:
        project = self._require_single_project(project_id)
        entry = self.registry.entry_for(project.task_id)
        if not entry.application_capability.candidate_excel_export:
            raise CandidateValidationError("Excel候補exportはこの予測タスクでは利用できません")
        runtime = self.resolver.runtime_for(project)
        candidates = self.store.list_candidates(project_id)
        for candidate in candidates:
            try:
                require_runtime_operation_allowed(
                    runtime,
                    candidate,
                    operation="export",
                )
            except ValueError as exc:
                raise CandidateValidationError(str(exc)) from exc
        return candidates_xlsx(
            candidates,
            runtime,
            task_id=project.task_id,
        )

    def template_xlsx(self, project_id: str) -> bytes:
        project = self._require_single_project(project_id)
        entry = self.registry.entry_for(project.task_id)
        if not entry.application_capability.candidate_excel_import:
            raise CandidateValidationError("Excel候補importはこの予測タスクでは利用できません")
        return candidate_template_xlsx(
            self.resolver.runtime_for(project),
            task_id=project.task_id,
        )

    def get(self, project_id: str, candidate_id: str, *, include_archived: bool = False) -> Candidate:
        self._require_single_project(project_id)
        candidate = self.store.get_candidate(candidate_id, project_id, include_archived=include_archived)
        if candidate is None:
            raise CandidateNotFoundError(candidate_id)
        return candidate

    def update(self, project_id: str, candidate_id: str, payload: CandidateUpdate) -> Candidate:
        project = self._require_single_project(project_id)
        self.registry.require_available(project.task_id)
        existing = self.store.get_candidate(candidate_id, project_id, include_archived=True)
        if existing is None:
            raise CandidateNotFoundError(candidate_id)
        if existing.archived_at is not None:
            raise CandidateArchivedError("archive済み候補は編集できません")
        candidate_input = CandidateInput.model_validate(payload.model_dump(exclude={"expected_revision"}))
        try:
            self.registry.candidate_family_for(
                project.task_id
            ).canonicalize_update(
                existing,
                candidate_input,
                self.registry.contract_for(project.task_id).task_definition,
            )
        except ValueError as exc:
            raise CandidateValidationError(str(exc)) from exc
        if existing.provenance != candidate_input.provenance:
            raise CandidateProvenanceImmutableError("候補の作成元は変更できません")
        if (
            existing.provenance.source_kind == "historical_observation"
            and existing.inputs != candidate_input.inputs
        ):
            raise CandidateProvenanceImmutableError(
                "過去の実測recordから作成した候補の入力条件は変更できません"
            )
        candidate_input = self._prepare(
            project.task_id,
            candidate_input,
            project.design_space,
        )
        candidate = self.store.update_candidate(candidate_id, project_id, candidate_input, payload.expected_revision)
        if candidate is None:
            raise CandidateNotFoundError(candidate_id)
        return candidate

    def delete(self, project_id: str, candidate_id: str, expected_revision: int) -> None:
        project = self._require_single_project(project_id)
        self.registry.require_available(project.task_id)
        if self.store.get_candidate(candidate_id, project_id, include_archived=True) is None:
            raise CandidateNotFoundError(candidate_id)
        if not self.store.delete_candidate(candidate_id, project_id, expected_revision):
            raise CandidateNotFoundError(candidate_id)

    def restore(self, project_id: str, candidate_id: str) -> Candidate:
        project = self._require_single_project(project_id)
        self.registry.require_available(project.task_id)
        candidate = self.store.restore_candidate(candidate_id, project_id)
        if candidate is None:
            raise CandidateNotFoundError(candidate_id)
        return candidate

    def at_revision(self, project_id: str, candidate_id: str, expected_revision: int) -> Candidate:
        current = self.get(project_id, candidate_id)
        if current.revision != expected_revision:
            raise CandidateRevisionConflictError(current)
        return current

    def historical_revision(
        self,
        project_id: str,
        candidate_id: str,
        revision: int,
    ) -> Candidate:
        self._require_single_project(project_id)
        candidate = self.store.get_candidate_revision(
            candidate_id,
            revision,
            project_id,
        )
        if candidate is None:
            raise CandidateNotFoundError(candidate_id)
        return candidate

    def blend_materials(
        self,
        project_id: str,
        candidate_id: str,
        revision: int | None = None,
    ) -> tuple[BlendMaterialDescriptor, ...]:
        candidate = (
            self.get(project_id, candidate_id)
            if revision is None
            else self.historical_revision(project_id, candidate_id, revision)
        )
        if candidate.blend is None:
            return ()
        try:
            try:
                return self.blend_contracts.describe(candidate.blend)
            except BlendStructuralError:
                if self.transform_catalog is None:
                    raise
                return self.transform_catalog.describe_blend(candidate.blend)
        except BlendStructuralError as exc:
            raise CandidateValidationError(str(exc)) from exc

    def derivation_chain(
        self,
        project_id: str,
        candidate_id: str,
    ) -> list[Candidate]:
        current = self.get(project_id, candidate_id, include_archived=True)
        chain: list[Candidate] = []
        visited = {(current.project_id, current.id, current.revision)}
        provenance = current.provenance
        while provenance.source_kind == "copy":
            reference = provenance.source_ref
            identity = (
                reference.project_id,
                reference.candidate_id,
                reference.candidate_revision,
            )
            if identity in visited:
                raise CandidateValidationError("候補の派生履歴が循環しています")
            visited.add(identity)
            source = self.store.get_candidate_revision(
                reference.candidate_id,
                reference.candidate_revision,
                reference.project_id,
            )
            if source is None:
                raise CandidateValidationError(
                    "派生元候補の指定revisionが見つかりません"
                )
            chain.append(source)
            provenance = source.provenance
            if len(chain) >= 100:
                raise CandidateValidationError("候補の派生履歴が長すぎます")
        return chain

    def _validate(
        self,
        task_id: str,
        payload: CandidateInput,
        design_space: DesignSpaceDefinition | None,
    ) -> None:
        try:
            self.registry.validate_candidate(task_id, payload)
            validate_candidate_in_design_space(payload, design_space)
        except (TaskRegistryError, ValueError) as exc:
            raise CandidateValidationError(str(exc)) from exc

    def _prepare(
        self,
        task_id: str,
        payload: CandidateInput,
        design_space: DesignSpaceDefinition | None,
    ) -> CandidateInput:
        """Server-authoritatively resolve structural refs and compute draft state."""
        if payload.blend is None:
            prepared = payload.model_copy(
                update={"blend_validation": BlendValidationState(status="not_applicable")}
            )
        else:
            if not self.registry.entry_for(task_id).application_capability.sparse_blend:
                raise CandidateValidationError(
                    "この予測タスクは疎な配合候補（sparse blend）に対応していません"
                )
            try:
                try:
                    contracts = self.blend_contracts.resolve(payload.blend)
                except BlendStructuralError:
                    if self.transform_catalog is None:
                        raise
                    contracts = self.transform_catalog.resolve_blend(payload.blend)
            except BlendStructuralError as exc:
                raise CandidateValidationError(str(exc)) from exc
            prepared = payload.model_copy(
                update={"blend_validation": validate_sparse_blend(payload.blend, contracts)}
            )
        self._validate(task_id, prepared, design_space)
        return prepared
