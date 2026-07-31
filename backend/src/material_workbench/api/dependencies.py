from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.contracts.blend_contracts import BlendContractRegistry
from material_workbench.contracts.candidate_project_contracts import Project
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.application.project_runtime import ProjectRuntimeResolver
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from material_workbench.contracts.subsystem_availability import (
    SubsystemAvailabilityRegistry,
    WELDING_TRANSFORM_SUBSYSTEM_ID,
)
from material_workbench.application.ai_review_provider import AiReviewProvider
from material_workbench.application.catalog import CatalogRuntimeState, CatalogUseCases
from material_workbench.application.data_library import DataLibraryUseCases
from material_workbench.application.chains import ChainUseCases


def get_runtime_context(request: Request) -> Any:
    """Return one immutable generation of the resources swapped after startup."""

    return getattr(
        request.state,
        "runtime_context",
        getattr(request.app.state, "runtime_context", request.app.state),
    )


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_task_registry(request: Request) -> TaskRegistry:
    return get_runtime_context(request).task_registry


def get_blend_contract_registry(request: Request) -> BlendContractRegistry:
    return request.app.state.blend_contract_registry


def get_deterministic_transform_catalog(
    request: Request,
) -> DeterministicTransformCatalog:
    request.app.state.subsystem_availability.require(
        WELDING_TRANSFORM_SUBSYSTEM_ID
    )
    catalog = request.app.state.deterministic_transform_catalog
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


def get_catalog_use_cases(request: Request) -> CatalogUseCases:
    state = request.app.state
    context = get_runtime_context(request)
    return CatalogUseCases(
        state=CatalogRuntimeState(
            resources_ready=bool(getattr(state, "resources_ready", True)),
            resources_loading_error=getattr(state, "resources_loading_error", None),
            workspace_database=state.workspace_database,
            data_library_root=state.data_library_root,
            workspace_kind=state.workspace_kind,
        ),
        store=state.store,
        registry=context.task_registry,
        resolver=context.project_runtime_resolver,
        subsystem_registry=state.subsystem_availability,
        transform_catalog=state.deterministic_transform_catalog,
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
    return ChainUseCases(
        store=state.store,
        workspace_catalog=context.workspace_catalog,
        execution_service=context.chain_execution_service,
        uncertainty_service=context.chain_uncertainty_service,
        evaluation_catalog=state.chain_evaluation_catalog,
        subsystem_registry=state.subsystem_availability,
    )


def project_or_404(store: Store, project_id: str) -> Project:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "プロジェクトが見つかりません")
    return project
