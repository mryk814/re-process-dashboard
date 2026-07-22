"""Data-only contracts for task definitions and canonical candidate fixtures.

This module deliberately has no API, registry, persistence, or UI integration.
It defines the contract that those production layers will adopt in later issues.
"""
from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, field_validator, model_validator


TASK_DEFINITION_SCHEMA_VERSION = "task-definition/v1"
CANONICAL_CANDIDATE_SCHEMA_VERSION = "canonical-candidate/v1"
RUNTIME_CAPABILITY_SCHEMA_VERSION = "runtime-capability/v1"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class NumericRange(ContractModel):
    min: float
    max: float

    @model_validator(mode="after")
    def finite_ascending_range(self) -> "NumericRange":
        if not math.isfinite(self.min) or not math.isfinite(self.max):
            raise ValueError("range bounds must be finite")
        if self.min >= self.max:
            raise ValueError("range min must be less than max")
        return self

    def contains(self, other: "NumericRange") -> bool:
        return self.min <= other.min and other.max <= self.max


class InputFieldDefinition(ContractModel):
    path: Annotated[str, Field(min_length=1, pattern=r"^(composition|process|categorical)\.[A-Za-z][A-Za-z0-9_]*$|^heat_pattern$")]
    kind: Literal["number", "categorical", "heat_pattern"]
    order: Annotated[int, Field(ge=0)]
    label: Annotated[str, Field(min_length=1)]
    unit: str | None = None
    required: bool = True
    editable: bool = True
    default_range: NumericRange | None = None
    allowed_range: NumericRange | None = None
    training_range: NumericRange | None = None
    choices: tuple[str, ...] = ()

    @model_validator(mode="after")
    def field_shape_matches_kind(self) -> "InputFieldDefinition":
        ranges = (self.default_range, self.allowed_range, self.training_range)
        if self.kind == "number":
            if any(value is None for value in ranges):
                raise ValueError("number fields require default, allowed, and training ranges")
            if self.choices:
                raise ValueError("number fields cannot declare choices")
            assert self.allowed_range is not None
            assert self.default_range is not None
            assert self.training_range is not None
            if not self.allowed_range.contains(self.default_range):
                raise ValueError("default_range must be contained by allowed_range")
            if not self.allowed_range.contains(self.training_range):
                raise ValueError("training_range must be contained by allowed_range")
        elif self.kind == "categorical":
            if any(value is not None for value in ranges):
                raise ValueError("categorical fields cannot declare numeric ranges")
            if not self.choices or len(set(self.choices)) != len(self.choices):
                raise ValueError("categorical fields require unique choices")
            if self.unit is not None:
                raise ValueError("categorical fields cannot declare a unit")
        else:
            if self.path != "heat_pattern":
                raise ValueError("heat_pattern fields must use path=heat_pattern")
            if any(value is not None for value in ranges) or self.choices or self.unit is not None:
                raise ValueError("heat_pattern fields cannot declare scalar ranges, choices, or a unit")
        return self


class InputGroupDefinition(ContractModel):
    key: Literal["composition", "process", "heat_pattern", "categorical"]
    order: Annotated[int, Field(ge=0)]
    label: Annotated[str, Field(min_length=1)]
    fields: Annotated[tuple[InputFieldDefinition, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def fields_belong_to_group(self) -> "InputGroupDefinition":
        paths = [field.path for field in self.fields]
        orders = [field.order for field in self.fields]
        if len(paths) != len(set(paths)):
            raise ValueError("field paths must be unique within a group")
        if len(orders) != len(set(orders)):
            raise ValueError("field order must be unique within a group")
        expected_prefix = f"{self.key}."
        for field in self.fields:
            if self.key == "heat_pattern":
                if field.kind != "heat_pattern" or field.path != "heat_pattern":
                    raise ValueError("heat_pattern group only accepts the heat_pattern field")
            elif not field.path.startswith(expected_prefix):
                raise ValueError(f"field path {field.path} does not belong to group {self.key}")
            elif self.key == "categorical" and field.kind != "categorical":
                raise ValueError("categorical group only accepts categorical fields")
            elif self.key in {"composition", "process"} and field.kind != "number":
                raise ValueError(f"{self.key} group only accepts number fields")
        return self


class OutputDefinition(ContractModel):
    key: Annotated[str, Field(min_length=1, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")]
    label: Annotated[str, Field(min_length=1)]
    unit: Annotated[str, Field(min_length=1)]
    goal_direction: Literal["at_least", "at_most", "target"]
    measurement_keys: tuple[Annotated[str, Field(min_length=1)], ...] = ()


class FixedContextDefinition(ContractModel):
    path: Annotated[str, Field(min_length=1, pattern=r"^context\.[A-Za-z][A-Za-z0-9_]*$")]
    order: Annotated[int, Field(ge=0)]
    label: Annotated[str, Field(min_length=1)]
    value: str | float | int | bool


class RelationalConstraint(ContractModel):
    left_path: Annotated[str, Field(min_length=1)]
    operator: Literal["lt", "lte", "gt", "gte"]
    right_path: Annotated[str, Field(min_length=1)]
    message: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def compares_distinct_fields(self) -> "RelationalConstraint":
        if self.left_path == self.right_path:
            raise ValueError("a relational constraint must compare distinct fields")
        return self


class ResponseCurveVariableDefinition(ContractModel):
    kind: Literal["numeric_input", "heat_stage_temperature"]
    order: Annotated[int, Field(ge=0)]
    label: Annotated[str, Field(min_length=1)]
    path: str | None = None
    time_transform: Literal["direct", "inverse_heat_time"] = "direct"

    @model_validator(mode="after")
    def shape_matches_kind(self) -> "ResponseCurveVariableDefinition":
        if self.kind == "numeric_input" and not self.path:
            raise ValueError("numeric response-curve variables require a path")
        if self.kind == "heat_stage_temperature" and self.path is not None:
            raise ValueError("heat-stage response-curve variables do not use a scalar path")
        if self.kind == "heat_stage_temperature" and self.time_transform != "direct":
            raise ValueError("heat-stage response-curve variables cannot transform time")
        return self


class TaskDefinition(ContractModel):
    schema_version: Literal[TASK_DEFINITION_SCHEMA_VERSION]
    id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    canonical_candidate_schema_version: Literal[CANONICAL_CANDIDATE_SCHEMA_VERSION]
    input_groups: Annotated[tuple[InputGroupDefinition, ...], Field(min_length=1)]
    outputs: Annotated[tuple[OutputDefinition, ...], Field(min_length=1)]
    display_decimals: dict[str, Annotated[int, Field(ge=0, le=8)]]
    fixed_context: tuple[FixedContextDefinition, ...] = ()
    constraints: tuple[RelationalConstraint, ...] = ()
    response_curve_variables: tuple[ResponseCurveVariableDefinition, ...] = ()
    # 予測対象が「この軸に沿った曲線」であるタスクだけが宣言する。宣言された
    # フィールドは候補入力上は1点の評価位置であり、曲線APIの横軸になる。
    curve_axis_path: str | None = None

    @model_validator(mode="after")
    def unique_groups_fields_and_outputs(self) -> "TaskDefinition":
        group_keys = [group.key for group in self.input_groups]
        group_orders = [group.order for group in self.input_groups]
        if len(group_keys) != len(set(group_keys)):
            raise ValueError("input group keys must be unique")
        if len(group_orders) != len(set(group_orders)):
            raise ValueError("input group order must be unique")
        if "composition" not in group_keys:
            raise ValueError("all material prediction tasks require a composition group")
        paths = [field.path for group in self.input_groups for field in group.fields]
        if len(paths) != len(set(paths)):
            raise ValueError("field paths must be unique across the task definition")
        output_keys = [output.key for output in self.outputs]
        if len(output_keys) != len(set(output_keys)):
            raise ValueError("output keys must be unique")
        display_keys = {
            *(field.path for group in self.input_groups for field in group.fields if field.kind == "number"),
            *(f"output.{output.key}" for output in self.outputs),
        }
        if set(self.display_decimals) != display_keys:
            raise ValueError("display_decimals must define every numeric input and output exactly once")
        context_paths = [item.path for item in self.fixed_context]
        context_orders = [item.order for item in self.fixed_context]
        if len(context_paths) != len(set(context_paths)):
            raise ValueError("fixed context paths must be unique")
        if len(context_orders) != len(set(context_orders)):
            raise ValueError("fixed context order must be unique")
        field_by_path = {field.path: field for group in self.input_groups for field in group.fields}
        response_orders = [item.order for item in self.response_curve_variables]
        if len(response_orders) != len(set(response_orders)):
            raise ValueError("response-curve variable order must be unique")
        if sum(item.kind == "heat_stage_temperature" for item in self.response_curve_variables) > 1:
            raise ValueError("heat-stage response-curve variable can only be declared once")
        for item in self.response_curve_variables:
            if item.kind == "numeric_input":
                field = field_by_path.get(item.path or "")
                if field is None or field.kind != "number" or not field.editable:
                    raise ValueError("numeric response-curve variables must reference editable number fields")
            elif not any(group.key == "heat_pattern" for group in self.input_groups):
                raise ValueError("heat-stage response-curve variables require a heat_pattern group")
        if self.curve_axis_path is not None:
            axis = field_by_path.get(self.curve_axis_path)
            if axis is None or axis.kind != "number" or not axis.required or not axis.editable:
                raise ValueError("curve_axis_path must reference a required, editable number field")
        for constraint in self.constraints:
            referenced = (constraint.left_path, constraint.right_path)
            if any(path not in field_by_path for path in referenced):
                raise ValueError("relational constraints must reference declared fields")
            if any(field_by_path[path].kind != "number" for path in referenced):
                raise ValueError("relational constraints can only compare number fields")
            if any(not field_by_path[path].required for path in referenced):
                raise ValueError("relational constraints must reference required fields")
        return self


class ManualSourceRef(ContractModel):
    source_kind: Literal["manual"]
    source_ref: None = None


class DirectSourceRef(ContractModel):
    source_kind: Literal["direct"]
    source_ref: None = None


class LineageReference(ContractModel):
    entity_type: Annotated[str, Field(min_length=1)]
    entity_key: Annotated[str, Field(min_length=1)]
    data_source_digest: Annotated[str, Field(min_length=1)] | None = None


class LineageSourceRef(ContractModel):
    source_kind: Literal["lineage"]
    source_ref: LineageReference


class ScreeningReference(ContractModel):
    run_id: Annotated[str, Field(min_length=1)]
    point_id: Annotated[str, Field(min_length=1)]
    point_index: Annotated[int, Field(ge=0)] | None = None


class ScreeningSourceRef(ContractModel):
    source_kind: Literal["screening"]
    source_ref: ScreeningReference


class SnapshotReference(ContractModel):
    snapshot_id: Annotated[str, Field(min_length=1)]


class SnapshotSourceRef(ContractModel):
    source_kind: Literal["snapshot"]
    source_ref: SnapshotReference


class CopyReference(ContractModel):
    project_id: Annotated[str, Field(min_length=1)]
    candidate_id: Annotated[str, Field(min_length=1)]
    candidate_revision: Annotated[int, Field(ge=1)]


class CopySourceRef(ContractModel):
    source_kind: Literal["copy"]
    source_ref: CopyReference


CandidateProvenance = Annotated[
    ManualSourceRef | DirectSourceRef | LineageSourceRef | ScreeningSourceRef | SnapshotSourceRef | CopySourceRef,
    Field(discriminator="source_kind"),
]


class CanonicalHeatPoint(ContractModel):
    time_s: Annotated[StrictFloat, Field(ge=0, allow_inf_nan=False)]
    temperature_c: Annotated[StrictFloat, Field(ge=-273.15, le=1800, allow_inf_nan=False)]


class CanonicalCandidate(ContractModel):
    schema_version: Literal[CANONICAL_CANDIDATE_SCHEMA_VERSION]
    task_id: Annotated[str, Field(min_length=1)]
    composition: dict[str, StrictFloat]
    process: dict[str, StrictFloat] = Field(default_factory=dict)
    heat_pattern: tuple[CanonicalHeatPoint, ...] | None = None
    categorical: dict[str, str] = Field(default_factory=dict)
    provenance: CandidateProvenance

    @field_validator("composition", "process")
    @classmethod
    def finite_numeric_values(cls, value: dict[str, float]) -> dict[str, float]:
        if any(not math.isfinite(item) for item in value.values()):
            raise ValueError("canonical numeric values must be finite")
        return value

    @model_validator(mode="after")
    def heat_pattern_time_is_strictly_increasing(self) -> "CanonicalCandidate":
        if self.heat_pattern is not None:
            if len(self.heat_pattern) < 2:
                raise ValueError("heat_pattern requires at least two points")
            times = [point.time_s for point in self.heat_pattern]
            if any(later <= earlier for earlier, later in zip(times, times[1:])):
                raise ValueError("heat_pattern time must be strictly increasing")
        return self


class TargetRuntimeCapability(ContractModel):
    target: Annotated[str, Field(min_length=1)]
    point_statistics: Annotated[
        tuple[Literal["mean", "median", "probability", "rate", "expected_category"], ...],
        Field(min_length=1),
    ]
    standard_deviation: bool
    quantiles: bool
    samples: bool
    parametric_distribution: bool
    uncertainty_components: bool
    support: bool
    warnings: bool
    goal_probability: Literal["native", "samples", "distribution", "normal_approximation", "unavailable"]

    @field_validator("point_statistics")
    @classmethod
    def unique_point_statistics(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("point_statistics must be unique")
        return value

    @model_validator(mode="after")
    def goal_probability_has_required_representation(self) -> "TargetRuntimeCapability":
        if self.goal_probability == "samples" and not self.samples:
            raise ValueError("sample-based goal probability requires samples")
        if self.goal_probability == "distribution" and not self.parametric_distribution:
            raise ValueError("distribution-based goal probability requires a parametric distribution")
        if self.goal_probability == "normal_approximation" and (
            "mean" not in self.point_statistics or not self.standard_deviation
        ):
            raise ValueError("normal approximation requires mean and standard deviation")
        return self


class RuntimeOperationsCapability(ContractModel):
    preview: bool
    detailed_prediction: bool
    response_curve: bool
    similarity: bool
    snapshot: bool
    actual_measurement: bool


class RuntimeCapability(ContractModel):
    schema_version: Literal[RUNTIME_CAPABILITY_SCHEMA_VERSION]
    task_id: Annotated[str, Field(min_length=1)]
    model_package_schema_version: Literal["model-package/v1"]
    targets: Annotated[tuple[TargetRuntimeCapability, ...], Field(min_length=1)]
    joint_samples: bool = False
    operations: RuntimeOperationsCapability

    @model_validator(mode="after")
    def targets_are_unique(self) -> "RuntimeCapability":
        targets = [item.target for item in self.targets]
        if len(targets) != len(set(targets)):
            raise ValueError("runtime capability targets must be unique")
        if self.joint_samples and any(not item.samples for item in self.targets):
            raise ValueError("joint_samples requires sample capability for every target")
        return self


class DataExplorerCapability(ContractModel):
    schema_version: Literal["data-explorer-capability/v1"] = "data-explorer-capability/v1"
    quality: bool
    lineage: bool
    candidate_creation: bool

    @model_validator(mode="after")
    def candidate_creation_requires_lineage(self) -> "DataExplorerCapability":
        if self.candidate_creation and not self.lineage:
            raise ValueError("candidate_creation requires lineage")
        return self


class ResolvedTaskDefinition(ContractModel):
    task_definition: TaskDefinition
    runtime_capability: RuntimeCapability
    data_explorer: DataExplorerCapability | None = None

    @model_validator(mode="after")
    def task_ids_match(self) -> "ResolvedTaskDefinition":
        if self.task_definition.id != self.runtime_capability.task_id:
            raise ValueError("resolved task definition and runtime capability must share task_id")
        return self


class TaskContractFixture(ContractModel):
    """Self-consistent fixture used to freeze a task contract before integration."""

    task_definition: TaskDefinition
    canonical_candidate: CanonicalCandidate
    runtime_capability: RuntimeCapability

    @model_validator(mode="after")
    def contracts_are_consistent(self) -> "TaskContractFixture":
        task = self.task_definition
        candidate = self.canonical_candidate
        capability = self.runtime_capability
        if candidate.task_id != task.id or capability.task_id != task.id:
            raise ValueError("task_id must match across all contracts")
        expected_targets = {output.key for output in task.outputs}
        actual_targets = {target.target for target in capability.targets}
        if actual_targets != expected_targets:
            raise ValueError("runtime capability targets must exactly match task outputs")

        groups = {group.key: group for group in task.input_groups}
        candidate_values: dict[str, Any] = {
            "composition": candidate.composition,
            "process": candidate.process,
            "heat_pattern": candidate.heat_pattern,
            "categorical": candidate.categorical,
        }
        for group_key, values in candidate_values.items():
            if group_key not in groups:
                if values not in ({}, None):
                    raise ValueError(f"candidate provides undeclared group: {group_key}")
                continue
            group = groups[group_key]
            if group_key == "heat_pattern":
                if any(field.required for field in group.fields) and values is None:
                    raise ValueError("candidate is missing required heat_pattern")
                continue
            assert isinstance(values, dict)
            declared = {field.path.split(".", 1)[1]: field for field in group.fields}
            unknown = set(values) - set(declared)
            if unknown:
                raise ValueError(f"candidate has undeclared fields in {group_key}: {sorted(unknown)}")
            missing = {name for name, field in declared.items() if field.required} - set(values)
            if missing:
                raise ValueError(f"candidate is missing required fields in {group_key}: {sorted(missing)}")
            for name, value in values.items():
                field = declared[name]
                if field.kind == "number":
                    assert field.allowed_range is not None
                    if not field.allowed_range.min <= value <= field.allowed_range.max:
                        raise ValueError(f"candidate value is outside allowed_range: {field.path}")
                elif value not in field.choices:
                    raise ValueError(f"candidate value is not an allowed choice: {field.path}")
        flat_values = {
            f"composition.{key}": value for key, value in candidate.composition.items()
        } | {
            f"process.{key}": value for key, value in candidate.process.items()
        }
        operators = {
            "lt": lambda left, right: left < right,
            "lte": lambda left, right: left <= right,
            "gt": lambda left, right: left > right,
            "gte": lambda left, right: left >= right,
        }
        for constraint in task.constraints:
            if not operators[constraint.operator](
                flat_values[constraint.left_path], flat_values[constraint.right_path]
            ):
                raise ValueError(constraint.message)
        return self
