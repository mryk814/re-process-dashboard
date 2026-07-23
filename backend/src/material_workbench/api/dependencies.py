from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request

from material_workbench.execution.inference_work_graph import InferenceWorkGraph
from material_workbench.contracts.schemas import Project
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_task_registry(request: Request) -> TaskRegistry:
    return request.app.state.task_registry


def get_workspace_catalog(request: Request) -> WorkspaceCatalog:
    return request.app.state.workspace_catalog


def get_data_library_root(request: Request) -> Path:
    return request.app.state.data_library_root


def get_project_runtime_resolver(request: Request) -> ProjectRuntimeResolver:
    return request.app.state.project_runtime_resolver


def get_inference_work_graph(request: Request) -> InferenceWorkGraph:
    return request.app.state.inference_work_graph


def project_or_404(store: Store, project_id: str) -> Project:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "プロジェクトが見つかりません")
    return project
