"""Resolve a Project's immutable data/profile/package references into a runtime."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from threading import RLock

from .dataset_profile import DatasetInputProfile, load_task_definitions, validate_profile
from .model_packages import ModelPackageLoader, PackageContractError
from .schemas import Project
from .task_modules import PredictionRuntime
from .task_registry import DataExplorerEntry, TaskRegistry, TaskRegistryError
from .workspace_catalog import WorkspaceCatalog
from .workspace_catalog_bootstrap import (
    CANONICALIZATION_CONTRACT_DIGEST,
    CANONICAL_DATASET_CONTRACT_DIGEST,
    task_definition_digest,
)


class ProjectRuntimeResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedProjectRuntime:
    runtime: PredictionRuntime
    data_explorer: DataExplorerEntry | None


class ProjectRuntimeResolver:
    def __init__(self, catalog: WorkspaceCatalog, registry: TaskRegistry) -> None:
        self.catalog = catalog
        self.registry = registry
        self._cache: dict[tuple[str, str, str], ResolvedProjectRuntime] = {}
        self._lock = RLock()

    def resolve(self, project: Project) -> ResolvedProjectRuntime:
        if not project.dataset_view_revision_id or not project.model_package_ref_id:
            raise ProjectRuntimeResolutionError("プロジェクトのData Library参照が固定されていません")
        key = (
            project.dataset_view_revision_id,
            project.task_contract_digest,
            project.model_package_manifest_digest,
        )
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            resolved = self._build(project)
            self._cache[key] = resolved
            return resolved

    def runtime_for(self, project: Project) -> PredictionRuntime:
        return self.resolve(project).runtime

    def data_explorer_for(self, project: Project) -> DataExplorerEntry:
        explorer = self.resolve(project).data_explorer
        if explorer is None:
            raise TaskRegistryError(f"data explorer is not available for task: {project.task_id}")
        return explorer

    def _build(self, project: Project) -> ResolvedProjectRuntime:
        expected_task_digest = task_definition_digest(self.registry, project.task_id)
        if project.task_contract_digest != expected_task_digest:
            raise ProjectRuntimeResolutionError("プロジェクトのPrediction Task契約digestが現在の定義と一致しません")

        view = self.catalog.get_dataset_view_revision(project.dataset_view_revision_id or "")
        if view is None:
            raise ProjectRuntimeResolutionError("プロジェクトのDataset Viewが見つかりません")
        if view.kind != "single" or len(view.members) != 1:
            raise ProjectRuntimeResolutionError(
                "cohort比較ビューは比較Activity専用です。推論には単一Dataset Viewを選択してください"
            )
        dataset = self.catalog.get_dataset_revision(view.members[0].dataset_revision_id)
        if dataset is None:
            raise ProjectRuntimeResolutionError("Dataset Revisionが見つかりません")
        if dataset.canonicalization_contract_digest != CANONICALIZATION_CONTRACT_DIGEST:
            raise ProjectRuntimeResolutionError("Dataset Revisionの正規化契約に対応していません")
        asset = self.catalog.get_data_asset(dataset.data_asset_id)
        profile_revision = self.catalog.get_profile_revision(dataset.profile_revision_id)
        if asset is None or profile_revision is None:
            raise ProjectRuntimeResolutionError("Dataset Revisionの構成要素が見つかりません")
        if profile_revision.canonical_contract_digest != CANONICAL_DATASET_CONTRACT_DIGEST:
            raise ProjectRuntimeResolutionError("Profile Revisionのcanonical契約に対応していません")

        source_path = Path(asset.locator)
        try:
            with source_path.open("rb") as source:
                actual_sha = hashlib.file_digest(source, "sha256").hexdigest()
        except OSError as exc:
            raise ProjectRuntimeResolutionError(f"DatasetのExcelを読み取れません: {source_path}") from exc
        if actual_sha != asset.sha256:
            raise ProjectRuntimeResolutionError("DatasetのExcel内容が登録時から変わっています")

        definitions = load_task_definitions()
        raw_profile = dict(profile_revision.effective_profile_json)
        raw_tasks = raw_profile.get("tasks")
        if not isinstance(raw_tasks, dict) or project.task_id not in raw_tasks:
            raise ProjectRuntimeResolutionError("Profile RevisionはこのPrediction Taskに対応していません")
        selected_definitions = {
            task_id: definitions[task_id]
            for task_id in raw_tasks
            if task_id in definitions
        }
        try:
            profile = DatasetInputProfile.model_validate({
                **raw_profile,
                "task_definitions": selected_definitions,
            })
            validate_profile(profile, selected_definitions)
        except ValueError as exc:
            raise ProjectRuntimeResolutionError("Profile Revisionを再構成できません") from exc

        package_ref = self.catalog.get_model_package_ref(project.model_package_ref_id or "")
        if package_ref is None:
            raise ProjectRuntimeResolutionError("Model Package参照が見つかりません")
        if (
            package_ref.task_id != project.task_id
            or package_ref.task_contract_digest != expected_task_digest
            or package_ref.manifest_digest != project.model_package_manifest_digest
        ):
            raise ProjectRuntimeResolutionError("Model Package参照とプロジェクトの固定値が一致しません")
        try:
            package = ModelPackageLoader().load(package_ref.locator)
        except PackageContractError as exc:
            raise ProjectRuntimeResolutionError(f"Model Packageを検証できません: {exc}") from exc
        if package.manifest_sha256 != package_ref.manifest_digest:
            raise ProjectRuntimeResolutionError("Model Package manifestが登録時から変わっています")

        module = self.registry.module_for(project.task_id)
        try:
            data = module.data_loader(source_path, profile)
            runtime = module.runtime_factory(data, Path(package_ref.locator))
            self.registry.validate_application_runtime(project.task_id, runtime)
        except (TaskRegistryError, ValueError, OSError) as exc:
            raise ProjectRuntimeResolutionError(f"プロジェクトruntimeを構築できません: {exc}") from exc
        explorer = (
            DataExplorerEntry(data=data, capability=module.data_explorer)
            if module.data_explorer is not None
            else None
        )
        return ResolvedProjectRuntime(runtime=runtime, data_explorer=explorer)
