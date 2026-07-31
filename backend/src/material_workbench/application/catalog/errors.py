"""Catalog use-case errors and small shared lookup helpers."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from material_workbench.persistence.store import Store


class CatalogUseCaseError(ValueError):
    """Base error translated to HTTP only at the API transport boundary."""


class CatalogNotFoundError(CatalogUseCaseError):
    pass


class CatalogValidationError(CatalogUseCaseError):
    pass


class CatalogConflictError(CatalogUseCaseError):
    pass


def require_project(store: Store, project_id: str) -> Any:
    project = store.get_project(project_id)
    if project is None:
        raise CatalogNotFoundError("プロジェクトが見つかりません")
    return project


def lifecycle_profile(data: Any) -> Path | Any:
    profile = getattr(data, "lifecycle_profile", None)
    if profile is not None:
        return profile
    profile_path = Path(data.profile_path)
    return profile_path if profile_path.exists() else data.profile
