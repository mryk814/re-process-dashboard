from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from material_workbench.adapters.builtin_deterministic_linear import (
    DeterministicLinearResult,
)
from material_workbench.api.errors import PROJECT_API_ERRORS
from material_workbench.contracts.blend_contracts import RevisionRef, SparseBlend
from material_workbench.modeling.model_packages import PackageContractError
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog


router = APIRouter(prefix="/api/transforms", tags=["deterministic-transforms"])


class TransformApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeterministicTransformCatalogItem(TransformApiModel):
    transform_id: str
    package_id: str
    package_version: str
    package_manifest_digest: str
    runtime_type: str
    active_locator: str
    available_locators: tuple[str, ...]
    commercial_catalog_locator: str
    scientific_master: RevisionRef
    commercial_catalog: RevisionRef
    outputs: tuple[str, ...]
    auxiliary_features: tuple[str, ...]


class DeterministicTransformExecutionRequest(TransformApiModel):
    blend: SparseBlend


def get_transform_catalog(request: Request) -> DeterministicTransformCatalog:
    return request.app.state.deterministic_transform_catalog


CatalogDependency = Annotated[
    DeterministicTransformCatalog,
    Depends(get_transform_catalog),
]


@router.get(
    "",
    response_model=list[DeterministicTransformCatalogItem],
    responses=PROJECT_API_ERRORS,
    operation_id="listDeterministicTransforms",
)
def list_deterministic_transforms(
    catalog: CatalogDependency,
) -> list[DeterministicTransformCatalogItem]:
    result: list[DeterministicTransformCatalogItem] = []
    for transform_id in catalog.transform_ids:
        entry = catalog.entry(transform_id)
        spec = entry.package.manifest.deterministic_transforms[0]
        scientific_master = entry.transform.artifact.scientific_master.ref
        result.append(
            DeterministicTransformCatalogItem(
                transform_id=transform_id,
                package_id=entry.package.manifest.package_id,
                package_version=entry.package.manifest.package_version,
                package_manifest_digest="sha256:" + entry.package.manifest_sha256,
                runtime_type=spec.runtime_type,
                active_locator=entry.package_locator,
                available_locators=entry.available_package_locators,
                commercial_catalog_locator=entry.commercial_catalog_locator,
                scientific_master=scientific_master,
                commercial_catalog=entry.commercial_catalog.ref,
                outputs=spec.output_names,
                auxiliary_features=spec.auxiliary_feature_names,
            )
        )
    return result


@router.post(
    "/{transform_id}/execute",
    response_model=DeterministicLinearResult,
    responses=PROJECT_API_ERRORS,
    operation_id="executeDeterministicTransform",
)
def execute_deterministic_transform(
    transform_id: str,
    payload: DeterministicTransformExecutionRequest,
    catalog: CatalogDependency,
) -> DeterministicLinearResult:
    try:
        return catalog.execute(transform_id, payload.blend)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except PackageContractError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
