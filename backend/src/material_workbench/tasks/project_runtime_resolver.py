"""Resolve a Project's immutable data/profile/package references into a runtime."""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import hashlib
from pathlib import Path
from threading import RLock
from typing import Any

from material_workbench.data.dataset_profile import DatasetInputProfile, load_task_definitions, validate_profile
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.model_packages import (
    ModelPackageLoader,
    PackageContractError,
    VerifiedModelPackage,
)
from material_workbench.contracts.schemas import Project
from material_workbench.task_modules import PredictionRuntime
from material_workbench.tasks.task_registry import DataExplorerEntry, TaskRegistry, TaskRegistryError
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.persistence.workspace_catalog_bootstrap import (
    CANONICALIZATION_CONTRACT_DIGEST,
    CANONICAL_DATASET_CONTRACT_DIGEST,
    task_definition_digest,
)


class ProjectRuntimeResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectRuntimeIdentity:
    task_id: str
    runtime_type: str
    package_manifest_digest: str
    package_digest: str
    pipeline_digest: str
    support_digest: str


@dataclass(frozen=True)
class ResolvedProjectRuntime:
    # The prediction runtime is built against the package's declared training
    # dataset so model support never drifts with the Project reference data.
    runtime: PredictionRuntime
    context_runtime: PredictionRuntime
    data_explorer: DataExplorerEntry | None
    identity: ProjectRuntimeIdentity


class ProjectRuntimeResolver:
    def __init__(
        self,
        catalog: WorkspaceCatalog,
        registry: TaskRegistry,
        *,
        max_cache_entries: int = 32,
    ) -> None:
        if max_cache_entries < 1:
            raise ValueError("max_cache_entries must be positive")
        self.catalog = catalog
        self.registry = registry
        self.max_cache_entries = max_cache_entries
        self._cache: OrderedDict[tuple[str, str, str, str, str], ResolvedProjectRuntime] = OrderedDict()
        self._package_cache: OrderedDict[tuple[str, str], VerifiedModelPackage] = OrderedDict()
        self._lock = RLock()

    def resolve(self, project: Project) -> ResolvedProjectRuntime:
        self.registry.require_available(project.task_id)
        if not project.dataset_view_revision_id or not project.model_package_ref_id:
            raise ProjectRuntimeResolutionError("プロジェクトのData Library参照が固定されていません")
        key = (
            project.task_id,
            project.dataset_view_revision_id,
            project.task_contract_digest,
            project.model_package_ref_id,
            project.model_package_manifest_digest,
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
            resolved = self._build(project)
            self._cache[key] = resolved
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_cache_entries:
                self._cache.popitem(last=False)
            return resolved

    def runtime_for(self, project: Project) -> PredictionRuntime:
        return self.resolve(project).runtime

    def data_explorer_for(self, project: Project) -> DataExplorerEntry:
        explorer = self.resolve(project).data_explorer
        if explorer is None:
            raise TaskRegistryError(f"data explorer is not available for task: {project.task_id}")
        return explorer

    def context_runtime_for(self, project: Project) -> PredictionRuntime:
        return self.resolve(project).context_runtime

    def _build(self, project: Project) -> ResolvedProjectRuntime:
        expected_task_digest = task_definition_digest(self.registry, project.task_id)
        if project.task_contract_digest != expected_task_digest:
            raise ProjectRuntimeResolutionError("プロジェクトのPrediction Task契約digestが現在の定義と一致しません")

        view = self.catalog.get_dataset_view_revision(
            project.dataset_view_revision_id or "", include_archived=True
        )
        if view is None:
            raise ProjectRuntimeResolutionError("プロジェクトのDataset Viewが見つかりません")
        if view.kind != "single" or len(view.members) != 1:
            raise ProjectRuntimeResolutionError(
                "cohort比較ビューは比較Activity専用です。推論には単一Dataset Viewを選択してください"
            )
        application_path, application_profile, application_sha, application_profile_digest = self._dataset_resources(
            view.members[0].dataset_revision_id, project.task_id
        )

        package_ref = self.catalog.get_model_package_ref(
            project.model_package_ref_id or "", include_archived=True
        )
        if package_ref is None:
            raise ProjectRuntimeResolutionError("Model Package参照が見つかりません")
        if (
            package_ref.task_id != project.task_id
            or package_ref.task_contract_digest != expected_task_digest
            or package_ref.manifest_digest != project.model_package_manifest_digest
        ):
            raise ProjectRuntimeResolutionError("Model Package参照とプロジェクトの固定値が一致しません")
        package_key = (package_ref.id, package_ref.manifest_digest)
        package = self._package_cache.get(package_key)
        if package is None:
            try:
                package = ModelPackageLoader().load(package_ref.locator)
            except PackageContractError as exc:
                raise ProjectRuntimeResolutionError(f"Model Packageを検証できません: {exc}") from exc
            self._package_cache[package_key] = package
        self._package_cache.move_to_end(package_key)
        while len(self._package_cache) > self.max_cache_entries:
            self._package_cache.popitem(last=False)
        if package.manifest_sha256 != package_ref.manifest_digest:
            raise ProjectRuntimeResolutionError("Model Package manifestが登録時から変わっています")

        module = self.registry.module_for(project.task_id)
        training_data_id = package.manifest.provenance.training_data_id
        if not training_data_id.startswith("sha256:"):
            raise ProjectRuntimeResolutionError("Model Packageのtraining_data_id形式に対応していません")
        training_sha = training_data_id.removeprefix("sha256:")
        training_profile_digest = package.manifest.provenance.dataset_profile_id
        training_dataset_id = next((
            item.id
            for item in self.catalog.list_dataset_revisions(include_archived=True)
            if (candidate_asset := self.catalog.get_data_asset(item.data_asset_id, include_archived=True)) is not None
            and candidate_asset.sha256 == training_sha
            and (candidate_profile := self.catalog.get_profile_revision(
                item.profile_revision_id, include_archived=True
            )) is not None
            and (
                training_profile_digest is None
                or candidate_profile.profile_digest == training_profile_digest
            )
        ), None)
        if training_dataset_id is None:
            raise ProjectRuntimeResolutionError(
                "Model Packageの学習DatasetがData Libraryにありません。Packageのsupport provenanceを確認してください"
            )
        training_path, training_profile, _, resolved_training_profile_digest = self._dataset_resources(
            training_dataset_id, project.task_id
        )
        try:
            context_data = module.data_loader(application_path, application_profile)
            if (
                application_sha == training_sha
                and application_profile_digest == resolved_training_profile_digest
            ):
                training_data = context_data
            else:
                training_data = module.data_loader(training_path, training_profile)
            runtime = module.runtime_factory(training_data, package)
            self.registry.validate_application_runtime(project.task_id, runtime)
            context_runtime = (
                runtime
                if context_data is training_data
                else module.runtime_factory(context_data, package)
            )
            self.registry.validate_application_runtime(project.task_id, context_runtime)
        except (TaskRegistryError, ValueError, OSError) as exc:
            raise ProjectRuntimeResolutionError(f"プロジェクトruntimeを構築できません: {exc}") from exc
        explorer = (
            DataExplorerEntry(data=context_data, capability=module.data_explorer)
            if module.data_explorer is not None
            else None
        )
        pipeline_digest = self.registry._pipeline_digest(package)
        identity = ProjectRuntimeIdentity(
            task_id=project.task_id,
            runtime_type="+".join(sorted({item.runtime_type for item in package.manifest.predictors})),
            package_manifest_digest=project.model_package_manifest_digest.removeprefix("sha256:"),
            package_digest=f"sha256:{project.model_package_manifest_digest.removeprefix('sha256:')}",
            pipeline_digest=pipeline_digest,
            support_digest=semantic_digest({
                "dataset_view_revision_id": project.dataset_view_revision_id,
                "source_sha256": runtime.data.source_sha256,
                "pipeline_digest": pipeline_digest,
                "policy_id": runtime.support_policy_id,
            }),
        )
        return ResolvedProjectRuntime(
            runtime=runtime,
            context_runtime=context_runtime,
            data_explorer=explorer,
            identity=identity,
        )

    def _dataset_resources(
        self, dataset_revision_id: str, task_id: str
    ) -> tuple[Path, Any, str, str]:
        dataset = self.catalog.get_dataset_revision(dataset_revision_id, include_archived=True)
        if dataset is None:
            raise ProjectRuntimeResolutionError("Dataset Revisionが見つかりません")
        if dataset.canonicalization_contract_digest != CANONICALIZATION_CONTRACT_DIGEST:
            raise ProjectRuntimeResolutionError("Dataset Revisionの正規化契約に対応していません")
        asset = self.catalog.get_data_asset(dataset.data_asset_id, include_archived=True)
        profile_revision = self.catalog.get_profile_revision(
            dataset.profile_revision_id, include_archived=True
        )
        if asset is None or profile_revision is None:
            raise ProjectRuntimeResolutionError("Dataset Revisionの構成要素が見つかりません")
        if profile_revision.canonical_contract_digest != CANONICAL_DATASET_CONTRACT_DIGEST:
            raise ProjectRuntimeResolutionError("Profile Revisionのcanonical契約に対応していません")

        source_path = Path(asset.locator)
        try:
            with source_path.open("rb") as source:
                actual_sha = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError as exc:
            raise ProjectRuntimeResolutionError(f"Datasetを読み取れません: {source_path}") from exc
        if actual_sha != asset.sha256:
            raise ProjectRuntimeResolutionError("DatasetのExcel内容が登録時から変わっています")

        definitions = load_task_definitions()
        raw_profile = dict(profile_revision.effective_profile_json)
        if raw_profile.get("schema_version") == "tabular-dataset-profile/v1":
            from material_workbench.modeling.tabular_regression import TabularDatasetProfile

            try:
                profile = TabularDatasetProfile.model_validate(raw_profile)
            except ValueError as exc:
                raise ProjectRuntimeResolutionError("Profile Revisionを再構成できません") from exc
            if profile.task_id != task_id:
                raise ProjectRuntimeResolutionError("Profile RevisionはこのPrediction Taskに対応していません")
            return source_path, profile, asset.sha256, profile_revision.profile_digest
        raw_tasks = raw_profile.get("tasks")
        if not isinstance(raw_tasks, dict) or task_id not in raw_tasks:
            raise ProjectRuntimeResolutionError("Profile RevisionはこのPrediction Taskに対応していません")
        selected_definitions = {
            selected_task_id: definitions[selected_task_id]
            for selected_task_id in raw_tasks
            if selected_task_id in definitions
        }
        try:
            profile = DatasetInputProfile.model_validate({
                **raw_profile,
                "task_definitions": selected_definitions,
            })
            validate_profile(profile, selected_definitions)
        except ValueError as exc:
            raise ProjectRuntimeResolutionError("Profile Revisionを再構成できません") from exc
        return source_path, profile, asset.sha256, profile_revision.profile_digest
