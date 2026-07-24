"""Immutable, task-bounded definitions for proposal design spaces."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.task_contracts import ContractModel, NumericRange, TaskDefinition
from material_workbench.execution.inference_work_graph import semantic_digest


class NumericDomain(ContractModel):
    path: Annotated[str, Field(min_length=1)]
    mode: Literal["range", "values"]
    range: NumericRange | None = None
    values: tuple[float, ...] = ()

    @model_validator(mode="after")
    def complete(self) -> "NumericDomain":
        if self.mode == "range" and (self.range is None or self.values):
            raise ValueError("range domain requires only range")
        if self.mode == "values" and (not self.values or self.range is not None):
            raise ValueError("values domain requires only values")
        return self


class CategoricalDomain(ContractModel):
    path: Annotated[str, Field(min_length=1)]
    choices: Annotated[tuple[str, ...], Field(min_length=1)]


class ConditionalActivation(ContractModel):
    controller_path: str
    active_choices: Annotated[tuple[str, ...], Field(min_length=1)]
    inactive_values: Annotated[dict[str, float | str], Field(min_length=1)]


class CompositionTotalConstraint(ContractModel):
    component_paths: Annotated[tuple[str, ...], Field(min_length=2)]
    total: float
    tolerance: Annotated[float, Field(ge=0)] = 1e-6
    unit: str
    balance_path: str | None = None


class DesignSpaceDefinition(ContractModel):
    schema_version: Literal["design-space-definition/v1"]
    design_space_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Annotated[str, Field(min_length=1)]
    fixed_values: dict[str, float | str] = {}
    fixed_heat_pattern: tuple[dict[str, object], ...] | None = None
    numeric_domains: tuple[NumericDomain, ...] = ()
    heat_pattern_domains: tuple[NumericDomain, ...] = ()
    categorical_domains: tuple[CategoricalDomain, ...] = ()
    conditional_constraints: tuple[ConditionalActivation, ...] = ()
    composition_constraints: tuple[CompositionTotalConstraint, ...] = ()

    @model_validator(mode="after")
    def unique_paths(self) -> "DesignSpaceDefinition":
        paths = [
            *self.fixed_values,
            *(item.path for item in self.numeric_domains),
            *(item.path for item in self.heat_pattern_domains),
            *(item.path for item in self.categorical_domains),
        ]
        if len(paths) != len(set(paths)):
            raise ValueError("a field may be fixed or variable, not both")
        return self

    def validate_against(self, task: TaskDefinition) -> None:
        if self.task_id != task.id:
            raise ValueError("Design SpaceのTaskがTaskDefinitionと一致しません")
        expected_digest = semantic_digest(task.model_dump(mode="json"))
        if self.task_contract_digest != expected_digest:
            raise ValueError("Design SpaceのTaskDefinition digestが一致しません")
        fields = {field.path: field for group in task.input_groups for field in group.fields}
        for path, value in self.fixed_values.items():
            field = fields.get(path)
            if field is None:
                raise ValueError(f"Design Spaceの固定値がTaskDefinitionにありません: {path}")
            if field.kind == "number":
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise ValueError(f"Design Spaceの固定値は数値が必要です: {path}")
                assert field.allowed_range is not None
                if not field.allowed_range.min <= float(value) <= field.allowed_range.max:
                    raise ValueError(f"Design Spaceの固定値が許容範囲外です: {path}")
            elif field.kind == "categorical" and value not in field.choices:
                raise ValueError(f"Design Spaceの固定値が選択肢にありません: {path}")
        for domain in self.numeric_domains:
            field = fields.get(domain.path)
            if field is None or field.kind != "number" or not field.editable:
                raise ValueError(f"Design Spaceで操作できない数値項目です: {domain.path}")
            assert field.allowed_range is not None
            if domain.range is not None and not field.allowed_range.contains(domain.range):
                raise ValueError(f"Design SpaceがTaskDefinitionの許容範囲を超えています: {domain.path}")
            if any(not field.allowed_range.min <= value <= field.allowed_range.max for value in domain.values):
                raise ValueError(f"Design Spaceの値がTaskDefinitionの許容範囲外です: {domain.path}")
        heat_editable = any(
            field.kind == "heat_pattern" and field.editable
            for field in fields.values()
        )
        for domain in self.heat_pattern_domains:
            if not heat_editable or not __import__("re").fullmatch(
                r"heat_pattern\.\d+\.(time_s|temperature_c)", domain.path
            ):
                raise ValueError(f"Design Spaceで操作できないヒートパターン項目です: {domain.path}")
            lower, upper = (0.0, math.inf) if domain.path.endswith(".time_s") else (-273.15, 1800.0)
            values = domain.values or (
                (domain.range.min, domain.range.max) if domain.range is not None else ()
            )
            if any(not lower <= value <= upper for value in values):
                raise ValueError(f"ヒートパターンの値が許容範囲外です: {domain.path}")
        for domain in self.categorical_domains:
            field = fields.get(domain.path)
            if field is None or field.kind != "categorical" or not field.editable:
                raise ValueError(f"Design Spaceで操作できない区分項目です: {domain.path}")
            if not set(domain.choices) <= set(field.choices):
                raise ValueError(f"Design Spaceの選択肢がTaskDefinitionにありません: {domain.path}")
        for constraint in self.composition_constraints:
            if not math.isfinite(constraint.total):
                raise ValueError("組成合計は有限数にします")
            if len(constraint.component_paths) != len(set(constraint.component_paths)):
                raise ValueError("組成合計のcomponentは重複できません")
            if any(
                path not in fields
                or not path.startswith("composition.")
                or fields[path].kind != "number"
                for path in constraint.component_paths
            ):
                raise ValueError("組成合計制約は宣言済み組成項目だけを参照します")
            if constraint.balance_path is not None and constraint.balance_path not in constraint.component_paths:
                raise ValueError("balance項目は組成合計のcomponentに含めます")
            if any(fields[path].unit != constraint.unit for path in constraint.component_paths):
                raise ValueError("組成合計の単位がTaskDefinitionと一致しません")
            ranges = [fields[path].allowed_range for path in constraint.component_paths]
            if any(item is None for item in ranges):
                raise ValueError("組成合計のcomponentに許容範囲がありません")
            minimum = sum(item.min for item in ranges if item is not None)
            maximum = sum(item.max for item in ranges if item is not None)
            if not minimum - constraint.tolerance <= constraint.total <= maximum + constraint.tolerance:
                raise ValueError("TaskDefinitionの許容範囲では組成合計を実現できません")
        for conditional in self.conditional_constraints:
            controller = fields.get(conditional.controller_path)
            if controller is None or controller.kind != "categorical":
                raise ValueError("条件付き項目のcontrollerは宣言済み区分項目です")
            if not set(conditional.active_choices) <= set(controller.choices):
                raise ValueError("active choiceがTaskDefinitionにありません")
            if any(path not in fields for path in conditional.inactive_values):
                raise ValueError("条件付き項目がTaskDefinitionにありません")
