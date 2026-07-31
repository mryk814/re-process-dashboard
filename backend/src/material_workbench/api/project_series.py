from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from .dependencies import get_workspace_catalog
from material_workbench.contracts.data_library_contracts import (
    ProjectSeries,
    ProjectSeriesCreateInput,
    ProjectSeriesUpdateInput,
)
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog


router = APIRouter(prefix="/api/project-series")
CatalogDependency = Annotated[WorkspaceCatalog, Depends(get_workspace_catalog)]


@router.get("", response_model=list[ProjectSeries])
def list_project_series(catalog: CatalogDependency) -> list[ProjectSeries]:
    return catalog.list_project_series()


@router.post("", status_code=201, response_model=ProjectSeries)
def create_project_series(payload: ProjectSeriesCreateInput, catalog: CatalogDependency) -> ProjectSeries:
    return catalog.create_project_series(payload)


@router.get("/{series_id}", response_model=ProjectSeries)
def get_project_series(series_id: str, catalog: CatalogDependency) -> ProjectSeries:
    series = catalog.get_project_series(series_id)
    if series is None:
        raise HTTPException(404, "検討グループが見つかりません")
    return series


@router.put("/{series_id}", response_model=ProjectSeries)
def update_project_series(
    series_id: str, payload: ProjectSeriesUpdateInput, catalog: CatalogDependency
) -> ProjectSeries:
    series = catalog.update_project_series(series_id, payload)
    if series is None:
        raise HTTPException(404, "検討グループが見つかりません")
    return series
