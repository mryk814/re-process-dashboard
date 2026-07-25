"""Safe deterministic Stage A transform for sparse powder blends.

The package carries a complete scientific-master snapshot. Candidate ratios are
compiled to whole-wire absolute mass fractions before the composition matrix is
applied, so the adapter remains strictly linear in its runtime coordinates.
Commercial prices are supplied separately and never enter the scientific
package digest.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from material_workbench.contracts.blend_contracts import (
    CommercialMaterialCatalog,
    RevisionRef,
    SparseBlend,
)
from material_workbench.modeling.model_packages import (
    DeterministicTransformSpec,
    PackageContractError,
    VerifiedModelPackage,
)


def _semantic_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class _ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class WholeWireCompilerContract(_ArtifactModel):
    id: Literal["sparse_blend_whole_wire.v1"]
    core_ratio_unit: Literal["mass_percent_core"]
    fill_ratio_unit: Literal["mass_percent_whole_wire"]
    absolute_mass_fraction_unit: Literal["mass_fraction_whole_wire"]
    composition_unit: Literal["mass_percent"]


class ScientificMaterialRow(_ArtifactModel):
    material_id: Annotated[str, Field(min_length=1)]
    group: Annotated[str, Field(min_length=1)]
    d50_um: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    composition: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]]


class ScientificHoopRow(_ArtifactModel):
    hoop_id: Annotated[str, Field(min_length=1)]
    composition: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]]


class ScientificTransformMaster(_ArtifactModel):
    schema_version: Literal["stage-a-scientific-master/v1"]
    resource_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    components: Annotated[tuple[str, ...], Field(min_length=1)]
    materials: Annotated[tuple[ScientificMaterialRow, ...], Field(min_length=1)]
    hoops: Annotated[tuple[ScientificHoopRow, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def matrix_is_rectangular(self) -> "ScientificTransformMaster":
        if len(self.components) != len(set(self.components)):
            raise ValueError("scientific component names must be unique")
        material_ids = [item.material_id for item in self.materials]
        hoop_ids = [item.hoop_id for item in self.hoops]
        if len(material_ids) != len(set(material_ids)):
            raise ValueError("scientific material ids must be unique")
        if len(hoop_ids) != len(set(hoop_ids)):
            raise ValueError("scientific hoop ids must be unique")
        expected = set(self.components)
        for row in (*self.materials, *self.hoops):
            if set(row.composition) != expected:
                raise ValueError("scientific composition rows must use the declared component axis")
            total = sum(row.composition.values())
            if not math.isclose(total, 100.0, rel_tol=0.0, abs_tol=1e-3):
                raise ValueError("scientific composition rows must total 100 mass percent")
        return self

    @property
    def ref(self) -> RevisionRef:
        return RevisionRef(
            resource_id=self.resource_id,
            revision=self.revision,
            digest=_semantic_digest(self.model_dump(mode="json")),
        )


class WeightedMeanAuxiliaryFeature(_ArtifactModel):
    name: Annotated[str, Field(min_length=1)]
    source: Literal["d50_um"]
    aggregation: Literal["core_ratio_weighted_mean"]
    included_groups: Annotated[tuple[str, ...], Field(min_length=1)]
    unit: Literal["um"]
    empty_value: Annotated[float, Field(allow_inf_nan=False)]


class DeterministicLinearArtifact(_ArtifactModel):
    schema_version: Literal["deterministic-linear-artifact/v1"]
    compiler: WholeWireCompilerContract
    scientific_master: ScientificTransformMaster
    auxiliary_features: tuple[WeightedMeanAuxiliaryFeature, ...] = ()

    @field_validator("auxiliary_features")
    @classmethod
    def auxiliary_names_are_unique(
        cls,
        value: tuple[WeightedMeanAuxiliaryFeature, ...],
    ) -> tuple[WeightedMeanAuxiliaryFeature, ...]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("auxiliary feature names must be unique")
        return value


class WholeWireCoordinate(_ArtifactModel):
    material_id: str
    mass_fraction: float


class CompiledWholeWireBlend(_ArtifactModel):
    materials: tuple[WholeWireCoordinate, ...]
    hoop_id: str
    hoop_mass_fraction: float


class MaterialCostContribution(_ArtifactModel):
    material_id: str
    core_ratio_percent: float
    unit_price_yen_per_kg_core: float
    contribution_yen_per_kg_core: float
    share_of_blend_cost: float


class ScientificTransformResult(_ArtifactModel):
    material_composition: dict[str, float]
    auxiliary_features: dict[str, float]
    whole_wire_coordinates: CompiledWholeWireBlend
    scientific_master: RevisionRef


class DeterministicLinearResult(ScientificTransformResult):
    material_cost_contributions: tuple[MaterialCostContribution, ...]
    powder_blend_cost_yen_per_kg_core: float
    commercial_catalog: RevisionRef


class _BuiltinDeterministicLinearTransform:
    def __init__(
        self,
        spec: DeterministicTransformSpec,
        artifact: DeterministicLinearArtifact,
    ) -> None:
        self.spec = spec
        self.artifact = artifact
        self._materials = {
            material.material_id: material for material in artifact.scientific_master.materials
        }
        self._hoops = {hoop.hoop_id: hoop for hoop in artifact.scientific_master.hoops}

    def compile(self, blend: SparseBlend) -> CompiledWholeWireBlend:
        if blend.scientific_master != self.artifact.scientific_master.ref:
            raise PackageContractError("candidate scientific master does not match Stage A package")
        unknown = sorted({item.material_id for item in blend.items} - set(self._materials))
        if unknown:
            raise PackageContractError(f"unknown Stage A material ids: {', '.join(unknown)}")
        if blend.hoop_id not in self._hoops:
            raise PackageContractError(f"unknown Stage A hoop id: {blend.hoop_id}")
        if not 0 < blend.fill_ratio <= 100:
            raise PackageContractError("Stage A fill ratio must be in (0, 100]")
        total = sum(item.ratio for item in blend.items)
        if not math.isclose(total, 100.0, rel_tol=0.0, abs_tol=1e-6):
            raise PackageContractError("Stage A core ratios must total 100 mass percent")
        if any(item.ratio < 0 for item in blend.items):
            raise PackageContractError("Stage A core ratios must be nonnegative")

        fill = blend.fill_ratio / 100.0
        coordinates = tuple(
            WholeWireCoordinate(
                material_id=item.material_id,
                mass_fraction=fill * item.ratio / 100.0,
            )
            for item in sorted(blend.items, key=lambda current: current.material_id)
            if item.ratio != 0
        )
        return CompiledWholeWireBlend(
            materials=coordinates,
            hoop_id=blend.hoop_id,
            hoop_mass_fraction=1.0 - fill,
        )

    def transform(self, blend: SparseBlend) -> ScientificTransformResult:
        coordinates = self.compile(blend)
        composition = {name: 0.0 for name in self.artifact.scientific_master.components}
        for coordinate in coordinates.materials:
            row = self._materials[coordinate.material_id]
            for component in composition:
                composition[component] += coordinate.mass_fraction * row.composition[component]
        hoop = self._hoops[coordinates.hoop_id]
        for component in composition:
            composition[component] += (
                coordinates.hoop_mass_fraction * hoop.composition[component]
            )

        auxiliary: dict[str, float] = {}
        for contract in self.artifact.auxiliary_features:
            eligible = [
                item
                for item in blend.items
                if self._materials[item.material_id].group in contract.included_groups
                and item.ratio != 0
            ]
            denominator = sum(item.ratio for item in eligible)
            auxiliary[contract.name] = (
                sum(
                    item.ratio * self._materials[item.material_id].d50_um
                    for item in eligible
                )
                / denominator
                if denominator
                else contract.empty_value
            )

        return ScientificTransformResult(
            material_composition=composition,
            auxiliary_features=auxiliary,
            whole_wire_coordinates=coordinates,
            scientific_master=self.artifact.scientific_master.ref,
        )

    def execute(
        self,
        blend: SparseBlend,
        commercial_catalog: CommercialMaterialCatalog,
    ) -> DeterministicLinearResult:
        scientific = self.transform(blend)
        if commercial_catalog.ref != blend.commercial_catalog:
            raise PackageContractError("commercial catalog does not match candidate revision")
        prices = {
            item.material_id: item.unit_price_yen_per_kg_core
            for item in commercial_catalog.materials
        }
        material_ids = {item.material_id for item in scientific.whole_wire_coordinates.materials}
        missing_prices = sorted(material_ids - set(prices))
        if missing_prices:
            raise PackageContractError(
                "commercial catalog is missing Stage A materials: " + ", ".join(missing_prices)
            )
        ratio_by_id = {item.material_id: item.ratio for item in blend.items}
        raw_costs = {
            material_id: ratio / 100.0 * prices[material_id]
            for material_id, ratio in ratio_by_id.items()
            if ratio != 0
        }
        total_cost = sum(raw_costs.values())
        costs = tuple(
            MaterialCostContribution(
                material_id=material_id,
                core_ratio_percent=ratio_by_id[material_id],
                unit_price_yen_per_kg_core=prices[material_id],
                contribution_yen_per_kg_core=raw_costs[material_id],
                share_of_blend_cost=raw_costs[material_id] / total_cost if total_cost else 0.0,
            )
            for material_id in sorted(raw_costs)
        )
        return DeterministicLinearResult(
            **scientific.model_dump(mode="python"),
            material_cost_contributions=costs,
            powder_blend_cost_yen_per_kg_core=total_cost,
            commercial_catalog=commercial_catalog.ref,
        )


class BuiltinDeterministicLinearAdapter:
    runtime_type = "builtin.deterministic_linear.v1"

    def load_transform(
        self,
        package: VerifiedModelPackage,
        transform: DeterministicTransformSpec,
    ) -> _BuiltinDeterministicLinearTransform:
        try:
            artifact = DeterministicLinearArtifact.model_validate_json(
                package.artifact_path(transform.artifact).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise PackageContractError(
                f"invalid deterministic-linear artifact: {exc}"
            ) from exc
        master_ref = artifact.scientific_master.ref
        if master_ref.digest != transform.scientific_master_digest:
            raise PackageContractError(
                "deterministic-linear scientific master digest does not match manifest"
            )
        if artifact.compiler.id != transform.compiler_id:
            raise PackageContractError(
                "deterministic-linear compiler differs between manifest and artifact"
            )
        if artifact.scientific_master.components != transform.output_names:
            raise PackageContractError(
                "deterministic-linear output order differs between manifest and artifact"
            )
        auxiliary_names = tuple(item.name for item in artifact.auxiliary_features)
        if auxiliary_names != transform.auxiliary_feature_names:
            raise PackageContractError(
                "deterministic-linear auxiliary features differ between manifest and artifact"
            )
        return _BuiltinDeterministicLinearTransform(transform, artifact)
