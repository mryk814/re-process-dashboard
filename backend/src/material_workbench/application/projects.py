from __future__ import annotations

from ..schemas import Project, ProjectCreateInput, ProjectDecisionInput, ProjectHistoryResponse, ProjectInput
from ..store import (
    CandidateCopyConflictError,
    InvalidProjectDecisionError,
    ProjectNotFoundError,
    Store,
    StoreDataIntegrityError,
)
from ..task_contracts import OutputDefinition, TaskContractFixture
from ..task_registry import TaskRegistry, TaskRegistryError


class ProjectValidationError(ValueError):
    pass


class ProjectTaskLockedError(ValueError):
    pass


class ProjectHistoryIntegrityError(RuntimeError):
    pass


class ProjectService:
    def __init__(self, store: Store, registry: TaskRegistry) -> None:
        self.store = store
        self.registry = registry

    def require(self, project_id: str) -> Project:
        project = self.store.get_project(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project

    def list(self) -> list[Project]:
        return self.store.list_projects()

    def create(self, payload: ProjectCreateInput) -> Project:
        contract = self._contract(payload.task_id)
        self._validate_targets(payload, contract.task_definition.outputs)
        self._validate_display_decimals(payload)
        if payload.decision_candidate_id:
            raise ProjectValidationError("新しいプロジェクトでは採用候補を空にしてください")
        initial = payload.initial_candidate
        if initial is not None:
            if initial.provenance.source_kind != "copy":
                raise ProjectValidationError("新規プロジェクトの初期候補はコピー由来にしてください")
            reference = initial.provenance.source_ref
            source = self.store.get_candidate(reference.candidate_id, reference.project_id, include_archived=True)
            if source is None or source.revision != reference.candidate_revision:
                raise ProjectValidationError("コピー元候補またはrevisionが一致しません")
            source_project = self.require(reference.project_id)
            if source_project.task_id != payload.task_id:
                raise ProjectValidationError("異なる予測タスクの候補はコピーできません")
            try:
                self.registry.validate_candidate(payload.task_id, initial)
            except (TaskRegistryError, ValueError) as exc:
                raise ProjectValidationError(str(exc)) from exc
        project_input = ProjectInput.model_validate(payload.model_dump(exclude={"initial_candidate"}))
        try:
            return self.store.create_project(project_input, initial)
        except CandidateCopyConflictError as exc:
            raise ProjectValidationError(str(exc)) from exc

    def delete(self, project_id: str) -> None:
        self.require(project_id)
        if not self.store.delete_project(project_id):
            raise ProjectNotFoundError(project_id)

    def history(self, project_id: str) -> ProjectHistoryResponse:
        try:
            history = self.store.project_history(project_id)
            if history is None:
                raise ProjectNotFoundError(project_id)
            return ProjectHistoryResponse.model_validate(history)
        except StoreDataIntegrityError as exc:
            raise ProjectHistoryIntegrityError(str(exc)) from exc
        except ValueError as exc:
            raise ProjectHistoryIntegrityError("プロジェクト履歴の形式が不正です") from exc

    def update(self, project_id: str, payload: ProjectInput) -> Project:
        current = self.require(project_id)
        contract = self._contract(payload.task_id)
        self._validate_targets(payload, contract.task_definition.outputs)
        self._validate_display_decimals(payload)
        if current.task_id != payload.task_id and self.store.list_candidates(project_id, include_archived=True):
            raise ProjectTaskLockedError("候補があるプロジェクトの予測タスクは変更できません")
        try:
            project = self.store.update_project(project_id, payload)
        except InvalidProjectDecisionError as exc:
            raise ProjectValidationError(str(exc)) from exc
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project

    def update_decision(self, project_id: str, payload: ProjectDecisionInput) -> Project:
        try:
            project = self.store.update_project_decision(
                project_id,
                payload.candidate_id,
                payload.snapshot_id,
                payload.note,
            )
        except InvalidProjectDecisionError as exc:
            raise ProjectValidationError(str(exc)) from exc
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project

    def _contract(self, task_id: str) -> TaskContractFixture:
        try:
            return self.registry.contract_for(task_id)
        except TaskRegistryError as exc:
            raise ProjectValidationError(str(exc)) from exc

    def _validate_targets(self, payload: ProjectInput, outputs: tuple[OutputDefinition, ...]) -> None:
        unsupported = sorted(set(payload.target_values) - {item.key for item in outputs})
        if unsupported:
            raise ProjectValidationError(f"タスクに存在しない目標特性です: {', '.join(unsupported)}")

    def _validate_display_decimals(self, payload: ProjectInput) -> None:
        definition = self.registry.resolved_definition_for(payload.task_id)
        unsupported = sorted(set(payload.display_decimals) - set(definition.task_definition.display_decimals))
        if unsupported:
            raise ProjectValidationError(f"タスクに存在しない表示項目です: {', '.join(unsupported)}")
