"""Contracts for the deliberately bounded Stage A blend optimization."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from material_workbench.contracts.blend_contracts import (
    BlendMaterialDescriptor,
    GroupCardinalityConstraint,
    GroupTotalConstraint,
    MaterialRatioBound,
    RevisionRef,
    SelectionCountConstraint,
)
from material_workbench.contracts.schemas import Candidate


class OptimizationContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CompositionTarget(OptimizationContractModel):
    component: Annotated[str, Field(min_length=1)]
    lower: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
    upper: Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]

    @model_validator(mode="after")
    def ascending(self) -> "CompositionTarget":
        if self.lower > self.upper:
            raise ValueError("composition target lower must not exceed upper")
        return self


class BlendOptimizationRequest(OptimizationContractModel):
    expected_revision: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=80)] = "配合逆算候補"
    objective: Literal["cost", "baseline_l1"]
    inclusion_decisions: bool = False
    material_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    composition_targets: Annotated[tuple[CompositionTarget, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_inputs(self) -> "BlendOptimizationRequest":
        if len(self.material_ids) != len(set(self.material_ids)):
            raise ValueError("optimization material ids must be unique")
        components = [target.component for target in self.composition_targets]
        if len(components) != len(set(components)):
            raise ValueError("composition target components must be unique")
        return self


class BlendOptimizationContext(OptimizationContractModel):
    baseline_candidate_id: str
    baseline_candidate_revision: int
    fixed_hoop_id: str
    fixed_fill_ratio: float
    balance_material_id: str
    scientific_master: RevisionRef
    commercial_catalog: RevisionRef
    design_space: RevisionRef
    materials: tuple[BlendMaterialDescriptor, ...]
    components: tuple[str, ...]
    material_bounds: tuple[MaterialRatioBound, ...]
    group_totals: tuple[GroupTotalConstraint, ...]
    group_cardinalities: tuple[GroupCardinalityConstraint, ...]
    selection_count: SelectionCountConstraint


class RelaxationCandidate(OptimizationContractModel):
    constraint: str
    direction: Literal["lower", "upper", "structural"]
    current: float | None = None
    suggested: float | None = None
    amount: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    unit: str
    message: str


class BlendOptimizationResult(OptimizationContractModel):
    status: Literal["feasible", "infeasible"]
    solver_name: str
    solver_version: str
    method: Literal["highs-lp", "highs-milp"]
    objective: Literal["cost", "baseline_l1"]
    objective_value: float | None = None
    objective_unit: Literal["yen/kg-core", "core mass %"]
    candidate: Candidate | None = None
    relaxation_candidates: tuple[RelaxationCandidate, ...] = ()
    message: str

    @model_validator(mode="after")
    def status_matches_payload(self) -> "BlendOptimizationResult":
        if self.status == "feasible" and self.candidate is None:
            raise ValueError("feasible optimization requires a candidate")
        if self.status == "infeasible" and self.candidate is not None:
            raise ValueError("infeasible optimization cannot create a candidate")
        return self
