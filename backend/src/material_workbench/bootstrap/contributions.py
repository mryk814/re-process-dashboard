"""Bootstrap optional application contributions without owning core startup."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI

from material_workbench.application.chain_evaluation import (
    DEFAULT_CHAIN_EVALUATION_PATH,
    ChainEvaluationCatalog,
)
from material_workbench.application.chain_execution_plan import ChainPlanningUseCase
from material_workbench.application.chain_execution_use_case import (
    ChainExecutionCoordinator,
    ChainExecutionUseCase,
)
from material_workbench.application.chain_snapshot_use_case import ChainSnapshotUseCase
from material_workbench.application.chain_stage_execution import ChainStageExecutor
from material_workbench.application.chain_uncertainty import (
    ChainUncertaintyService,
)
from material_workbench.bootstrap.resources import AppResources
from material_workbench.contracts.subsystem_availability import (
    WELDING_CHAIN_EVALUATION_RESOURCE_ID,
    WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
    WELDING_CHAIN_RESOURCE_ID,
    WELDING_CHAIN_SUBSYSTEM_ID,
    WELDING_TRANSFORM_RESOURCE_ID,
    WELDING_TRANSFORM_SUBSYSTEM_ID,
    SubsystemAvailabilityRegistry,
    SubsystemKind,
)
from material_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalogUnavailableError,
    load_deterministic_transform_catalog,
)
from material_workbench.persistence.welding_chain_bootstrap import (
    WeldingChainBootstrapError,
    bootstrap_welding_chain,
)
from material_workbench.tasks.task_registry import TaskRegistry

logger = logging.getLogger(__name__)


def _record_optional_failure(
    registry: SubsystemAvailabilityRegistry,
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
    registry.record_unavailable(
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


def build_chain_services(
    store: Any,
    task_registry: TaskRegistry,
    transform_catalog: Any,
) -> tuple[
    ChainPlanningUseCase,
    ChainExecutionUseCase,
    ChainSnapshotUseCase,
    ChainUncertaintyService | None,
]:
    planning = ChainPlanningUseCase(store, task_registry, transform_catalog)
    stages = ChainStageExecutor(task_registry, transform_catalog)
    execution = ChainExecutionUseCase(planning, stages, ChainExecutionCoordinator())
    snapshots = ChainSnapshotUseCase(planning, stages)
    uncertainty = (
        ChainUncertaintyService(store, planning, stages)
        if transform_catalog is not None
        else None
    )
    return planning, execution, snapshots, uncertainty


def bootstrap_welding_chain_contribution(
    app: FastAPI,
    *,
    task_registry: TaskRegistry,
    workspace_catalog: Any,
) -> tuple[str | None, WeldingChainBootstrapError | None]:
    transform_catalog = app.state.deterministic_transform_catalog
    chain_revision_id = app.state.welding_chain_revision_id
    if transform_catalog is None:
        return chain_revision_id, None
    try:
        chain_revision_id = bootstrap_welding_chain(
            store=app.state.store,
            workspace_catalog=workspace_catalog,
            task_registry=task_registry,
            transform_catalog=transform_catalog,
        )
    except WeldingChainBootstrapError as exc:
        return chain_revision_id, exc
    return chain_revision_id, None


def record_promoted_welding_chain(
    app: FastAPI,
    *,
    chain_revision_id: str | None,
    chain_error: WeldingChainBootstrapError | None,
) -> None:
    if chain_error is None:
        app.state.subsystem_availability.record_available(
            subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
            kind="chain",
            resource_id=WELDING_CHAIN_RESOURCE_ID,
            owner_kind="chain",
            owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
            stage="chain_catalog",
        )
    else:
        _record_optional_failure(
            app.state.subsystem_availability,
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
            exc=chain_error,
        )
    app.state.welding_chain_revision_id = chain_revision_id


def initialize_contributions(
    app: FastAPI,
    prepared: AppResources,
    *,
    active_transforms_path: str | Path | None,
    chain_evaluation_path: str | Path | None,
    defer_resources: bool,
) -> None:
    """Initialize optional transforms, Chain resources, and evaluation data."""

    app.state.subsystem_availability = SubsystemAvailabilityRegistry()
    transform_catalog = None
    try:
        transform_catalog = load_deterministic_transform_catalog(
            active_transforms_path
        )
        app.state.subsystem_availability.record_available(
            subsystem_id=WELDING_TRANSFORM_SUBSYSTEM_ID,
            kind="deterministic_transform",
            resource_id=WELDING_TRANSFORM_RESOURCE_ID,
            owner_kind="transform",
            owner_resource_id=WELDING_TRANSFORM_RESOURCE_ID,
            stage="deterministic_transforms",
        )
    except DeterministicTransformCatalogUnavailableError as exc:
        _record_optional_failure(
            app.state.subsystem_availability,
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
    app.state.deterministic_transform_catalog = transform_catalog
    chain_revision_id = None
    if transform_catalog is None:
        dependency = app.state.subsystem_availability.get(
            WELDING_TRANSFORM_SUBSYSTEM_ID
        )
        app.state.subsystem_availability.record_unavailable(
            subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
            kind="chain",
            resource_id=WELDING_CHAIN_RESOURCE_ID,
            owner_kind="chain",
            owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
            stage="chain_catalog",
            cause=(
                f"dependency_unavailable: {WELDING_TRANSFORM_SUBSYSTEM_ID}"
            ),
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
    elif defer_resources:
        app.state.subsystem_availability.record_unavailable(
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
    else:
        try:
            chain_revision_id = bootstrap_welding_chain(
                store=app.state.store,
                workspace_catalog=app.state.workspace_catalog,
                task_registry=prepared.task_registry,
                transform_catalog=transform_catalog,
            )
            app.state.subsystem_availability.record_available(
                subsystem_id=WELDING_CHAIN_SUBSYSTEM_ID,
                kind="chain",
                resource_id=WELDING_CHAIN_RESOURCE_ID,
                owner_kind="chain",
                owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
                stage="chain_catalog",
            )
        except WeldingChainBootstrapError as exc:
            _record_optional_failure(
                app.state.subsystem_availability,
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
    app.state.welding_chain_revision_id = chain_revision_id

    evaluation_catalog = None
    try:
        configured_evaluation = (
            chain_evaluation_path
            or os.getenv("WORKBENCH_CHAIN_EVALUATION_PATH")
            or DEFAULT_CHAIN_EVALUATION_PATH
        )
        evaluation_catalog = ChainEvaluationCatalog.load(
            configured_evaluation
        )
        app.state.subsystem_availability.record_available(
            subsystem_id=WELDING_CHAIN_EVALUATION_SUBSYSTEM_ID,
            kind="chain_evaluation",
            resource_id=WELDING_CHAIN_EVALUATION_RESOURCE_ID,
            owner_kind="chain",
            owner_resource_id=WELDING_CHAIN_RESOURCE_ID,
            stage="chain_evaluation",
        )
    except (OSError, ValueError) as exc:
        _record_optional_failure(
            app.state.subsystem_availability,
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
    app.state.chain_evaluation_catalog = evaluation_catalog
    (
        app.state.chain_planning_use_case,
        app.state.chain_execution_use_case,
        app.state.chain_snapshot_use_case,
        app.state.chain_uncertainty_service,
    ) = build_chain_services(
        app.state.store,
        prepared.task_registry,
        transform_catalog,
    )
