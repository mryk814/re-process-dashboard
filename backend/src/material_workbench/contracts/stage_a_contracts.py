"""Canonical Stage A axes and scientific-only sparse blend input."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from material_workbench.contracts.blend_contracts import (
    BlendContractModel,
    BlendItem,
    RevisionRef,
    SparseBlend,
)


STAGE_A_COMPONENTS = (
    "Fe", "C", "Si", "Mn", "Cr", "Ni", "Mo", "Ti", "B", "Al",
    "Mg", "Nb", "V", "Cu", "Zr", "Ca", "N", "O", "S", "P",
    "CaF2", "TiO2", "SiO2", "Al2O3", "MgO", "ZrO2", "K2O", "Na2O",
    "CaCO3", "Fe2O3", "other",
)
STAGE_A_SOURCE_COMPONENT_COLUMNS = tuple(
    f"{'その他' if component == 'other' else component}[%]"
    for component in STAGE_A_COMPONENTS
)
STAGE_A_HOOP_SOURCE_COMPONENT_COLUMNS = tuple(
    f"{component}[%]" for component in STAGE_A_COMPONENTS[:20]
)
STAGE_A_COMPONENT_OUTPUT_UNIT = "mass% whole wire"
STAGE_A_AUXILIARY_SOURCE_PRESENTATION = {
    "d50_um": ("合金粉末 D50", "µm", 1),
}


class ScientificBlendInput(BlendContractModel):
    """Scientific Stage A input; excludes commercial and Design Space identity."""

    schema_version: Literal["stage-a-scientific-input/v1"] = "stage-a-scientific-input/v1"
    items: Annotated[tuple[BlendItem, ...], Field(min_length=1)]
    hoop_id: Annotated[str, Field(min_length=1)]
    fill_ratio: Annotated[float, Field(allow_inf_nan=False)]
    scientific_master: RevisionRef

    @classmethod
    def from_sparse_blend(cls, blend: SparseBlend) -> "ScientificBlendInput":
        return cls(
            items=blend.items,
            hoop_id=blend.hoop_id,
            fill_ratio=blend.fill_ratio,
            scientific_master=blend.scientific_master,
        )
