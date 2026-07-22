from __future__ import annotations

from fastapi import HTTPException, Request

from ..schemas import Project
from ..store import Store
from ..task_registry import TaskRegistry


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_task_registry(request: Request) -> TaskRegistry:
    return request.app.state.task_registry


def project_or_404(store: Store, project_id: str) -> Project:
    project = store.get_project(project_id)
    if project is None:
        raise HTTPException(404, "プロジェクトが見つかりません")
    return project
