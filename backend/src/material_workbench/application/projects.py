from __future__ import annotations

from material_workbench.contracts.schemas import (
    ModelPackageRef,
    DatasetViewMember,
    Project,
    ProjectCreateInput,
    ProjectDecisionInput,
    ProjectGroupMoveInput,
    ProjectHistoryResponse,
    ProjectInput,
    TargetRange,
    ProjectUpdateInput,
)
from material_workbench.data.profile_document import supported_task_ids
from material_workbench.persistence.store import (
    CandidateCopyConflictError,
    ChainCatalogConflictError,
    InvalidProjectDecisionError,
    ProjectNotFoundError,
    ProjectGroupConflictError,
    ProjectGroupUnavailableError,
    Store,
    StoreDataIntegrityError,
)
from material_workbench.contracts.task_contracts import OutputDefinition, TaskContractFixture
from material_workbench.contracts.design_space_contracts import default_design_space
from material_workbench.contracts.objective_contracts import (
    ObjectiveDefinitionRevision,
    objective_from_project_targets,
)
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

    def require(
        self, project_id: str, *, include_archived: bool = False
    ) -> Project:
        project = self.store.get_project(
            project_id, include_archived=include_archived
        )
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project

    def list(self, *, include_archived: bool = False) -> list[Project]:
        return self.store.list_projects(include_archived=include_archived)

    def objective_revisions(
        self,
        project_id: str,
    ) -> list[ObjectiveDefinitionRevision]:
        self.require(project_id)
        return self.store.list_project_objective_revisions(project_id)

    def create(self, payload: ProjectCreateInput) -> Project:
        if (
            payload.scientific_identity is not None
            and payload.scientific_identity.identity_kind == "chain"
        ):
            return self._create_chain_project(payload)
        if payload.scientific_identity is not None:
            identity = payload.scientific_identity
            if identity.binding_provenance != "explicit":
                raise ProjectValidationError(
                    "新しいsingle-Task Projectには明示的な固定参照が必要です"
                )
            payload = payload.model_copy(
                update={
                    "task_id": identity.task_id,
                    "dataset_view_revision_id": identity.dataset_view_revision_id,
                    "task_contract_digest": identity.task_contract_digest or "",
                    "model_package_ref_id": identity.model_package_ref_id,
                    "model_package_manifest_digest": (
                        identity.model_package_manifest_digest or ""
                    ),
                    "scientific_identity": None,
                }
            )
        self.registry.require_available(payload.task_id)
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
        except (CandidateCopyConflictError, ProjectGroupUnavailableError) as exc:
            raise ProjectValidationError(str(exc)) from exc

    def archive(self, project_id: str) -> Project:
        self.require(project_id, include_archived=True)
        archived = self.store.archive_project(project_id)
        if archived is None:
            raise ProjectNotFoundError(project_id)
        return archived

    def restore(self, project_id: str) -> Project:
        self.require(project_id, include_archived=True)
        restored = self.store.restore_project(project_id)
        if restored is None:
            raise ProjectNotFoundError(project_id)
        return restored

    def purge(self, project_id: str) -> None:
        self.require(project_id, include_archived=True)
        if not self.store.purge_project(project_id):
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
        task_id = self._terminal_task_id(current)
        self.registry.require_available(task_id)
        frozen = {
            "task_id": current.task_id,
            "dataset_view_revision_id": current.dataset_view_revision_id,
            "task_contract_digest": current.task_contract_digest,
            "model_package_ref_id": current.model_package_ref_id,
            "model_package_manifest_digest": current.model_package_manifest_digest,
            "project_series_id": current.project_series_id,
            "predecessor_project_id": current.predecessor_project_id,
            "scientific_identity": current.scientific_identity,
            "design_space_digest": current.design_space_digest,
            "objective_definition_digest": current.objective_definition_digest,
        }
        changed = [
            key for key, expected in frozen.items()
            if (provided := getattr(payload, key)) is not None and provided != expected
        ]
        if changed:
            raise ProjectTaskLockedError(
                "プロジェクトの固定参照は変更できません。『このプロジェクトの続き』として新規作成してください"
            )
        contract = self._contract(task_id)
        self._validate_targets(payload, contract.task_definition.outputs)
        self._validate_display_decimals(payload, task_id)
        objective = current.objective_definition
        objective_provenance = current.objective_binding_provenance
        if (
            current.scientific_identity.identity_kind == "single_task"
            and payload.target_values != current.target_values
        ):
            try:
                objective = objective_from_project_targets(
                    task=contract.task_definition,
                    task_contract_digest=current.task_contract_digest,
                    target_values=payload.target_values,
                )
            except ValueError as exc:
                raise ProjectValidationError(str(exc)) from exc
            if objective is not None:
                if current.objective_definition is not None:
                    objective = objective.model_copy(
                        update={
                            "objective_id": current.objective_definition.objective_id,
                            "revision": current.objective_definition.revision + 1,
                        }
                    )
                try:
                    objective.validate_against(
                        contract.task_definition,
                        contract.runtime_capability,
                    )
                except ValueError as exc:
                    raise ProjectValidationError(str(exc)) from exc
                objective_provenance = "updated_revision"
            else:
                objective_provenance = "none_configured"
        try:
            project = self.store.update_project(
                project_id,
                payload,
                objective_definition=objective,
                objective_binding_provenance=objective_provenance,
            )
        except InvalidProjectDecisionError as exc:
            raise ProjectValidationError(str(exc)) from exc
        if project is None:
            raise ProjectNotFoundError(project_id)
        return project

    def update_decision(self, project_id: str, payload: ProjectDecisionInput) -> Project:
        current = self.require(project_id)
        self.registry.require_available(self._terminal_task_id(current))
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
        current = self.require(project_id)
        self.registry.require_available(self._terminal_task_id(current))
        try:
            return self.store.move_project_to_group(project_id, payload)
        except ProjectGroupUnavailableError as exc:
            raise ProjectValidationError(str(exc)) from exc

    def _contract(self, task_id: str) -> TaskContractFixture:
        try:
            return self.registry.contract_for(task_id)
        except TaskRegistryError as exc:
            raise ProjectValidationError(str(exc)) from exc

    def _terminal_task_id(self, project: Project) -> str:
        identity = project.scientific_identity
        if identity.identity_kind == "single_task":
            return identity.task_id
        revision = self.store.get_chain_revision(identity.chain_revision_id)
        if revision is None or revision.revision_digest != identity.chain_revision_digest:
            raise ProjectValidationError(
                "プロジェクトに固定されたChain Revisionを読み込めません"
            )
        task_stages = [
            stage for stage in revision.stages if stage.stage_kind == "task"
        ]
        if not task_stages:
            raise ProjectValidationError("Chainに予測Taskがありません")
        return task_stages[-1].contract_id

    def _create_chain_project(self, payload: ProjectCreateInput) -> Project:
        identity = payload.scientific_identity
        assert identity is not None
        revision = self.store.get_chain_revision(identity.chain_revision_id)
        if revision is None or revision.revision_digest != identity.chain_revision_digest:
            raise ProjectValidationError(
                "選択したChain RevisionのIDまたはdigestが登録内容と一致しません"
            )
        task_stages = [
            stage for stage in revision.stages if stage.stage_kind == "task"
        ]
        if not task_stages:
            raise ProjectValidationError("Chainに予測Taskがありません")
        terminal_task_id = task_stages[-1].contract_id
        self.registry.require_available(terminal_task_id)
        contract = self._contract(terminal_task_id)
        self._validate_targets(payload, contract.task_definition.outputs)
        self._validate_display_decimals(payload, terminal_task_id)
        if payload.decision_candidate_id:
            raise ProjectValidationError("新しいプロジェクトでは採用候補を空にしてください")
        if (
            payload.initial_candidate is not None
            and payload.initial_candidate.provenance.source_kind != "copy"
        ):
            raise ProjectValidationError(
                "Chain Projectの初期候補は同じChain Revisionのコピー由来にしてください"
            )
        resolved = self._validate_project_series_selection(payload).model_copy(
            update={
                "task_id": "",
                "dataset_view_revision_id": None,
                "task_contract_digest": "",
                "model_package_ref_id": None,
                "model_package_manifest_digest": "",
            }
        )
        try:
            return self.store.create_chain_project(
                resolved,
                identity,
                payload.initial_candidate,
            )
        except (
            CandidateCopyConflictError,
            ChainCatalogConflictError,
            ProjectGroupUnavailableError,
        ) as exc:
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
        contract = self._contract(payload.task_id)
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
            and self._package_trained_on_dataset(item, view.members[0])
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

        space = payload.design_space
        provenance = "explicit"
        if space is None and payload.predecessor_project_id:
            predecessor = self.require(payload.predecessor_project_id)
            if (
                predecessor.scientific_identity.identity_kind == "single_task"
                and predecessor.task_id == payload.task_id
                and predecessor.design_space is not None
            ):
                space = predecessor.design_space
                provenance = "inherited_predecessor"
        if space is None:
            space = default_design_space(
                contract.task_definition,
                task_contract_digest=task_digest,
            )
            provenance = "generated_default"
        try:
            space.validate_against(contract.task_definition)
        except ValueError as exc:
            raise ProjectValidationError(str(exc)) from exc

        objective = payload.objective_definition
        objective_provenance = "explicit"
        if objective is None and payload.predecessor_project_id:
            predecessor = self.require(payload.predecessor_project_id)
            if (
                predecessor.scientific_identity.identity_kind == "single_task"
                and predecessor.task_id == payload.task_id
                and predecessor.objective_definition is not None
            ):
                objective = predecessor.objective_definition
                objective_provenance = "inherited_predecessor"
        if objective is None:
            try:
                objective = objective_from_project_targets(
                    task=contract.task_definition,
                    task_contract_digest=task_digest,
                    target_values=payload.target_values,
                )
            except ValueError as exc:
                raise ProjectValidationError(str(exc)) from exc
            objective_provenance = (
                "generated_default" if objective is not None else "none_configured"
            )
        if objective is not None:
            try:
                objective.validate_against(
                    contract.task_definition,
                    contract.runtime_capability,
                )
            except ValueError as exc:
                raise ProjectValidationError(str(exc)) from exc

        payload = self._validate_project_series_selection(payload)
        return payload.model_copy(update={
            "dataset_view_revision_id": view.id,
            "task_contract_digest": task_digest,
            "model_package_ref_id": package.id,
            "model_package_manifest_digest": package.manifest_digest,
            "design_space": space,
            "design_space_binding_provenance": provenance,
            "objective_definition": objective,
            "objective_binding_provenance": objective_provenance,
        })

    def _validate_project_series_selection(
        self, payload: ProjectCreateInput
    ) -> ProjectCreateInput:
        if self.catalog is None:
            raise ProjectValidationError("Data Libraryを利用できません")
        if payload.predecessor_project_id:
            self.require(payload.predecessor_project_id)
        if payload.project_series_id:
            if self.catalog.get_project_series(payload.project_series_id) is None:
                raise ProjectValidationError("選択した検討グループが見つかりません")
        return payload

    def _profile_supports_task(self, dataset_revision_id: str, task_id: str) -> bool:
        dataset = self.catalog.get_dataset_revision(dataset_revision_id)
        if dataset is None:
            return False
        profile = self.catalog.get_profile_revision(dataset.profile_revision_id)
        if profile is None:
            return False
        return task_id in supported_task_ids(profile.effective_profile_json)

    def _package_trained_on_dataset(
        self, package: ModelPackageRef, member: DatasetViewMember
    ) -> bool:
        if self.catalog is None:
            return False
        dataset = self.catalog.get_dataset_revision(member.dataset_revision_id)
        if dataset is None:
            return False
        asset = self.catalog.get_data_asset(dataset.data_asset_id)
        profile = self.catalog.get_profile_revision(dataset.profile_revision_id)
        provenance = package.manifest_json.get("provenance")
        if asset is None or profile is None or not isinstance(provenance, dict):
            return False
        if not (
            provenance.get("training_data_id") == f"sha256:{asset.sha256}"
            and provenance.get("dataset_profile_id") == profile.profile_digest
        ):
            return False
        source_lifecycle = provenance.get("source_lifecycle")
        if source_lifecycle is None:
            return True
        return (
            member.provenance_json.get("source_lifecycle")
            == source_lifecycle
        )
