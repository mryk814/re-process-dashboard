from __future__ import annotations

from ..schemas import (
    Project,
    ProjectCreateInput,
    ProjectDecisionInput,
    ProjectHistoryResponse,
    ProjectInput,
    ProjectSeriesCreateInput,
    ProjectUpdateInput,
)
from ..store import (
    CandidateCopyConflictError,
    InvalidProjectDecisionError,
    ProjectNotFoundError,
    Store,
    StoreDataIntegrityError,
)
from ..task_contracts import OutputDefinition, TaskContractFixture
from ..task_registry import TaskRegistry, TaskRegistryError
from ..workspace_catalog import WorkspaceCatalog
from ..workspace_catalog_bootstrap import task_definition_digest


class ProjectValidationError(ValueError):
    pass


class ProjectTaskLockedError(ValueError):
    pass


class ProjectHistoryIntegrityError(RuntimeError):
    pass


class ProjectService:
    def __init__(
        self, store: Store, registry: TaskRegistry, catalog: WorkspaceCatalog | None = None
    ) -> None:
        self.store = store
        self.registry = registry
        self.catalog = catalog

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
        project_input = self._resolve_bindings(payload)
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

    def update(self, project_id: str, payload: ProjectUpdateInput) -> Project:
        current = self.require(project_id)
        frozen = {
            "task_id": current.task_id,
            "dataset_view_revision_id": current.dataset_view_revision_id,
            "task_contract_digest": current.task_contract_digest,
            "model_package_ref_id": current.model_package_ref_id,
            "model_package_manifest_digest": current.model_package_manifest_digest,
            "project_series_id": current.project_series_id,
            "predecessor_project_id": current.predecessor_project_id,
        }
        changed = [
            key for key, expected in frozen.items()
            if (provided := getattr(payload, key)) is not None and provided != expected
        ]
        if changed:
            raise ProjectTaskLockedError(
                "プロジェクトの固定参照は変更できません。『この検討の続き』として新規作成してください"
            )
        contract = self._contract(current.task_id)
        self._validate_targets(payload, contract.task_definition.outputs)
        self._validate_display_decimals(payload, current.task_id)
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

    def _validate_targets(self, payload: ProjectInput | ProjectUpdateInput, outputs: tuple[OutputDefinition, ...]) -> None:
        unsupported = sorted(set(payload.target_values) - {item.key for item in outputs})
        if unsupported:
            raise ProjectValidationError(f"タスクに存在しない目標特性です: {', '.join(unsupported)}")

    def _validate_display_decimals(
        self, payload: ProjectInput | ProjectUpdateInput, task_id: str | None = None
    ) -> None:
        definition = self.registry.resolved_definition_for(task_id or payload.task_id)
        unsupported = sorted(set(payload.display_decimals) - set(definition.task_definition.display_decimals))
        if unsupported:
            raise ProjectValidationError(f"タスクに存在しない表示項目です: {', '.join(unsupported)}")

    def _resolve_bindings(self, payload: ProjectCreateInput) -> ProjectCreateInput:
        if self.catalog is None:
            raise ProjectValidationError("Data Libraryを利用できません")
        task_digest = task_definition_digest(self.registry, payload.task_id)
        if payload.task_contract_digest and payload.task_contract_digest != task_digest:
            raise ProjectValidationError("Prediction Taskの契約digestが現在の定義と一致しません")

        compatible_views = [
            view for view in self.catalog.list_dataset_view_revisions()
            if view.kind == "single"
            and all(self._profile_supports_task(member.dataset_revision_id, payload.task_id) for member in view.members)
        ]
        if payload.dataset_view_revision_id:
            view = self.catalog.get_dataset_view_revision(payload.dataset_view_revision_id)
            if view is None:
                raise ProjectValidationError("選択したDataset Viewが見つかりません")
            if view.kind != "single":
                raise ProjectValidationError(
                    "複数Datasetの比較セットはProjectの参照データにできません。Data Libraryの比較Activityで利用してください"
                )
            if view.id not in {item.id for item in compatible_views}:
                raise ProjectValidationError("Dataset ViewのProfileはこのPrediction Taskに対応していません")
        elif len(compatible_views) == 1:
            view = compatible_views[0]
        else:
            raise ProjectValidationError("Prediction Taskに使うDataset Viewを選択してください")

        compatible_packages = [
            item for item in self.catalog.list_model_package_refs()
            if item.task_id == payload.task_id and item.task_contract_digest == task_digest
        ]
        if payload.model_package_ref_id:
            package = self.catalog.get_model_package_ref(payload.model_package_ref_id)
            if package is None:
                raise ProjectValidationError("選択したModel Packageが見つかりません")
            if package.id not in {item.id for item in compatible_packages}:
                raise ProjectValidationError("Model PackageはこのPrediction Taskと互換ではありません")
        elif len(compatible_packages) == 1:
            package = compatible_packages[0]
        else:
            raise ProjectValidationError("Prediction Taskに使うModel Packageを選択してください")
        if payload.model_package_manifest_digest and payload.model_package_manifest_digest != package.manifest_digest:
            raise ProjectValidationError("Model Packageのmanifest digestが登録内容と一致しません")

        predecessor = None
        if payload.predecessor_project_id:
            predecessor = self.require(payload.predecessor_project_id)
        series_id = payload.project_series_id or (predecessor.project_series_id if predecessor else None)
        if series_id:
            series = self.catalog.get_project_series(series_id)
            if series is None:
                raise ProjectValidationError("選択した一連の検討が見つかりません")
            if predecessor and predecessor.project_series_id != series.id:
                raise ProjectValidationError("継続元と異なる一連の検討には接続できません")
        else:
            series = self.catalog.create_project_series(
                ProjectSeriesCreateInput(name=payload.name, description=payload.description)
            )

        return payload.model_copy(update={
            "dataset_view_revision_id": view.id,
            "task_contract_digest": task_digest,
            "model_package_ref_id": package.id,
            "model_package_manifest_digest": package.manifest_digest,
            "project_series_id": series.id,
        })

    def _profile_supports_task(self, dataset_revision_id: str, task_id: str) -> bool:
        dataset = self.catalog.get_dataset_revision(dataset_revision_id)
        if dataset is None:
            return False
        profile = self.catalog.get_profile_revision(dataset.profile_revision_id)
        if profile is None:
            return False
        tasks = profile.effective_profile_json.get("tasks")
        return isinstance(tasks, dict) and task_id in tasks
