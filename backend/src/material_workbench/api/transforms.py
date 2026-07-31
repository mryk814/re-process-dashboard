from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from material_workbench.adapters.builtin_deterministic_linear import (
    DeterministicLinearResult,
)
from material_workbench.api.errors import PROJECT_API_ERRORS
from material_workbench.api.dependencies import get_deterministic_transform_catalog
from material_workbench.contracts.blend_contracts import (
    BlendStructuralError,
    CommercialMaterialCatalog,
    RevisionRef,
    SparseBlend,
    SparseBlendDesignSpace,
)
from material_workbench.modeling.packages.contracts import PackageContractError
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
    design_space_locator: str
    scientific_master: RevisionRef
    commercial_catalog: RevisionRef
    outputs: tuple[str, ...]
    auxiliary_features: tuple[str, ...]


class BlendEditorMaterial(TransformApiModel):
    material_id: str
    name: str
    group: str
    material_type: str
    d50_um: float
    main_components: tuple[str, ...]
    procurement: Literal["常用", "条件付", "試作限定", "廃止予定"]
    unit_price_yen_per_kg_core: float


class BlendEditorContext(TransformApiModel):
    transform_id: str
    scientific_master: RevisionRef
    commercial_catalog: RevisionRef
    design_space_ref: RevisionRef
    design_space: SparseBlendDesignSpace
    materials: tuple[BlendEditorMaterial, ...]


class DeterministicTransformExecutionRequest(TransformApiModel):
    blend: SparseBlend


CatalogDependency = Annotated[
    DeterministicTransformCatalog,
    Depends(get_deterministic_transform_catalog),
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
                design_space_locator=entry.design_space_locator,
                scientific_master=scientific_master,
                commercial_catalog=entry.commercial_catalog.ref,
                outputs=spec.output_names,
                auxiliary_features=spec.auxiliary_feature_names,
            )
        )
    return result


@router.get(
    "/{transform_id}/blend-editor",
    response_model=BlendEditorContext,
    responses=PROJECT_API_ERRORS,
    operation_id="getBlendEditorContext",
)
def get_blend_editor_context(
    transform_id: str,
    catalog: CatalogDependency,
) -> BlendEditorContext:
    try:
        entry = catalog.entry(transform_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    return _editor_context(
        transform_id,
        entry.transform.artifact.scientific_master,
        entry.commercial_catalog,
        entry.design_space,
    )


def _editor_context(
    transform_id: str,
    scientific_master: Any,
    commercial_catalog: CommercialMaterialCatalog,
    design_space: SparseBlendDesignSpace,
) -> BlendEditorContext:
    commercial = {
        material.material_id: material for material in commercial_catalog.materials
    }
    allowed = set(design_space.allowed_material_ids)
    materials = tuple(
        BlendEditorMaterial(
            material_id=material.material_id,
            name=commercial[material.material_id].name or material.material_id,
            group=commercial[material.material_id].group or material.group,
            material_type=(
                commercial[material.material_id].material_type
                or material.group
            ),
            d50_um=material.d50_um,
            main_components=(
                commercial[material.material_id].main_components
                or tuple(
                    name
                    for name, value in sorted(
                        material.composition.items(),
                        key=lambda item: item[1],
                        reverse=True,
                    )
                    if value > 0
                )[:3]
            ),
            procurement=commercial[material.material_id].procurement,
            unit_price_yen_per_kg_core=(
                commercial[material.material_id].unit_price_yen_per_kg_core
            ),
        )
        for material in scientific_master.materials
        if material.material_id in allowed
    )
    return BlendEditorContext(
        transform_id=transform_id,
        scientific_master=scientific_master.ref,
        commercial_catalog=commercial_catalog.ref,
        design_space_ref=design_space.ref,
        design_space=design_space,
        materials=materials,
    )


@router.post(
    "/{transform_id}/blend-editor/resolve",
    response_model=BlendEditorContext,
    responses=PROJECT_API_ERRORS,
    operation_id="resolveBlendEditorContext",
)
def resolve_blend_editor_context(
    transform_id: str,
    payload: DeterministicTransformExecutionRequest,
    catalog: CatalogDependency,
) -> BlendEditorContext:
    try:
        catalog.entry(transform_id)
        contracts = catalog.resolve_blend(payload.blend)
        return _editor_context(
            transform_id,
            contracts.scientific_master,
            contracts.commercial_catalog,
            contracts.design_space,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=exc.args[0]) from exc
    except BlendStructuralError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
    except (BlendStructuralError, PackageContractError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
