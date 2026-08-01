"""Immutable, task-bounded definitions for proposal design spaces."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import (
    ContractModel,
    NumericRange,
    RelationalConstraint,
    TaskDefinition,
)
from decision_workbench.execution.inference_work_graph import semantic_digest


class NumericDomain(ContractModel):
    path: Annotated[str, Field(min_length=1)]
    mode: Literal["range", "values"]
    range: NumericRange | None = None
    values: tuple[float, ...] = ()
    numeric_domain_kind: Literal["continuous", "integer", "step"] = "continuous"
    step: float | None = None
    step_origin: float | None = None
    search_scale: Literal["linear", "log"] = "linear"

    @model_validator(mode="after")
    def complete(self) -> "NumericDomain":
        if self.mode == "range" and (self.range is None or self.values):
            raise ValueError("range domain requires only range")
        if self.mode == "values" and (not self.values or self.range is not None):
            raise ValueError("values domain requires only values")
        if self.numeric_domain_kind == "step" and self.step is None:
            raise ValueError("step numeric domain requires step")
        if self.numeric_domain_kind != "step" and self.step is not None:
            raise ValueError("only step numeric domains can declare step")
        if self.numeric_domain_kind == "step" and self.step_origin is None:
            raise ValueError("step numeric domain requires step_origin")
        if self.numeric_domain_kind != "step" and self.step_origin is not None:
            raise ValueError("only step numeric domains can declare step_origin")
        if self.step is not None and (not math.isfinite(self.step) or self.step <= 0):
            raise ValueError("numeric domain step must be a positive finite value")
        if self.search_scale == "log":
            values = self.values or ((self.range.min, self.range.max) if self.range is not None else ())
            if any(value <= 0 for value in values):
                raise ValueError("log scale numeric domains require positive values")
        return self

    def validate_value(self, value: float) -> None:
        if not math.isfinite(value):
            raise ValueError(f"numeric domain value must be finite: {self.path}")
        if self.numeric_domain_kind == "integer" and not math.isclose(value, round(value), rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"numeric domain value must be an integer: {self.path}")
        if self.step is not None and not math.isclose((value - self.step_origin) / self.step, round((value - self.step_origin) / self.step), rel_tol=0.0, abs_tol=1e-9):
            raise ValueError(f"numeric domain value must align to step {self.step:g}: {self.path}")


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
    revision: Annotated[int, Field(ge=1)] = 1
    name: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Annotated[str, Field(min_length=1)]
    fixed_values: dict[str, float | str] = {}
    fixed_heat_pattern: tuple[dict[str, object], ...] | None = None
    numeric_domains: tuple[NumericDomain, ...] = ()
    heat_pattern_domains: tuple[NumericDomain, ...] = ()
    categorical_domains: tuple[CategoricalDomain, ...] = ()
    conditional_constraints: tuple[ConditionalActivation, ...] = ()
    relational_constraints: tuple[RelationalConstraint, ...] = ()
    composition_constraints: tuple[CompositionTotalConstraint, ...] = ()

    @property
    def digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))

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
                field.validate_numeric_value(float(value))
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
            if (
                domain.numeric_domain_kind != field.numeric_domain_kind
                or domain.step != field.step
                or domain.step_origin != (field.allowed_range.min if field.numeric_domain_kind == "step" else None)
                or domain.search_scale != field.search_scale
            ):
                raise ValueError(f"Design SpaceがTaskDefinitionの数値domainを変更しています: {domain.path}")
            values = domain.values or (
                (domain.range.min, domain.range.max) if domain.range is not None else ()
            )
            for value in values:
                field.validate_numeric_value(value)
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
        for constraint in self.relational_constraints:
            referenced = (constraint.left_path, constraint.right_path)
            if any(
                path not in fields or fields[path].kind != "number"
                for path in referenced
            ):
                raise ValueError("関係制約は宣言済み数値項目だけを参照します")

    def validate_narrows(self, parent: "DesignSpaceDefinition") -> None:
        """Reject a run-local space that widens its Project Design Space."""

        if self.task_id != parent.task_id:
            raise ValueError("Design SpaceのTaskがProjectと一致しません")
        if self.task_contract_digest != parent.task_contract_digest:
            raise ValueError("Design SpaceのTask契約がProjectと一致しません")
        parent_numeric = {
            item.path: item for item in (*parent.numeric_domains, *parent.heat_pattern_domains)
        }
        for child in (*self.numeric_domains, *self.heat_pattern_domains):
            outer = parent_numeric.get(child.path)
            # Heat-point paths depend on the selected candidate length. An empty
            # Project heat domain therefore means "Task bounds apply".
            if outer is None and child.path.startswith("heat_pattern."):
                continue
            if outer is None:
                raise ValueError(f"Project Design Spaceで変更できない項目です: {child.path}")
            values = child.values or (
                (child.range.min, child.range.max) if child.range is not None else ()
            )
            if outer.range is not None and any(
                not outer.range.min <= value <= outer.range.max for value in values
            ):
                raise ValueError(f"Project Design Spaceの範囲を超えています: {child.path}")
            if outer.values and any(value not in outer.values for value in values):
                raise ValueError(f"Project Design Spaceの候補値にありません: {child.path}")
            if (
                child.numeric_domain_kind != outer.numeric_domain_kind
                or child.step != outer.step
                or child.step_origin != outer.step_origin
                or child.search_scale != outer.search_scale
            ):
                raise ValueError(f"Project Design Spaceの数値domainを変更できません: {child.path}")
        parent_categories = {item.path: set(item.choices) for item in parent.categorical_domains}
        for child in self.categorical_domains:
            if not set(child.choices) <= parent_categories.get(child.path, set()):
                raise ValueError(f"Project Design Spaceの選択肢を超えています: {child.path}")
        parent_fixed = dict(parent.fixed_values)
        for path, value in self.fixed_values.items():
            numeric = parent_numeric.get(path)
            categories = parent_categories.get(path)
            if path in parent_fixed and value != parent_fixed[path]:
                raise ValueError(f"Project Design Spaceの固定値と一致しません: {path}")
            if numeric is not None:
                if numeric.range is not None and not numeric.range.min <= float(value) <= numeric.range.max:
                    raise ValueError(f"Project Design Spaceの範囲を超えています: {path}")
                if numeric.values and value not in numeric.values:
                    raise ValueError(f"Project Design Spaceの候補値にありません: {path}")
            elif categories is not None:
                if value not in categories:
                    raise ValueError(f"Project Design Spaceの選択肢にありません: {path}")
            elif path not in parent_fixed:
                raise ValueError(f"Project Design Spaceにない固定項目です: {path}")


def default_design_space(task: TaskDefinition, *, task_contract_digest: str) -> DesignSpaceDefinition:
    """Create the immutable full Task-bounded space used by new Projects."""

    numeric = []
    categorical = []
    for group in task.input_groups:
        for field in group.fields:
            if not field.editable:
                continue
            if field.kind == "number" and field.allowed_range is not None:
                numeric.append(
                    NumericDomain(
                        path=field.path,
                        mode="range",
                        range=field.allowed_range,
                        numeric_domain_kind=field.numeric_domain_kind,
                        step=field.step,
                        step_origin=(field.allowed_range.min if field.numeric_domain_kind == "step" else None),
                        search_scale=field.search_scale,
                    )
                )
            elif field.kind == "categorical":
                categorical.append(
                    CategoricalDomain(path=field.path, choices=field.choices)
                )
    return DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id=f"{task.id}-project-default",
        revision=1,
        name="Task許容範囲",
        task_id=task.id,
        task_contract_digest=task_contract_digest,
        numeric_domains=tuple(numeric),
        categorical_domains=tuple(categorical),
        relational_constraints=task.constraints,
        composition_constraints=tuple(
            CompositionTotalConstraint(
                component_paths=item.component_paths,
                total=item.total,
                tolerance=item.tolerance,
                unit=item.unit,
                balance_path=item.balance_path,
            )
            for item in task.composition_totals
        ),
    )
