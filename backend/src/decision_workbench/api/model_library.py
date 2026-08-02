"""Read-only Workspace Model Library transport."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from decision_workbench.application.model_library import (
    ModelLibraryCatalogService,
)
from decision_workbench.contracts.model_library_contracts import (
    ModelLibraryCatalog,
)

from .dependencies import get_model_library_catalog_service


router = APIRouter(prefix="/api/model-library", tags=["model-library"])
ServiceDependency = Annotated[
    ModelLibraryCatalogService,
    Depends(get_model_library_catalog_service),
]


@router.get(
    "",
    response_model=ModelLibraryCatalog,
    operation_id="getModelLibraryCatalog",
)
def get_model_library_catalog(
    service: ServiceDependency,
) -> ModelLibraryCatalog:
    return service.catalog()
