from __future__ import annotations

from material_workbench.contracts.schemas import (
    ModelPackageRef,
    Project,
    ProjectCreateInput,
    ProjectDecisionInput,
    ProjectGroupMoveInput,
    ProjectHistoryResponse,
    ProjectInput,
    ProjectSeriesCreateInput,
    TargetRange,
    ProjectUpdateInput,
)
from material_workbench.data.profile_document import supported_task_ids
from material_workbench.persistence.store import (
    CandidateCopyConflictError,
    InvalidProjectDecisionError,
    ProjectNotFoundError,
    ProjectGroupConflictError,
    ProjectGroupUnavailableError,
    Store,
    StoreDataIntegrityError,
)
from material_workbench.contracts.task_contracts import OutputDefinition, TaskContractFixture
from material_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.persistence.workspace_catalog_bootstrap import task_definition_digest


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
                "プロジェクトの固定参照は変更できません。『このプロジェクトの続き』として新規作成してください"
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

    def move_to_group(self, project_id: str, payload: ProjectGroupMoveInput) -> Project:
        try:
            return self.store.move_project_to_group(project_id, payload)
        except ProjectGroupUnavailableError as exc:
            raise ProjectValidationError(str(exc)) from exc

    def _contract(self, task_id: str) -> TaskContractFixture:
        try:
            return self.registry.contract_for(task_id)
        except TaskRegistryError as exc:
            raise ProjectValidationError(str(exc)) from exc

    def _validate_targets(self, payload: ProjectInput | ProjectUpdateInput, outputs: tuple[OutputDefinition, ...]) -> None:
        unsupported = sorted(set(payload.target_values) - {item.key for item in outputs})
        if unsupported:
            raise ProjectValidationError(f"タスクに存在しない目標特性です: {', '.join(unsupported)}")
        output_by_key = {item.key: item for item in outputs}
        scalar_target_outputs = [
            key
            for key, goal in payload.target_values.items()
            if output_by_key[key].goal_direction == "target" and not isinstance(goal, TargetRange)
        ]
        if scalar_target_outputs:
            raise ProjectValidationError(
                f"方向のない目標特性は下限・上限を指定してください: {', '.join(sorted(scalar_target_outputs))}"
            )

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
            and self._package_trained_on_dataset(item, view.members[0].dataset_revision_id)
        ]
        if payload.model_package_ref_id:
            package = self.catalog.get_model_package_ref(payload.model_package_ref_id)
            if package is None:
                raise ProjectValidationError("選択したModel Packageが見つかりません")
            if package.id not in {item.id for item in compatible_packages}:
                raise ProjectValidationError(
                    "Model Packageは選択したDataset・Profile・Prediction Taskを学習元としていません"
                )
        elif len(compatible_packages) == 1:
            package = compatible_packages[0]
        else:
            raise ProjectValidationError("Prediction Taskに使うModel Packageを選択してください")
        if payload.model_package_manifest_digest and payload.model_package_manifest_digest != package.manifest_digest:
            raise ProjectValidationError("Model Packageのmanifest digestが登録内容と一致しません")

        predecessor = None
        if payload.predecessor_project_id:
            predecessor = self.require(payload.predecessor_project_id)
        series_id = payload.project_series_id
        if series_id:
            series = self.catalog.get_project_series(series_id)
            if series is None:
                raise ProjectValidationError("選択した検討グループが見つかりません")
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
        return task_id in supported_task_ids(profile.effective_profile_json)

    def _package_trained_on_dataset(
        self, package: ModelPackageRef, dataset_revision_id: str
    ) -> bool:
        if self.catalog is None:
            return False
        dataset = self.catalog.get_dataset_revision(dataset_revision_id)
        if dataset is None:
            return False
        asset = self.catalog.get_data_asset(dataset.data_asset_id)
        profile = self.catalog.get_profile_revision(dataset.profile_revision_id)
        provenance = package.manifest_json.get("provenance")
        if asset is None or profile is None or not isinstance(provenance, dict):
            return False
        return (
            provenance.get("training_data_id") == f"sha256:{asset.sha256}"
            and provenance.get("dataset_profile_id") == profile.profile_digest
        )
