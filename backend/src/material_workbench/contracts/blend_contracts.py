"""Versioned sparse-blend, material-master, and design-space contracts.

The candidate keeps only sparse line items and immutable revision references.
Scientific attributes and commercial attributes deliberately live in separate
resources so a price change cannot change the Stage A scientific identity.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _semantic_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class BlendContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RevisionRef(BlendContractModel):
    resource_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class ScientificMaterial(BlendContractModel):
    material_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    material_type: Annotated[str, Field(min_length=1)]
    group: Annotated[str, Field(min_length=1)]
    d50_um: Annotated[float, Field(gt=0, allow_inf_nan=False)]


class ScientificHoop(BlendContractModel):
    hoop_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]


class ScientificMaterialMaster(BlendContractModel):
    schema_version: Literal["scientific-material-master/v1"]
    resource_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    materials: Annotated[tuple[ScientificMaterial, ...], Field(min_length=1)]
    hoops: Annotated[tuple[ScientificHoop, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "ScientificMaterialMaster":
        material_ids = [item.material_id for item in self.materials]
        hoop_ids = [item.hoop_id for item in self.hoops]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("scientific material ids must be unique")
        if len(hoop_ids) != len(set(hoop_ids)):
            raise ValueError("scientific hoop ids must be unique")
        return self

    @property
    def ref(self) -> RevisionRef:
        return RevisionRef(
            resource_id=self.resource_id,
            revision=self.revision,
            digest=_semantic_digest(self.model_dump(mode="json")),
        )


class CommercialMaterial(BlendContractModel):
    material_id: Annotated[str, Field(min_length=1)]
    procurement: Literal["常用", "条件付", "試作限定", "廃止予定"]
    unit_price_yen_per_kg_core: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class CommercialMaterialCatalog(BlendContractModel):
    schema_version: Literal["commercial-material-catalog/v1"]
    resource_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    materials: Annotated[tuple[CommercialMaterial, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def identifiers_are_unique(self) -> "CommercialMaterialCatalog":
        material_ids = [item.material_id for item in self.materials]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("commercial material ids must be unique")
        return self

    @property
    def ref(self) -> RevisionRef:
        return RevisionRef(
            resource_id=self.resource_id,
            revision=self.revision,
            digest=_semantic_digest(self.model_dump(mode="json")),
        )


class BlendItem(BlendContractModel):
    material_id: Annotated[str, Field(min_length=1)]
    ratio: Annotated[float, Field(allow_inf_nan=False)]


class SparseBlend(BlendContractModel):
    """Canonical core blend. Ratios and fill are percentages, not fractions."""

    schema_version: Literal["sparse-blend/v1"] = "sparse-blend/v1"
    items: Annotated[tuple[BlendItem, ...], Field(min_length=1)]
    hoop_id: Annotated[str, Field(min_length=1)]
    fill_ratio: Annotated[float, Field(allow_inf_nan=False)]
    balance_material_id: Annotated[str, Field(min_length=1)]
    scientific_master: RevisionRef
    commercial_catalog: RevisionRef
    design_space: RevisionRef

    @model_validator(mode="after")
    def line_items_are_structurally_valid(self) -> "SparseBlend":
        material_ids = [item.material_id for item in self.items]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("blend material ids must be unique")
        if self.balance_material_id not in material_ids:
            raise ValueError("balance material must be one of the blend items")
        return self

    def model_input_payload(self) -> dict[str, Any]:
        """Return scientific model identity, excluding catalog and editor state."""
        return {
            "schema_version": self.schema_version,
            "items": [
                {"material_id": item.material_id, "ratio": item.ratio}
                for item in sorted(self.items, key=lambda item: item.material_id)
                if item.ratio != 0
            ],
            "hoop_id": self.hoop_id,
            "fill_ratio": self.fill_ratio,
            "scientific_master": self.scientific_master.model_dump(mode="json"),
        }

    @property
    def model_input_digest(self) -> str:
        return _semantic_digest(self.model_input_payload())


class BlendEditorState(BaseModel):
    """Mutable UI state; never part of canonical scientific input."""

    model_config = ConfigDict(extra="forbid")
    locked_material_ids: list[str] = Field(default_factory=list)

    @field_validator("locked_material_ids")
    @classmethod
    def locked_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("locked material ids must be unique")
        return value


class MaterialRatioBound(BlendContractModel):
    material_id: Annotated[str, Field(min_length=1)]
    lower: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    upper: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]

    @model_validator(mode="after")
    def ascending(self) -> "MaterialRatioBound":
        if self.lower > self.upper:
            raise ValueError("material lower bound must not exceed upper bound")
        return self


class GroupTotalConstraint(BlendContractModel):
    group: Annotated[str, Field(min_length=1)]
    lower: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    upper: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]

    @model_validator(mode="after")
    def ascending(self) -> "GroupTotalConstraint":
        if self.lower > self.upper:
            raise ValueError("group lower bound must not exceed upper bound")
        return self


class GroupCardinalityConstraint(BlendContractModel):
    group: Annotated[str, Field(min_length=1)]
    minimum: Annotated[int, Field(ge=0)] = 0
    maximum: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def ascending(self) -> "GroupCardinalityConstraint":
        if self.minimum > self.maximum:
            raise ValueError("group minimum cardinality must not exceed maximum")
        return self


class SelectionCountConstraint(BlendContractModel):
    minimum: Annotated[int, Field(ge=1)]
    maximum: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def ascending(self) -> "SelectionCountConstraint":
        if self.minimum > self.maximum:
            raise ValueError("selection minimum must not exceed maximum")
        return self


class SparseBlendDesignSpace(BlendContractModel):
    schema_version: Literal["sparse-blend-design-space/v1"]
    resource_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    scientific_master: RevisionRef
    commercial_catalog: RevisionRef
    allowed_material_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    material_bounds: tuple[MaterialRatioBound, ...] = ()
    group_totals: tuple[GroupTotalConstraint, ...] = ()
    group_cardinalities: tuple[GroupCardinalityConstraint, ...] = ()
    selection_count: SelectionCountConstraint
    total: Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)] = 100.0
    tolerance: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 1e-6
    fixed_hoop_id: Annotated[str, Field(min_length=1)]
    fixed_fill_ratio: Annotated[float, Field(gt=0, le=100, allow_inf_nan=False)]
    balance_material_id: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def identifiers_are_consistent(self) -> "SparseBlendDesignSpace":
        if len(self.allowed_material_ids) != len(set(self.allowed_material_ids)):
            raise ValueError("allowed material ids must be unique")
        if self.balance_material_id not in self.allowed_material_ids:
            raise ValueError("balance material must be allowed")
        if self.selection_count.maximum > len(self.allowed_material_ids):
            raise ValueError("selection maximum cannot exceed the allowed material count")
        bounds = [item.material_id for item in self.material_bounds]
        if len(bounds) != len(set(bounds)):
            raise ValueError("material bounds must be unique")
        if not set(bounds) <= set(self.allowed_material_ids):
            raise ValueError("material bounds must reference allowed materials")
        groups = [item.group for item in self.group_totals]
        if len(groups) != len(set(groups)):
            raise ValueError("group total constraints must be unique")
        cardinality_groups = [item.group for item in self.group_cardinalities]
        if len(cardinality_groups) != len(set(cardinality_groups)):
            raise ValueError("group cardinality constraints must be unique")
        return self

    @property
    def ref(self) -> RevisionRef:
        return RevisionRef(
            resource_id=self.resource_id,
            revision=self.revision,
            digest=_semantic_digest(self.model_dump(mode="json")),
        )


class BlendValidationIssue(BlendContractModel):
    code: Literal[
        "total",
        "material_not_allowed",
        "material_bounds",
        "group_total",
        "group_cardinality",
        "selection_count",
        "fixed_hoop",
        "fixed_fill",
        "balance_material",
    ]
    path: Annotated[str, Field(min_length=1)]
    message: Annotated[str, Field(min_length=1)]


class BlendValidationState(BlendContractModel):
    status: Literal["not_applicable", "valid", "invalid"] = "not_applicable"
    issues: tuple[BlendValidationIssue, ...] = ()
    design_space_digest: str | None = None

    @model_validator(mode="after")
    def status_matches_issues(self) -> "BlendValidationState":
        if self.status == "invalid" and not self.issues:
            raise ValueError("invalid blend validation requires issues")
        if self.status != "invalid" and self.issues:
            raise ValueError("only invalid blend validation may carry issues")
        if self.status == "not_applicable" and self.design_space_digest is not None:
            raise ValueError("not-applicable validation cannot reference a design space")
        if self.status != "not_applicable" and self.design_space_digest is None:
            raise ValueError("blend validation requires a design space digest")
        return self


class BlendStructuralError(ValueError):
    pass


@dataclass(frozen=True)
class ResolvedBlendContracts:
    scientific_master: ScientificMaterialMaster
    commercial_catalog: CommercialMaterialCatalog
    design_space: SparseBlendDesignSpace


class BlendContractRegistry:
    """Immutable in-memory registry keyed by exact revision references."""

    def __init__(
        self,
        scientific_masters: tuple[ScientificMaterialMaster, ...] = (),
        commercial_catalogs: tuple[CommercialMaterialCatalog, ...] = (),
        design_spaces: tuple[SparseBlendDesignSpace, ...] = (),
    ) -> None:
        if len({item.ref for item in scientific_masters}) != len(scientific_masters):
            raise BlendStructuralError("scientific material master references must be unique")
        if len({item.ref for item in commercial_catalogs}) != len(commercial_catalogs):
            raise BlendStructuralError("commercial material catalog references must be unique")
        if len({item.ref for item in design_spaces}) != len(design_spaces):
            raise BlendStructuralError("sparse blend design-space references must be unique")
        self._scientific = {item.ref: item for item in scientific_masters}
        self._commercial = {item.ref: item for item in commercial_catalogs}
        self._spaces = {item.ref: item for item in design_spaces}
        for space in design_spaces:
            master = self._scientific.get(space.scientific_master)
            catalog = self._commercial.get(space.commercial_catalog)
            if master is None:
                raise BlendStructuralError(
                    f"design space {space.resource_id} references an unknown scientific master"
                )
            if catalog is None:
                raise BlendStructuralError(
                    f"design space {space.resource_id} references an unknown commercial catalog"
                )
            material_ids = {item.material_id for item in master.materials}
            commercial_ids = {item.material_id for item in catalog.materials}
            unknown_allowed = sorted(set(space.allowed_material_ids) - material_ids)
            if unknown_allowed:
                raise BlendStructuralError(
                    f"design space allowed set has unknown material ids: {', '.join(unknown_allowed)}"
                )
            missing_commercial = sorted(set(space.allowed_material_ids) - commercial_ids)
            if missing_commercial:
                raise BlendStructuralError(
                    "design space allowed materials are missing from commercial catalog: "
                    + ", ".join(missing_commercial)
                )
            if space.fixed_hoop_id not in {item.hoop_id for item in master.hoops}:
                raise BlendStructuralError(
                    f"design space fixed hoop is unknown: {space.fixed_hoop_id}"
                )
            known_groups = {item.group for item in master.materials}
            unknown_groups = sorted(
                {
                    *(item.group for item in space.group_totals),
                    *(item.group for item in space.group_cardinalities),
                }
                - known_groups
            )
            if unknown_groups:
                raise BlendStructuralError(
                    f"design space constraints have unknown groups: {', '.join(unknown_groups)}"
                )

    def resolve(self, blend: SparseBlend) -> ResolvedBlendContracts:
        try:
            master = self._scientific[blend.scientific_master]
        except KeyError as exc:
            raise BlendStructuralError("scientific material master revision was not found") from exc
        try:
            catalog = self._commercial[blend.commercial_catalog]
        except KeyError as exc:
            raise BlendStructuralError("commercial material catalog revision was not found") from exc
        try:
            space = self._spaces[blend.design_space]
        except KeyError as exc:
            raise BlendStructuralError("sparse blend design-space revision was not found") from exc
        if space.scientific_master != blend.scientific_master:
            raise BlendStructuralError("design space references a different scientific master")
        if space.commercial_catalog != blend.commercial_catalog:
            raise BlendStructuralError("design space references a different commercial catalog")
        material_ids = {item.material_id for item in master.materials}
        commercial_ids = {item.material_id for item in catalog.materials}
        unknown = sorted({item.material_id for item in blend.items} - material_ids)
        if unknown:
            raise BlendStructuralError(f"unknown material ids: {', '.join(unknown)}")
        missing_commercial = sorted({item.material_id for item in blend.items} - commercial_ids)
        if missing_commercial:
            raise BlendStructuralError(
                f"materials missing from commercial catalog: {', '.join(missing_commercial)}"
            )
        if blend.hoop_id not in {item.hoop_id for item in master.hoops}:
            raise BlendStructuralError(f"unknown hoop id: {blend.hoop_id}")
        return ResolvedBlendContracts(master, catalog, space)


def validate_sparse_blend(
    blend: SparseBlend,
    contracts: ResolvedBlendContracts,
) -> BlendValidationState:
    """Return design-space violations without rejecting the candidate draft."""
    space = contracts.design_space
    master_by_id = {item.material_id: item for item in contracts.scientific_master.materials}
    ratio_by_id = {item.material_id: item.ratio for item in blend.items}
    issues: list[BlendValidationIssue] = []

    def issue(code: str, path: str, message: str) -> None:
        issues.append(BlendValidationIssue(code=code, path=path, message=message))  # type: ignore[arg-type]

    if blend.hoop_id != space.fixed_hoop_id:
        issue("fixed_hoop", "blend.hoop_id", f"フープは {space.fixed_hoop_id} に固定されています")
    if not math.isclose(blend.fill_ratio, space.fixed_fill_ratio, rel_tol=0.0, abs_tol=space.tolerance):
        issue(
            "fixed_fill",
            "blend.fill_ratio",
            f"充填率は {space.fixed_fill_ratio:g}% に固定されています",
        )
    if blend.balance_material_id != space.balance_material_id:
        issue(
            "balance_material",
            "blend.balance_material_id",
            f"残部原料は {space.balance_material_id} に固定されています",
        )

    allowed = set(space.allowed_material_ids)
    for material_id in sorted(set(ratio_by_id) - allowed):
        issue(
            "material_not_allowed",
            f"blend.items[{material_id}]",
            f"{material_id} はこのDesign Spaceで使用できません",
        )

    bounds = {item.material_id: item for item in space.material_bounds}
    for material_id, ratio in sorted(ratio_by_id.items()):
        bound = bounds.get(material_id, MaterialRatioBound(material_id=material_id, lower=0.0, upper=100.0))
        if not bound.lower <= ratio <= bound.upper:
            issue(
                "material_bounds",
                f"blend.items[{material_id}].ratio",
                f"{material_id} は {bound.lower:g}〜{bound.upper:g}% にしてください（現在 {ratio:g}%）",
            )

    actual_total = sum(ratio_by_id.values())
    if not math.isclose(actual_total, space.total, rel_tol=0.0, abs_tol=space.tolerance):
        issue(
            "total",
            "blend.items",
            f"配合比の合計は {space.total:g}% にしてください（現在 {actual_total:g}%）",
        )

    selected = sum(abs(value) > space.tolerance for value in ratio_by_id.values())
    if not space.selection_count.minimum <= selected <= space.selection_count.maximum:
        issue(
            "selection_count",
            "blend.items",
            f"使用原料数は {space.selection_count.minimum}〜{space.selection_count.maximum} 点にしてください"
            f"（現在 {selected} 点）",
        )

    for constraint in space.group_totals:
        value = sum(
            ratio
            for material_id, ratio in ratio_by_id.items()
            if master_by_id[material_id].group == constraint.group
        )
        if not constraint.lower <= value <= constraint.upper:
            issue(
                "group_total",
                f"blend.groups[{constraint.group}]",
                f"{constraint.group}の合計は {constraint.lower:g}〜{constraint.upper:g}% にしてください"
                f"（現在 {value:g}%）",
            )
    for constraint in space.group_cardinalities:
        value = sum(
            abs(ratio) > space.tolerance
            for material_id, ratio in ratio_by_id.items()
            if master_by_id[material_id].group == constraint.group
        )
        if not constraint.minimum <= value <= constraint.maximum:
            issue(
                "group_cardinality",
                f"blend.groups[{constraint.group}]",
                f"{constraint.group}の使用点数は {constraint.minimum}〜{constraint.maximum} 点にしてください"
                f"（現在 {value} 点）",
            )
    return BlendValidationState(
        status="invalid" if issues else "valid",
        issues=tuple(issues),
        design_space_digest=space.ref.digest,
    )
