"""Workspace-level read model for Task, Package, Transform, and Graph assets."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from decision_workbench.application.chains import (
    ChainValidationError,
    resolve_task_stage_lock,
    resolve_task_stage_surface,
)
from decision_workbench.contracts.chain_contracts import (
    ChainDefinition,
    ChainRevision,
    GraphDefinitionRef,
    GraphRevisionRef,
    PredictionGraphDefinition,
    PredictionGraphRevision,
    project_prediction_graph,
)
from decision_workbench.contracts.data_library_contracts import ModelPackageRef
from decision_workbench.contracts.feature_recipe_contracts import FeatureRecipe
from decision_workbench.contracts.model_library_contracts import (
    ModelAssetState,
    ModelLibraryCatalog,
    ModelLibraryDataReference,
    ModelLibraryGraphAsset,
    ModelLibraryGraphDefinition,
    ModelLibraryGraphRevision,
    ModelLibraryGraphStageReference,
    ModelLibraryPackageAsset,
    ModelLibraryPredictorFamily,
    ModelLibraryPort,
    ModelLibraryProjectReference,
    ModelLibraryTaskAsset,
    ModelLibraryTransformAsset,
    ModelLibraryValidationPlanIdentity,
    ModelLibraryVersionedIdentity,
)
from decision_workbench.contracts.task_contracts import (
    persisted_task_definition_payload,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalog,
)
from decision_workbench.modeling.model_lifecycle import QualityReport
from decision_workbench.modeling.packages.contracts import (
    FeaturePipelineDocument,
    ModelPackageManifest,
)
from decision_workbench.modeling.training.validation_plan import ValidationPlan
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog
from decision_workbench.tasks.task_registry import (
    TaskRegistry,
    TaskRegistryError,
)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}))


def _digest_key(value: str) -> str:
    return value.removeprefix("sha256:")


def _same_digest(left: str, right: str) -> bool:
    return _digest_key(left) == _digest_key(right)


def _project_reference(project) -> ModelLibraryProjectReference:
    return ModelLibraryProjectReference(
        project_id=project.id,
        project_name=project.name,
        archived=project.archived_at is not None,
    )


def _project_graph_revision_id(project) -> str | None:
    identity = project.scientific_identity
    if identity.identity_kind == "chain":
        return identity.chain_revision_id
    if identity.identity_kind == "prediction_graph":
        return identity.graph_revision_id
    return None


def _record(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _package_is_research_only(manifest: dict[str, Any]) -> bool:
    version = manifest.get("package_version")
    if isinstance(version, str) and "experimental" in version.lower():
        return True
    predictors = manifest.get("predictors")
    if not isinstance(predictors, list):
        return False
    return any(
        bool(_record(_record(item).get("config")).get("experimental"))
        for item in predictors
    )


def _package_runtime_types(manifest: dict[str, Any]) -> tuple[str, ...]:
    predictors = manifest.get("predictors")
    if not isinstance(predictors, list):
        return ()
    return _unique(
        value
        for item in predictors
        if isinstance(item, dict)
        for value in [item.get("runtime_type")]
        if isinstance(value, str)
    )


def _package_targets(manifest: dict[str, Any]) -> tuple[str, ...]:
    predictors = manifest.get("predictors")
    if not isinstance(predictors, list):
        return ()
    return _unique(
        value
        for item in predictors
        if isinstance(item, dict)
        for value in [item.get("target")]
        if isinstance(value, str)
    )


def _package_lifecycle_ids(
    manifest: dict[str, Any],
) -> tuple[str | None, str | None]:
    provenance = _record(manifest.get("provenance"))
    lifecycle = _record(provenance.get("source_lifecycle"))
    connector_id = lifecycle.get("connector_id")
    snapshot_id = lifecycle.get("training_snapshot_id")
    return (
        connector_id if isinstance(connector_id, str) else None,
        snapshot_id if isinstance(snapshot_id, str) else None,
    )


class ModelLibraryCatalogService:
    """Compose existing authorities without owning model asset persistence."""

    def __init__(
        self,
        *,
        store: Store,
        workspace_catalog: WorkspaceCatalog,
        task_registry: TaskRegistry,
        transform_catalog: DeterministicTransformCatalog | None,
    ) -> None:
        self.store = store
        self.workspace_catalog = workspace_catalog
        self.task_registry = task_registry
        self.transform_catalog = transform_catalog

    def catalog(self) -> ModelLibraryCatalog:
        projects = self.store.list_projects(include_archived=True)
        definitions = self.store.list_chain_definitions()
        revisions = self.store.list_chain_revisions()
        package_refs = self.workspace_catalog.list_model_package_refs(
            include_archived=True
        )
        graph_assets = self._graph_assets(
            definitions,
            revisions,
            projects,
            package_refs,
        )
        graph_revisions = tuple(
            revision
            for graph in graph_assets
            for definition in graph.definitions
            for revision in definition.revisions
        )
        return ModelLibraryCatalog(
            tasks=self._task_assets(
                projects,
                package_refs,
                graph_revisions,
            ),
            packages=self._package_assets(
                projects,
                package_refs,
                graph_revisions,
            ),
            transforms=self._transform_assets(graph_revisions),
            graphs=graph_assets,
        )

    def _data_reference_for_view_ids(
        self,
        view_ids: Iterable[str],
        *,
        connector_id: str | None = None,
        training_snapshot_id: str | None = None,
    ) -> ModelLibraryDataReference:
        views = [
            view
            for view_id in _unique(view_ids)
            if (
                view := self.workspace_catalog.get_dataset_view_revision(
                    view_id,
                    include_archived=True,
                )
            )
            is not None
        ]
        dataset_ids = _unique(
            member.dataset_revision_id
            for view in views
            for member in view.members
        )
        datasets = [
            dataset
            for dataset_id in dataset_ids
            if (
                dataset := self.workspace_catalog.get_dataset_revision(
                    dataset_id,
                    include_archived=True,
                )
            )
            is not None
        ]
        profiles = [
            profile
            for dataset in datasets
            if (
                profile := self.workspace_catalog.get_profile_revision(
                    dataset.profile_revision_id,
                    include_archived=True,
                )
            )
            is not None
        ]
        assets = [
            asset
            for dataset in datasets
            if (
                asset := self.workspace_catalog.get_data_asset(
                    dataset.data_asset_id,
                    include_archived=True,
                )
            )
            is not None
        ]
        return ModelLibraryDataReference(
            dataset_view_revision_ids=_unique(view.id for view in views),
            dataset_revision_ids=_unique(dataset.id for dataset in datasets),
            profile_revision_ids=_unique(profile.id for profile in profiles),
            profile_digests=_unique(
                profile.profile_digest for profile in profiles
            ),
            source_sha256s=_unique(asset.sha256 for asset in assets),
            source_names=_unique(asset.original_filename for asset in assets),
            connector_id=connector_id,
            training_snapshot_id=training_snapshot_id,
        )

    def _package_data_reference(
        self,
        package: ModelPackageRef,
    ) -> ModelLibraryDataReference:
        manifest = package.manifest_json
        provenance = _record(manifest.get("provenance"))
        training_data = provenance.get("training_data_id")
        profile_digest = provenance.get("dataset_profile_id")
        source_sha = (
            training_data.removeprefix("sha256:")
            if isinstance(training_data, str)
            else ""
        )
        datasets = [
            dataset
            for dataset in self.workspace_catalog.list_dataset_revisions(
                include_archived=True
            )
            if (
                asset := self.workspace_catalog.get_data_asset(
                    dataset.data_asset_id,
                    include_archived=True,
                )
            )
            is not None
            and asset.sha256 == source_sha
            and (
                not isinstance(profile_digest, str)
                or (
                    profile := self.workspace_catalog.get_profile_revision(
                        dataset.profile_revision_id,
                        include_archived=True,
                    )
                )
                is not None
                and profile.profile_digest == profile_digest
            )
        ]
        dataset_ids = {dataset.id for dataset in datasets}
        view_ids = [
            view.id
            for view in self.workspace_catalog.list_dataset_view_revisions(
                include_archived=True
            )
            if any(
                member.dataset_revision_id in dataset_ids
                for member in view.members
            )
        ]
        connector_id, training_snapshot_id = _package_lifecycle_ids(manifest)
        return self._data_reference_for_view_ids(
            view_ids,
            connector_id=connector_id,
            training_snapshot_id=training_snapshot_id,
        )

    @staticmethod
    def _revision_id(revision: GraphRevisionRef) -> str:
        return f"{revision.chain_id}:r{revision.revision}"

    def _revision_projects(
        self,
        revision_id: str,
        projects,
    ) -> tuple[ModelLibraryProjectReference, ...]:
        return tuple(
            _project_reference(project)
            for project in projects
            if _project_graph_revision_id(project) == revision_id
        )

    def _task_stage_reason(
        self,
        stage,
        package_refs: list[ModelPackageRef],
    ) -> str:
        try:
            availability = self.task_registry.availability_for(
                stage.contract_id
            )
            if availability.status == "unavailable":
                return availability.message
            definition = self.task_registry.contract_for(
                stage.contract_id
            ).task_definition
            digest = semantic_digest(
                persisted_task_definition_payload(definition)
            )
            if digest != stage.contract_digest:
                return "固定したTask contractを現在のcatalogで解決できません"
            active_digest = self.task_registry.entry_for(
                stage.contract_id
            ).package_digest
            if not _same_digest(
                active_digest,
                stage.package_manifest_digest,
            ):
                return "固定したModel Packageは現在のruntimeでactiveではありません"
        except TaskRegistryError:
            return "固定したPrediction Taskを現在のcatalogで解決できません"
        packages = [
            package
            for package in package_refs
            if package.task_id == stage.contract_id
            and package.task_contract_digest == stage.contract_digest
            and _same_digest(
                package.manifest_digest,
                stage.package_manifest_digest,
            )
        ]
        if not packages:
            return "固定したModel Packageを現在のcatalogで解決できません"
        if all(package.archived_at is not None for package in packages):
            return "固定したModel Packageは新規利用停止中です"
        if stage.dataset_view_revision_id is None:
            return "Prediction Task stageにDataset Viewが固定されていません"
        view = self.workspace_catalog.get_dataset_view_revision(
            stage.dataset_view_revision_id,
            include_archived=True,
        )
        if view is None:
            return "固定したDataset Viewを現在のcatalogで解決できません"
        if view.archived_at is not None:
            return "固定したDataset Viewは利用停止中です"
        profiles = [
            self.workspace_catalog.get_profile_revision(
                dataset.profile_revision_id,
                include_archived=True,
            )
            for member in view.members
            if (
                dataset := self.workspace_catalog.get_dataset_revision(
                    member.dataset_revision_id,
                    include_archived=True,
                )
            )
            is not None
        ]
        if not any(
            profile is not None
            and profile.profile_digest == stage.dataset_profile_digest
            for profile in profiles
        ):
            return "固定したDataset Profileを現在のcatalogで解決できません"
        return ""

    def _transform_stage_reason(self, stage) -> str:
        if self.transform_catalog is None:
            return "deterministic Transform catalogを利用できません"
        try:
            self.transform_catalog.resolution_for_revision(
                stage.contract_id,
                stage.package_manifest_digest,
                stage.contract_digest,
            )
        except (KeyError, ValueError):
            return "固定したdeterministic Transform revisionを解決できません"
        return ""

    def _revision_state(
        self,
        definition: GraphDefinitionRef,
        revision: GraphRevisionRef,
        *,
        latest_revision: int,
        failed_stage_ids: set[str],
        research_only: bool,
    ) -> ModelAssetState:
        if failed_stage_ids:
            if isinstance(definition, PredictionGraphDefinition):
                required_nodes = {
                    node
                    for output in definition.decision_outputs
                    if output.required_for_complete_result
                    for node in {
                        output.source_stage_id,
                        *definition.topology.ancestors[
                            output.source_stage_id
                        ],
                    }
                }
                availability = (
                    "unavailable"
                    if failed_stage_ids & required_nodes
                    else "degraded"
                )
            else:
                availability = "unavailable"
        else:
            availability = "available"

        if isinstance(revision, ChainRevision):
            lifecycle = "compatibility_only"
            reason = (
                "既存Chain v1を互換Graphとして読み取っています"
                if not failed_stage_ids
                else "既存Chain v1の固定参照をすべて解決できません"
            )
            impact = (
                "ProjectとRevisionは読み続けられますが、Prediction Graph v1の"
                "input roleとDecision Output semanticsは追加されません"
            )
            recovery = (
                "既存Revisionは変更せず、必要な場合だけ新しいPrediction Graphを"
                "別Revisionとして公開してください"
            )
        elif revision.revision < latest_revision:
            lifecycle = "superseded"
            reason = "同じPrediction Graphに新しいRevisionがあります"
            impact = "既存ProjectはこのRevisionを固定したまま再現できます"
            recovery = "新規Projectでは最新Revisionと固定参照を比較してください"
        elif research_only:
            lifecycle = "research_only"
            reason = "固定したModel Packageにexperimental表示があります"
            impact = "production-readyな品質を示す資産としては扱えません"
            recovery = "用途と品質証拠を確認し、研究用途として明示して使用してください"
        elif failed_stage_ids:
            lifecycle = "current"
            reason = "Graph Revisionの固定参照を現在のWorkspaceで解決できません"
            impact = (
                "必須outputを実行できません"
                if availability == "unavailable"
                else "一部のoptional branchを実行できません"
            )
            recovery = "対象stageのTask、Package、Dataset、Transformを復旧してください"
        else:
            return ModelAssetState(
                availability="available",
                lifecycle="current",
            )

        if failed_stage_ids and lifecycle != "current":
            impact = (
                f"{impact}。現在は"
                + (
                    "必須outputを実行できません"
                    if availability == "unavailable"
                    else "一部のoptional branchを実行できません"
                )
            )
            recovery = (
                f"{recovery}。固定参照を復旧してから実行してください"
            )
        return ModelAssetState(
            availability=availability,
            lifecycle=lifecycle,
            reason=reason,
            impact=impact,
            recovery_hint=recovery,
        )

    def _graph_assets(
        self,
        definitions: list[GraphDefinitionRef],
        revisions: list[GraphRevisionRef],
        projects,
        package_refs: list[ModelPackageRef],
    ) -> tuple[ModelLibraryGraphAsset, ...]:
        definitions_by_graph: dict[str, list[GraphDefinitionRef]] = defaultdict(
            list
        )
        for definition in definitions:
            definitions_by_graph[definition.chain_id].append(definition)
        revisions_by_graph: dict[str, list[GraphRevisionRef]] = defaultdict(
            list
        )
        for revision in revisions:
            revisions_by_graph[revision.chain_id].append(revision)

        package_research = {
            _digest_key(package.manifest_digest): _package_is_research_only(
                package.manifest_json
            )
            for package in package_refs
        }
        assets: list[ModelLibraryGraphAsset] = []
        for graph_id in sorted(definitions_by_graph):
            graph_revisions = revisions_by_graph.get(graph_id, [])
            latest_revision = max(
                (revision.revision for revision in graph_revisions),
                default=0,
            )
            definition_assets: list[ModelLibraryGraphDefinition] = []
            graph_projects: dict[str, ModelLibraryProjectReference] = {}
            task_ids: set[str] = set()
            transform_ids: set[str] = set()
            latest_state: ModelAssetState | None = None
            latest_label = graph_id
            latest_revision_id: str | None = None
            for definition in sorted(
                definitions_by_graph[graph_id],
                key=lambda item: item.digest,
            ):
                matching = [
                    revision
                    for revision in graph_revisions
                    if revision.chain_definition_digest == definition.digest
                ]
                revision_assets: list[ModelLibraryGraphRevision] = []
                projection_contracts = {}
                for revision in matching:
                    revision_id = self._revision_id(revision)
                    surfaces = self.store.get_chain_stage_contract_surfaces(
                        revision_id
                    )
                    projection_contracts.update(
                        {
                            (
                                stage.stage_kind,
                                stage.contract_id,
                            ): surfaces[stage.stage_id]
                            for stage in revision.stages
                            if stage.stage_id in surfaces
                        }
                    )
                    stage_assets: list[
                        ModelLibraryGraphStageReference
                    ] = []
                    failed_stage_ids: set[str] = set()
                    research_only = False
                    for stage in revision.stages:
                        if stage.stage_kind == "task":
                            reason = self._task_stage_reason(
                                stage,
                                package_refs,
                            )
                            task_ids.add(stage.contract_id)
                            data_refs = self._data_reference_for_view_ids(
                                (
                                    stage.dataset_view_revision_id,
                                )
                                if stage.dataset_view_revision_id
                                else ()
                            )
                        else:
                            reason = self._transform_stage_reason(stage)
                            transform_ids.add(stage.contract_id)
                            data_refs = ModelLibraryDataReference()
                        if reason:
                            failed_stage_ids.add(stage.stage_id)
                        research_only = research_only or package_research.get(
                            _digest_key(stage.package_manifest_digest),
                            False,
                        )
                        stage_assets.append(
                            ModelLibraryGraphStageReference(
                                stage_id=stage.stage_id,
                                stage_kind=stage.stage_kind,
                                contract_id=stage.contract_id,
                                contract_digest=stage.contract_digest,
                                package_manifest_digest=(
                                    stage.package_manifest_digest
                                ),
                                data_references=data_refs,
                                available=not reason,
                                reason=reason,
                            )
                        )
                    state = self._revision_state(
                        definition,
                        revision,
                        latest_revision=latest_revision,
                        failed_stage_ids=failed_stage_ids,
                        research_only=research_only,
                    )
                    revision_projects = self._revision_projects(
                        revision_id,
                        projects,
                    )
                    graph_projects.update(
                        {
                            item.project_id: item
                            for item in revision_projects
                        }
                    )
                    revision_assets.append(
                        ModelLibraryGraphRevision(
                            revision_id=revision_id,
                            revision=revision.revision,
                            revision_digest=revision.revision_digest,
                            state=state,
                            revision_contract=revision,
                            stages=tuple(stage_assets),
                            project_references=revision_projects,
                        )
                    )
                    if revision.revision == latest_revision:
                        latest_state = state
                        latest_revision_id = revision_id
                        latest_label = getattr(
                            definition,
                            "label",
                            graph_id,
                        )
                if projection_contracts:
                    projection = project_prediction_graph(
                        definition,
                        contracts=projection_contracts,
                    )
                else:
                    projection = project_prediction_graph(
                        definition,
                        contracts={},
                    )
                definition_assets.append(
                    ModelLibraryGraphDefinition(
                        definition_id=(
                            f"{graph_id}@"
                            f"{definition.digest.removeprefix('sha256:')[:12]}"
                        ),
                        definition_digest=definition.digest,
                        definition=definition,
                        projection=projection,
                        revisions=tuple(
                            sorted(
                                revision_assets,
                                key=lambda item: item.revision,
                            )
                        ),
                    )
                )
            if latest_state is None:
                latest_state = ModelAssetState(
                    availability="unavailable",
                    reason="公開済みRevisionがありません",
                    impact="このGraphからProjectを作成できません",
                    recovery_hint="Studioで検証し、immutable Revisionを公開してください",
                )
            assets.append(
                ModelLibraryGraphAsset(
                    graph_id=graph_id,
                    label=latest_label,
                    state=latest_state,
                    latest_revision_id=latest_revision_id,
                    definitions=tuple(definition_assets),
                    compatible_task_ids=_unique(task_ids),
                    compatible_transform_ids=_unique(transform_ids),
                    project_references=tuple(
                        graph_projects[key]
                        for key in sorted(graph_projects)
                    ),
                )
            )
        return tuple(assets)

    def _task_assets(
        self,
        projects,
        package_refs: list[ModelPackageRef],
        graph_revisions: tuple[ModelLibraryGraphRevision, ...],
    ) -> tuple[ModelLibraryTaskAsset, ...]:
        assets: list[ModelLibraryTaskAsset] = []
        for task_id in self.task_registry.task_ids:
            resolved = self.task_registry.resolved_definition_for(task_id)
            definition = resolved.task_definition
            availability = resolved.availability
            packages = [
                package
                for package in package_refs
                if package.task_id == task_id
            ]
            try:
                surface = resolve_task_stage_surface(
                    self.task_registry,
                    task_id,
                )
                resolve_task_stage_lock(
                    self.workspace_catalog,
                    self.task_registry,
                    surface,
                )
                authoring_ready = True
            except (ChainValidationError, TaskRegistryError, ValueError):
                authoring_ready = False
            if availability.status == "unavailable":
                state = ModelAssetState(
                    availability="unavailable",
                    reason=availability.message,
                    impact="新規ProjectとGraph authoringで利用できません",
                    recovery_hint=availability.recovery_hint,
                )
            else:
                state = ModelAssetState(
                    availability="available",
                )
            task_graph_revisions = _unique(
                revision.revision_id
                for revision in graph_revisions
                if any(
                    stage.stage_kind == "task"
                    and stage.contract_id == task_id
                    for stage in revision.stages
                )
            )
            task_projects = tuple(
                _project_reference(project)
                for project in projects
                if project.scientific_identity.identity_kind == "single_task"
                and project.scientific_identity.task_id == task_id
            )
            inputs = tuple(
                ModelLibraryPort(
                    path=field.path,
                    label=field.label,
                    value_kind=field.kind,
                    unit=field.unit,
                    required=field.required,
                )
                for group in definition.input_groups
                for field in group.fields
            )
            outputs = tuple(
                ModelLibraryPort(
                    path=output.key,
                    label=output.label,
                    value_kind=output.target_kind,
                    unit=output.unit,
                    quantity=output.key,
                )
                for output in definition.outputs
            )
            assets.append(
                ModelLibraryTaskAsset(
                    task_id=task_id,
                    label=definition.label,
                    contract_digest=semantic_digest(
                        persisted_task_definition_payload(definition)
                    ),
                    state=state,
                    inputs=inputs,
                    outputs=outputs,
                    package_reference_ids=_unique(
                        package.id for package in packages
                    ),
                    graph_revision_ids=task_graph_revisions,
                    project_references=task_projects,
                    onboarding_ready=(
                        availability.status == "available"
                        and resolved.application.project_creation
                    ),
                    graph_authoring_ready=authoring_ready,
                )
            )
        return tuple(assets)

    def _package_assets(
        self,
        projects,
        package_refs: list[ModelPackageRef],
        graph_revisions: tuple[ModelLibraryGraphRevision, ...],
    ) -> tuple[ModelLibraryPackageAsset, ...]:
        assets: list[ModelLibraryPackageAsset] = []
        for package in sorted(package_refs, key=lambda item: item.id):
            manifest = package.manifest_json
            typed_manifest: ModelPackageManifest | None = None
            try:
                typed_manifest = ModelPackageManifest.model_validate(manifest)
            except ValueError:
                pass
            research_only = _package_is_research_only(manifest)
            reason = ""
            recovery = ""
            try:
                availability = self.task_registry.availability_for(
                    package.task_id
                )
                definition = self.task_registry.contract_for(
                    package.task_id
                ).task_definition
                current_contract = semantic_digest(
                    persisted_task_definition_payload(definition)
                )
                active_digest = (
                    self.task_registry.entry_for(
                        package.task_id
                    ).package_digest
                    if availability.status == "available"
                    else ""
                )
                if availability.status == "unavailable":
                    reason = availability.message
                    recovery = availability.recovery_hint
                elif current_contract != package.task_contract_digest:
                    reason = "現在のPrediction Task contractと一致しません"
                    recovery = "TaskとPackageの対応を確認して再登録してください"
                elif not _same_digest(active_digest, package.manifest_digest):
                    reason = "現在のruntimeが選択しているPackageではありません"
                    recovery = "必要ならPackageを明示的に昇格し、再読込してください"
            except TaskRegistryError:
                reason = "対応するPrediction Taskを解決できません"
                recovery = "TaskとPackageを同じWorkspaceへ登録してください"
            if package.archived_at is not None:
                reason = "Model Packageは新規利用停止中です"
                recovery = "新規Projectで使う場合は利用可能に戻してください"
            if reason:
                state = ModelAssetState(
                    availability="unavailable",
                    lifecycle=(
                        "research_only" if research_only else "current"
                    ),
                    reason=reason,
                    impact="新規ProjectやGraph Revisionへ固定できません",
                    recovery_hint=recovery,
                )
            elif research_only:
                state = ModelAssetState(
                    availability="available",
                    lifecycle="research_only",
                    reason="Package manifestにexperimental表示があります",
                    impact="production-readyな品質を示す資産としては扱えません",
                    recovery_hint="用途と品質証拠を確認し、研究用途として明示してください",
                )
            else:
                state = ModelAssetState(
                    availability="available",
                )
            package_graph_revisions = _unique(
                revision.revision_id
                for revision in graph_revisions
                if any(
                    _same_digest(
                        stage.package_manifest_digest,
                        package.manifest_digest,
                    )
                    for stage in revision.stages
                )
            )
            graph_project_ids = {
                item.project_id
                for revision in graph_revisions
                if revision.revision_id in package_graph_revisions
                for item in revision.project_references
            }
            package_projects = tuple(
                _project_reference(project)
                for project in projects
                if (
                    project.scientific_identity.identity_kind
                    == "single_task"
                    and (
                        project.scientific_identity.model_package_ref_id
                        == package.id
                        or _same_digest(
                            project.scientific_identity.model_package_manifest_digest,
                            package.manifest_digest,
                        )
                    )
                )
                or project.id in graph_project_ids
            )
            version = manifest.get("package_version")
            predictor_families = (
                tuple(
                    ModelLibraryPredictorFamily(
                        predictor_id=predictor.id,
                        target=predictor.target,
                        runtime_type=predictor.runtime_type,
                        predictive_family=predictor.predictive_family,
                        architecture_id=predictor.architecture_id,
                    )
                    for predictor in typed_manifest.predictors
                )
                if typed_manifest is not None
                else ()
            )
            feature_pipeline = (
                ModelLibraryVersionedIdentity(
                    identity_id=typed_manifest.feature_pipeline.id,
                    version=typed_manifest.feature_pipeline.version,
                )
                if typed_manifest is not None
                and typed_manifest.feature_pipeline is not None
                else None
            )
            feature_recipe = None
            validation_plans: tuple[
                ModelLibraryValidationPlanIdentity, ...
            ] = ()
            try:
                entry = self.task_registry.entry_for(package.task_id)
                verified = entry.model_package
                if not _same_digest(
                    entry.package_digest,
                    package.manifest_digest,
                ):
                    raise TaskRegistryError("package is not active")
                if typed_manifest is not None and typed_manifest.feature_pipeline:
                    pipeline = FeaturePipelineDocument.model_validate_json(
                        verified.artifact_path(
                            typed_manifest.feature_pipeline.spec
                        ).read_text(encoding="utf-8")
                    )
                    if pipeline.feature_recipe is not None:
                        recipe = FeatureRecipe.model_validate_json(
                            verified.artifact_path(
                                pipeline.feature_recipe.recipe
                            ).read_text(encoding="utf-8")
                        )
                        feature_recipe = ModelLibraryVersionedIdentity(
                            identity_id=recipe.id,
                            version=recipe.version,
                            digest=pipeline.feature_recipe.recipe_digest,
                        )
                if typed_manifest is not None and typed_manifest.quality_report:
                    quality = QualityReport.model_validate_json(
                        verified.artifact_path(
                            typed_manifest.quality_report
                        ).read_text(encoding="utf-8")
                    )
                    identities = []
                    for target, payload in sorted(
                        (quality.validation_plans or {}).items()
                    ):
                        plan_payload = dict(payload)
                        digest = plan_payload.pop("digest", None)
                        plan = ValidationPlan.model_validate(plan_payload)
                        if not isinstance(digest, str):
                            evidence = (quality.validation_evidence or {}).get(
                                target
                            )
                            digest = (
                                evidence.validation_plan_digest
                                if evidence is not None
                                else None
                            )
                        if digest is not None:
                            identities.append(
                                ModelLibraryValidationPlanIdentity(
                                    target=target,
                                    schema_version=plan.schema_version,
                                    strategy=plan.strategy,
                                    digest=digest,
                                    identity_source="validation_plan",
                                )
                            )
                    if not identities:
                        identities.extend(
                            ModelLibraryValidationPlanIdentity(
                                target=metric.target,
                                schema_version=quality.schema_version,
                                strategy=quality.split,
                                digest=semantic_digest(
                                    {
                                        "schema_version": (
                                            quality.schema_version
                                        ),
                                        "split": quality.split,
                                        "folds": quality.folds,
                                        "target": metric.target,
                                    }
                                ),
                                identity_source="quality_report_split",
                            )
                            for metric in quality.targets
                        )
                    validation_plans = tuple(identities)
            except (KeyError, OSError, TaskRegistryError, ValueError):
                # Historical references remain visible without reading their
                # locators; verified artifact identities are active-package only.
                pass
            assets.append(
                ModelLibraryPackageAsset(
                    reference_id=package.id,
                    package_id=package.package_id,
                    version=version if isinstance(version, str) else "",
                    task_id=package.task_id,
                    task_contract_digest=package.task_contract_digest,
                    manifest_digest=package.manifest_digest,
                    storage_scope=package.storage_scope,
                    state=state,
                    runtime_types=_package_runtime_types(manifest),
                    predictor_targets=_package_targets(manifest),
                    predictor_families=predictor_families,
                    feature_pipeline=feature_pipeline,
                    feature_recipe=feature_recipe,
                    validation_plans=validation_plans,
                    quality_summary_available=isinstance(
                        manifest.get("quality_report"),
                        str,
                    ),
                    data_references=self._package_data_reference(package),
                    graph_revision_ids=package_graph_revisions,
                    project_references=package_projects,
                )
            )
        return tuple(assets)

    def _transform_assets(
        self,
        graph_revisions: tuple[ModelLibraryGraphRevision, ...],
    ) -> tuple[ModelLibraryTransformAsset, ...]:
        assets: list[ModelLibraryTransformAsset] = []
        persisted_ids = {
            stage.contract_id
            for revision in graph_revisions
            for stage in revision.stages
            if stage.stage_kind == "deterministic_transform"
        }
        current_ids = (
            set(self.transform_catalog.transform_ids)
            if self.transform_catalog is not None
            else set()
        )
        for transform_id in sorted(current_ids | persisted_ids):
            if (
                self.transform_catalog is None
                or transform_id not in current_ids
            ):
                surface = None
                lock = None
                state = ModelAssetState(
                    availability="unavailable",
                    reason="現在のTransform catalogに登録されていません",
                    impact="既存Graphの利用箇所は確認できますが、新しいRevisionへ追加できません",
                    recovery_hint="Transform Packageとallow-listを復旧してください",
                )
            else:
                try:
                    surface, lock = self.transform_catalog.authoring_contract(
                        transform_id
                    )
                    state = ModelAssetState(availability="available")
                except (KeyError, ValueError) as exc:
                    surface = None
                    lock = None
                    state = ModelAssetState(
                        availability="unavailable",
                        reason=str(exc),
                        impact="新しいGraph Revisionへ追加できません",
                        recovery_hint="Transform Packageとallow-listを確認してください",
                    )
            assets.append(
                ModelLibraryTransformAsset(
                    transform_id=transform_id,
                    label=transform_id,
                    state=state,
                    surface=surface,
                    package_manifest_digest=(
                        lock.package_manifest_digest if lock else None
                    ),
                    graph_revision_ids=_unique(
                        revision.revision_id
                        for revision in graph_revisions
                        if any(
                            stage.stage_kind == "deterministic_transform"
                            and stage.contract_id == transform_id
                            for stage in revision.stages
                        )
                    ),
                )
            )
        return tuple(assets)
