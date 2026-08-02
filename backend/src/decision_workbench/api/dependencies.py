from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from decision_workbench.application.ai_review_provider import AiReviewProvider
from decision_workbench.application.catalog.contracts import CatalogRuntimeState
from decision_workbench.application.catalog.feature_inspector import FeatureInspector
from decision_workbench.application.catalog.task_package_catalog import (
    TaskPackageCatalog,
)
from decision_workbench.application.catalog.training_inspector import (
    TrainingInspector,
)
from decision_workbench.application.chains import ChainUseCases
from decision_workbench.application.data_library import DataLibraryUseCases
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.application.prediction_graphs import (
    PredictionGraphUseCases,
)
from decision_workbench.contracts.blend_contracts import BlendContractRegistry
from decision_workbench.contracts.candidate_project_contracts import Project
from decision_workbench.contracts.subsystem_availability import (
    WELDING_TRANSFORM_SUBSYSTEM_ID,
    SubsystemAvailabilityRegistry,
)
from decision_workbench.execution.inference_work_graph import InferenceWorkGraph
from decision_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from decision_workbench.persistence.store import Store
from decision_workbench.persistence.workspace_catalog import WorkspaceCatalog
from decision_workbench.tasks.task_registry import TaskRegistry


def get_runtime_context(request: Request) -> Any:
    """Return one immutable generation of the resources swapped after startup."""

    return getattr(
        request.state,
        "runtime_context",
        getattr(request.app.state, "runtime_context", request.app.state),
    )


def get_application_contribution_runtime(
    request: Request,
    contribution_id: str,
) -> Any:
    return get_runtime_context(request).contribution_runtimes[contribution_id]


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_task_registry(request: Request) -> TaskRegistry:
    return get_runtime_context(request).task_registry


def get_blend_contract_registry(request: Request) -> BlendContractRegistry:
    return get_application_contribution_runtime(
        request, "welding-blend"
    ).blend_contract_registry


def get_deterministic_transform_catalog(
    request: Request,
) -> DeterministicTransformCatalog:
    request.app.state.subsystem_availability.require(WELDING_TRANSFORM_SUBSYSTEM_ID)
    catalog = get_application_contribution_runtime(
        request, "welding-blend"
    ).transform_catalog
    assert catalog is not None
    return catalog


def get_subsystem_availability(
    request: Request,
) -> SubsystemAvailabilityRegistry:
    return request.app.state.subsystem_availability


def get_workspace_catalog(request: Request) -> WorkspaceCatalog:
    return get_runtime_context(request).workspace_catalog


def get_data_library_root(request: Request) -> Path:
    return request.app.state.data_library_root


def get_available_packages_paths(request: Request) -> tuple[Path, ...]:
    return request.app.state.available_packages_paths


def get_personal_available_packages_paths(request: Request) -> tuple[Path, ...]:
    return request.app.state.personal_available_packages_paths


def get_model_package_origins(request: Request) -> dict[str, str]:
    return request.app.state.model_package_origins


def get_project_runtime_resolver(request: Request) -> ProjectRuntimeResolver:
    return get_runtime_context(request).project_runtime_resolver


def get_inference_work_graph(request: Request) -> InferenceWorkGraph:
    return request.app.state.inference_work_graph


def get_ai_review_provider(request: Request) -> AiReviewProvider | None:
    return request.app.state.ai_review_provider


def _catalog_runtime_state(request: Request) -> CatalogRuntimeState:
    state = request.app.state
    return CatalogRuntimeState(
        resources_ready=bool(getattr(state, "resources_ready", True)),
        resources_loading_error=getattr(state, "resources_loading_error", None),
        workspace_database=state.workspace_database,
        data_library_root=state.data_library_root,
        workspace_kind=state.workspace_kind,
        task_store_path=state.task_store_path,
        model_store_path=getattr(state, "model_store_path", None),
    )


def get_task_package_catalog(request: Request) -> TaskPackageCatalog:
    state = request.app.state
    context = get_runtime_context(request)
    contribution = get_application_contribution_runtime(
        request, "welding-blend"
    )
    return TaskPackageCatalog(
        state=_catalog_runtime_state(request),
        store=state.store,
        registry=context.task_registry,
        resolver=context.project_runtime_resolver,
        subsystem_registry=state.subsystem_availability,
        transform_catalog=contribution.transform_catalog,
    )


def get_training_inspector(request: Request) -> TrainingInspector:
    state = request.app.state
    context = get_runtime_context(request)
    return TrainingInspector(
        store=state.store,
        registry=context.task_registry,
        resolver=context.project_runtime_resolver,
    )


def get_feature_inspector(request: Request) -> FeatureInspector:
    state = request.app.state
    context = get_runtime_context(request)
    return FeatureInspector(
        store=state.store,
        registry=context.task_registry,
        resolver=context.project_runtime_resolver,
    )


def get_data_library_use_cases(request: Request) -> DataLibraryUseCases:
    state = request.app.state
    context = get_runtime_context(request)
    return DataLibraryUseCases(
        catalog=context.workspace_catalog,
        registry=context.task_registry,
        store=state.store,
        available_packages_paths=state.available_packages_paths,
        personal_available_packages_paths=state.personal_available_packages_paths,
        package_origins=state.model_package_origins,
    )


def get_chain_use_cases(request: Request) -> ChainUseCases:
    state = request.app.state
    context = get_runtime_context(request)
    contribution = get_application_contribution_runtime(
        request, "welding-blend"
    )
    return ChainUseCases(
        store=state.store,
        workspace_catalog=context.workspace_catalog,
        task_registry=context.task_registry,
        planning_use_case=contribution.planning_use_case,
        execution_use_case=contribution.execution_use_case,
        snapshot_use_case=contribution.snapshot_use_case,
        uncertainty_service=contribution.uncertainty_service,
        evaluation_catalog=contribution.evaluation_catalog,
        subsystem_registry=state.subsystem_availability,
    )


def get_prediction_graph_use_cases(request: Request) -> PredictionGraphUseCases:
    contribution = get_application_contribution_runtime(
        request, "welding-blend"
    )
    return contribution.prediction_graph_use_cases


def project_or_404(store: Store, project_id: str) -> Project:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "プロジェクトが見つかりません")
    return project
