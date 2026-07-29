from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.contracts.blend_contracts import BlendContractRegistry
from material_workbench.contracts.schemas import Project
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from material_workbench.contracts.subsystem_availability import (
    SubsystemAvailabilityRegistry,
    WELDING_TRANSFORM_SUBSYSTEM_ID,
)
from material_workbench.application.ai_review_provider import AiReviewProvider


def get_runtime_context(request: Request) -> Any:
    """Return one immutable generation of the resources swapped after startup."""

    return getattr(request.app.state, "runtime_context", request.app.state)


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


def get_available_packages_path(request: Request) -> Path:
    return request.app.state.available_packages_path


def get_project_runtime_resolver(request: Request) -> ProjectRuntimeResolver:
    return get_runtime_context(request).project_runtime_resolver


def get_inference_work_graph(request: Request) -> InferenceWorkGraph:
    return request.app.state.inference_work_graph


def get_ai_review_provider(request: Request) -> AiReviewProvider | None:
    return request.app.state.ai_review_provider


def project_or_404(store: Store, project_id: str) -> Project:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "プロジェクトが見つかりません")
    return project
