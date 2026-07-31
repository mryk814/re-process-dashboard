"""Typed application contributions from the internal allow-list."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, Protocol, runtime_checkable

from fastapi import APIRouter, FastAPI

from decision_workbench.api.blend_optimization import (
    router as blend_optimization_router,
)
from decision_workbench.api.chains import execution_router
from decision_workbench.api.chains import router as chains_router
from decision_workbench.api.transforms import router as transforms_router
from decision_workbench.application.chain_evaluation import (
    DEFAULT_CHAIN_EVALUATION_PATH,
    ChainEvaluationCatalog,
)
from decision_workbench.application.chain.plan import (
    ChainPlanningUseCase,
)
from decision_workbench.application.chain.execution import (
    ChainExecutionCoordinator,
    ChainExecutionUseCase,
)
from decision_workbench.application.chain.snapshot import (
    ChainSnapshotUseCase,
)
from decision_workbench.application.chain.stage_execution import (
    ChainStageExecutor,
)
from decision_workbench.application.chain_uncertainty import (
    ChainUncertaintyService,
)
from decision_workbench.contracts.blend_contracts import BlendContractRegistry
from decision_workbench.contracts.subsystem_availability import (
    WELDING_CHAIN_EVALUATION_RESOURCE_ID,
    WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
    WELDING_CHAIN_RESOURCE_ID,
    WELDING_CHAIN_SUBSYSTEM_ID,
    WELDING_TRANSFORM_RESOURCE_ID,
    WELDING_TRANSFORM_SUBSYSTEM_ID,
    SubsystemKind,
    SubsystemAvailabilityRegistry,
)
from decision_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalog,
    DeterministicTransformCatalogUnavailableError,
    load_deterministic_transform_catalog,
)
from decision_workbench.persistence.welding_chain_bootstrap import (
    WeldingChainBootstrapError,
    bootstrap_welding_chain,
)
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog
from decision_workbench.tasks.task_registry import TaskRegistry

logger = logging.getLogger(__name__)

WELDING_BLEND_CONTRIBUTION_ID = "welding-blend"


@runtime_checkable
class ApplicationContributionConfig(Protocol):
    """Configuration accepted by one allow-listed application contribution."""

    contribution_id: str


@dataclass(frozen=True)
class ApplicationContributionContext:
    store: Store
    workspace_catalog: WorkspaceCatalog
    task_registry: TaskRegistry
    subsystem_availability: SubsystemAvailabilityRegistry


class ApplicationContributionRuntime(Protocol):
    """One immutable generation of contribution-owned services."""

    contribution_id: str

    def install_state(self, app: FastAPI) -> None:
        """Publish diagnostic mirrors for existing API and test integrations."""


class ApplicationContribution(Protocol):
    contribution_id: str
    routers: tuple[APIRouter, ...]

    def initialize(
        self,
        context: ApplicationContributionContext,
        *,
        defer_resources: bool,
    ) -> ApplicationContributionRuntime: ...

    def rebuild(
        self,
        context: ApplicationContributionContext,
        current: ApplicationContributionRuntime,
        *,
        promote_deferred: bool,
    ) -> ApplicationContributionRuntime: ...


@dataclass(frozen=True)
class WeldingBlendContributionConfig:
    contribution_id: str = WELDING_BLEND_CONTRIBUTION_ID
    active_transforms_path: str | Path | None = None
    chain_evaluation_path: str | Path | None = None
    blend_contracts: BlendContractRegistry | None = None


@dataclass(frozen=True)
class WeldingBlendContributionRuntime:
    contribution_id: str
    blend_contract_registry: BlendContractRegistry
    transform_catalog: DeterministicTransformCatalog | None
    chain_revision_id: str | None
    evaluation_catalog: ChainEvaluationCatalog | None
    planning_use_case: ChainPlanningUseCase
    execution_use_case: ChainExecutionUseCase
    snapshot_use_case: ChainSnapshotUseCase
    uncertainty_service: ChainUncertaintyService | None

    def install_state(self, app: FastAPI) -> None:
        app.state.blend_contract_registry = self.blend_contract_registry
        app.state.deterministic_transform_catalog = self.transform_catalog
        app.state.welding_chain_revision_id = self.chain_revision_id
        app.state.chain_evaluation_catalog = self.evaluation_catalog
        app.state.chain_planning_use_case = self.planning_use_case
        app.state.chain_execution_use_case = self.execution_use_case
        app.state.chain_snapshot_use_case = self.snapshot_use_case
        app.state.chain_uncertainty_service = self.uncertainty_service


def _record_optional_failure(
    context: ApplicationContributionContext,
    *,
    subsystem_id: str,
    kind: SubsystemKind,
    resource_id: str,
    owner_kind: Literal["chain", "transform"] | None = None,
    owner_resource_id: str | None = None,
    stage: str,
    label: str,
    impact: str,
    recovery_hint: str,
    exc: Exception,
) -> None:
    logger.exception(
        "OPTIONAL_SUBSYSTEM_UNAVAILABLE subsystem_id=%s stage=%s "
        "error_type=%s detail=%s",
        subsystem_id,
        stage,
        type(exc).__name__,
        exc,
    )
    context.subsystem_availability.record_unavailable(
        subsystem_id=subsystem_id,
        kind=kind,
        resource_id=resource_id,
        owner_kind=owner_kind,
        owner_resource_id=owner_resource_id,
        stage=stage,
        cause=f"{type(exc).__name__}: {str(exc).splitlines()[0]}",
        message=f"{label}を利用できません。",
        impact=impact,
        recovery_hint=recovery_hint,
    )


def _build_chain_services(
    context: ApplicationContributionContext,
    transform_catalog: DeterministicTransformCatalog | None,
) -> tuple[
    ChainPlanningUseCase,
    ChainExecutionUseCase,
    ChainSnapshotUseCase,
    ChainUncertaintyService | None,
]:
    planning = ChainPlanningUseCase(
        context.store,
        context.task_registry,
        transform_catalog,
    )
    stages = ChainStageExecutor(context.task_registry, transform_catalog)
    execution = ChainExecutionUseCase(
        planning,
        stages,
        ChainExecutionCoordinator(),
    )
    snapshots = ChainSnapshotUseCase(planning, stages)
    return (
        planning,
        execution,
        snapshots,
        ChainUncertaintyService(context.store, planning, stages)
        if transform_catalog is not None
        else None,
    )


@dataclass(frozen=True)
class WeldingBlendApplicationContribution:
    config: WeldingBlendContributionConfig
    contribution_id: str = WELDING_BLEND_CONTRIBUTION_ID
    routers: tuple[APIRouter, ...] = (
        chains_router,
        execution_router,
        blend_optimization_router,
        transforms_router,
    )

    def initialize(
        self,
        context: ApplicationContributionContext,
        *,
        defer_resources: bool,
    ) -> WeldingBlendContributionRuntime:
        transform_catalog = self._load_transform_catalog(context)
        chain_revision_id = self._bootstrap_chain(
            context,
            transform_catalog,
            defer_resources=defer_resources,
        )
        evaluation_catalog = self._load_evaluation_catalog(context)
        planning, execution, snapshots, uncertainty = _build_chain_services(
            context,
            transform_catalog,
        )
        return WeldingBlendContributionRuntime(
            contribution_id=self.contribution_id,
            blend_contract_registry=(
                self.config.blend_contracts or BlendContractRegistry()
            ),
            transform_catalog=transform_catalog,
            chain_revision_id=chain_revision_id,
            evaluation_catalog=evaluation_catalog,
            planning_use_case=planning,
            execution_use_case=execution,
            snapshot_use_case=snapshots,
            uncertainty_service=uncertainty,
        )

    def rebuild(
        self,
        context: ApplicationContributionContext,
        current: ApplicationContributionRuntime,
        *,
        promote_deferred: bool,
    ) -> WeldingBlendContributionRuntime:
        if not isinstance(current, WeldingBlendContributionRuntime):
            raise TypeError("invalid Welding/Blend contribution runtime")
        revision_id = current.chain_revision_id
        if promote_deferred and current.transform_catalog is not None:
            revision_id = self._bootstrap_chain(
                context,
                current.transform_catalog,
                defer_resources=False,
            )
        planning, execution, snapshots, uncertainty = _build_chain_services(
            context, current.transform_catalog
        )
        return WeldingBlendContributionRuntime(
            contribution_id=self.contribution_id,
            blend_contract_registry=current.blend_contract_registry,
            transform_catalog=current.transform_catalog,
            chain_revision_id=revision_id,
            evaluation_catalog=current.evaluation_catalog,
            planning_use_case=planning,
            execution_use_case=execution,
            snapshot_use_case=snapshots,
            uncertainty_service=uncertainty,
        )

    def _load_transform_catalog(
        self, context: ApplicationContributionContext
    ) -> DeterministicTransformCatalog | None:
        try:
            catalog = load_deterministic_transform_catalog(
                self.config.active_transforms_path
            )
            context.subsystem_availability.record_available(
                subsystem_id=WELDING_TRANSFORM_SUBSYSTEM_ID,
                kind="deterministic_transform",
                resource_id=WELDING_TRANSFORM_RESOURCE_ID,
                owner_kind="transform",
                owner_resource_id=WELDING_TRANSFORM_RESOURCE_ID,
                stage="deterministic_transforms",
            )
            return catalog
        except DeterministicTransformCatalogUnavailableError as exc:
            _record_optional_failure(
                context,
                subsystem_id=WELDING_TRANSFORM_SUBSYSTEM_ID,
                kind="deterministic_transform",
                resource_id=WELDING_TRANSFORM_RESOURCE_ID,
                owner_kind="transform",
                owner_resource_id=WELDING_TRANSFORM_RESOURCE_ID,
                stage="deterministic_transforms",
                label="溶接材料の決定論的Transform",
                impact=(
                    "このTransformを使う配合編集とChain実行を停止します。"
                    "ほかの予測Taskと保存済み証跡は利用できます。"
                ),
                recovery_hint=(
                    "active-transforms.jsonと対象Packageのmanifest・artifact "
                    "digestを確認して再起動してください。"
                ),
                exc=exc,
            )
            return None

    def _bootstrap_chain(
        self,
        context: ApplicationContributionContext,
        transform_catalog: DeterministicTransformCatalog | None,
        *,
        defer_resources: bool,
    ) -> str | None:
        if transform_catalog is None:
            dependency = context.subsystem_availability.get(
                WELDING_TRANSFORM_SUBSYSTEM_ID
            )
            context.subsystem_availability.record_unavailable(
                subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                kind="chain",
                resource_id=WELDING_CHAIN_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_catalog",
                cause=f"dependency_unavailable: {WELDING_TRANSFORM_SUBSYSTEM_ID}",
                message="溶接材料Chainを利用できません。",
                impact=(
                    "このChainの候補編集と実行を停止します。"
                    "保存済みProject・Run・SnapshotはProject概要から参照できます。"
                ),
                recovery_hint=(
                    dependency.recovery_hint
                    if dependency is not None
                    else "依存する決定論的Transformを復旧して再起動してください。"
                ),
            )
            return None
        if defer_resources:
            context.subsystem_availability.record_unavailable(
                subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                kind="chain",
                resource_id=WELDING_CHAIN_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_catalog",
                cause="resources_loading",
                message="起動後にChainのTask resourceを準備しています。",
                impact="準備中はChain候補の編集と実行を待機します。",
                recovery_hint="準備完了後に自動で利用可能になります。",
            )
            return None
        try:
            revision_id = bootstrap_welding_chain(
                store=context.store,
                workspace_catalog=context.workspace_catalog,
                task_registry=context.task_registry,
                transform_catalog=transform_catalog,
            )
            context.subsystem_availability.record_available(
                subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                kind="chain",
                resource_id=WELDING_CHAIN_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_catalog",
            )
            return revision_id
        except WeldingChainBootstrapError as exc:
            _record_optional_failure(
                context,
                subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                kind="chain",
                resource_id=WELDING_CHAIN_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_catalog",
                label="溶接材料Chain",
                impact=(
                    "このChainの候補編集と実行を停止します。"
                    "保存済みProject・Run・SnapshotはProject概要から参照できます。"
                ),
                recovery_hint=(
                    "Chain Definition、binding、固定Dataset／Package参照を"
                    "確認して再起動してください。"
                ),
                exc=exc,
            )
            return None

    def _load_evaluation_catalog(
        self, context: ApplicationContributionContext
    ) -> ChainEvaluationCatalog | None:
        try:
            configured = (
                self.config.chain_evaluation_path
                or os.getenv("WORKBENCH_CHAIN_EVALUATION_PATH")
                or DEFAULT_CHAIN_EVALUATION_PATH
            )
            catalog = ChainEvaluationCatalog.load(configured)
            context.subsystem_availability.record_available(
                subsystem_id=WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
                kind="chain_evaluation",
                resource_id=WELDING_CHAIN_EVALUATION_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_evaluation",
            )
            return catalog
        except (OSError, ValueError) as exc:
            _record_optional_failure(
                context,
                subsystem_id=WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
                kind="chain_evaluation",
                resource_id=WELDING_CHAIN_EVALUATION_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_evaluation",
                label="溶接材料Chainの評価成果物",
                impact=(
                    "段単体／通し評価だけを停止します。"
                    "Chain候補、実行、保存済み証跡とほかのTaskは利用できます。"
                ),
                recovery_hint=(
                    "評価JSONのschema、digest、Chain／Stage identityを"
                    "確認して再起動してください。"
                ),
                exc=exc,
            )
            return None


def create_welding_blend_contribution(
    config: WeldingBlendContributionConfig,
) -> WeldingBlendApplicationContribution:
    return WeldingBlendApplicationContribution(config=config)


def builtin_application_contributions(
    configs: Mapping[str, ApplicationContributionConfig] | None = None,
) -> tuple[ApplicationContribution, ...]:
    """Build only the application-owned allow-list; no external plugins."""

    configured = dict(configs or {})
    unknown = set(configured) - {WELDING_BLEND_CONTRIBUTION_ID}
    if unknown:
        raise ValueError(
            "unknown application contribution: " + ", ".join(sorted(unknown))
        )
    welding_config = configured.get(
        WELDING_BLEND_CONTRIBUTION_ID,
        WeldingBlendContributionConfig(),
    )
    if not isinstance(welding_config, WeldingBlendContributionConfig):
        raise TypeError(
            f"{WELDING_BLEND_CONTRIBUTION_ID} requires "
            "WeldingBlendContributionConfig"
        )
    return (create_welding_blend_contribution(welding_config),)


def initialize_application_contributions(
    contributions: tuple[ApplicationContribution, ...],
    context: ApplicationContributionContext,
    *,
    defer_resources: bool,
) -> Mapping[str, ApplicationContributionRuntime]:
    runtimes = {
        contribution.contribution_id: contribution.initialize(
            context,
            defer_resources=defer_resources,
        )
        for contribution in contributions
    }
    return MappingProxyType(runtimes)


def rebuild_application_contributions(
    contributions: tuple[ApplicationContribution, ...],
    context: ApplicationContributionContext,
    current: Mapping[str, ApplicationContributionRuntime],
    *,
    promote_deferred: bool,
) -> Mapping[str, ApplicationContributionRuntime]:
    runtimes = {
        contribution.contribution_id: contribution.rebuild(
            context,
            current[contribution.contribution_id],
            promote_deferred=promote_deferred,
        )
        for contribution in contributions
    }
    return MappingProxyType(runtimes)


def install_application_contribution_state(
    app: FastAPI,
    runtimes: Mapping[str, ApplicationContributionRuntime],
) -> None:
    for runtime in runtimes.values():
        runtime.install_state(app)
