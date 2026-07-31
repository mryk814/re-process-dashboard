"""Profile contract and loader for generic tabular regression tasks.

The profile owns input/output columns and curation rules. It does not read
source rows or select a runtime.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TabularInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    path: str
    column: str
    kind: Literal["number", "categorical"]
    unit: str = ""
    choices: tuple[str, ...] = ()
    transform: Literal["quadratic", "linear", "log1p"] = "quadratic"
    interact_with_axis: bool = False
    main_effect: bool = True

    @model_validator(mode="after")
    def categorical_shape(self) -> "TabularInput":
        if (self.kind == "categorical") != bool(self.choices):
            raise ValueError("categorical inputs require choices; numeric inputs must omit them")
        if self.kind == "categorical" and self.transform != "quadratic":
            raise ValueError("categorical inputs cannot declare a numeric transform")
        return self


class TabularOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    column: str
    unit: str
    lower_bound: float | None = None
    upper_bound: float | None = None

    @model_validator(mode="after")
    def ordered_bounds(self) -> "TabularOutput":
        if (
            self.lower_bound is not None
            and self.upper_bound is not None
            and self.lower_bound > self.upper_bound
        ):
            raise ValueError("output lower_bound must not exceed upper_bound")
        return self


class TabularQualityRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    column: str
    label: str
    kind: Literal["below_minimum", "repeated_value_fraction"]
    minimum: float | None = None
    value: float | None = None
    fraction: float | None = Field(default=None, gt=0, le=1)

    @model_validator(mode="after")
    def rule_parameters_match_kind(self) -> "TabularQualityRule":
        if self.kind == "below_minimum":
            if self.minimum is None or self.value is not None or self.fraction is not None:
                raise ValueError("below_minimum requires only minimum")
        elif self.value is None or self.fraction is None or self.minimum is not None:
            raise ValueError("repeated_value_fraction requires value and fraction")
        return self


class CurationColumnRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    parser: Literal[
        "finite_number",
        "reported_flag",
        "temperature_c",
        "duration_hours",
        "duration_minutes",
        "percentage",
        "reported_scalar",
    ]
    condition_column: str | None = None
    inactive_value: float | str | None = None
    allowed_units: tuple[str, ...] = ()
    warn_below: float | None = None
    warn_above: float | None = None
    reject_below: float | None = None
    reject_above: float | None = None

    @model_validator(mode="after")
    def conditional_shape(self) -> "CurationColumnRule":
        if (self.condition_column is None) != (self.inactive_value is None):
            raise ValueError("conditional curation requires condition_column and inactive_value")
        if self.allowed_units and self.parser != "reported_scalar":
            raise ValueError("allowed_units is only valid for reported_scalar")
        if (
            self.warn_below is not None
            and self.warn_above is not None
            and self.warn_below >= self.warn_above
        ):
            raise ValueError("curation warning bounds must be ascending")
        if (
            self.reject_below is not None
            and self.reject_above is not None
            and self.reject_below >= self.reject_above
        ):
            raise ValueError("curation rejection bounds must be ascending")
        return self


class CurationSumRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    label: str
    columns: tuple[str, ...] = Field(min_length=2)
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def ordered_range(self) -> "CurationSumRule":
        if self.minimum > self.maximum:
            raise ValueError("curation sum minimum must not exceed maximum")
        return self


class CurationRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["curation-recipe/v1"]
    id: str
    version: str
    header_row_index: int = Field(default=0, ge=0)
    columns: dict[str, CurationColumnRule]
    sum_rules: tuple[CurationSumRule, ...] = ()

    @model_validator(mode="after")
    def referenced_columns_are_declared(self) -> "CurationRecipe":
        declared = set(self.columns)
        for column, rule in self.columns.items():
            if rule.condition_column is not None:
                condition = self.columns.get(rule.condition_column)
                if condition is None or condition.parser != "reported_flag":
                    raise ValueError(
                        f"curation condition for {column} must reference a reported_flag column"
                    )
        for rule in self.sum_rules:
            if not set(rule.columns) <= declared:
                raise ValueError(f"curation sum rule {rule.id} references undeclared columns")
        return self


class TabularDatasetProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["tabular-dataset-profile/v1"]
    profile_id: str
    name: str
    task_id: str
    package_id: str
    id_column: str | None = None
    group_column: str | None = None
    curve_axis_path: str | None = None
    interaction_axis_path: str | None = None
    # Compatibility-only fields for previously issued Profile documents.
    # New Profiles keep estimator choices in a model-training-recipe/v1.
    model_family: Literal["ridge", "lightgbm_monotone", "lightgbm_binary"] | None = None
    ridge_alpha: float | None = Field(default=None, gt=0)
    num_boost_round: int | None = Field(default=None, ge=1)
    monotone_decreasing_paths: tuple[str, ...] = ()
    inputs: tuple[TabularInput, ...] = Field(min_length=1)
    outputs: tuple[TabularOutput, ...] = Field(min_length=1)
    quality_rules: tuple[TabularQualityRule, ...] = ()
    curation_recipe: CurationRecipe | None = None

    @model_validator(mode="after")
    def unique_contract(self) -> "TabularDatasetProfile":
        paths = [item.path for item in self.inputs]
        columns = [item.column for item in self.inputs]
        targets = [item.key for item in self.outputs]
        if len(paths) != len(set(paths)) or len(columns) != len(set(columns)):
            raise ValueError("tabular input paths and columns must be unique")
        if len(targets) != len(set(targets)):
            raise ValueError("tabular output keys must be unique")
        if self.interaction_axis_path is not None:
            axis = next(
                (item for item in self.inputs if item.path == self.interaction_axis_path),
                None,
            )
            if axis is None or axis.kind != "number":
                raise ValueError("interaction_axis_path must identify a numeric input")
            if axis.interact_with_axis:
                raise ValueError("interaction axis cannot interact with itself")
        elif any(item.interact_with_axis for item in self.inputs):
            raise ValueError("interact_with_axis requires interaction_axis_path")
        if any(not item.main_effect and not item.interact_with_axis for item in self.inputs):
            raise ValueError("inputs without a main effect must interact with the axis")
        numeric_paths = {item.path for item in self.inputs if item.kind == "number"}
        if not set(self.monotone_decreasing_paths) <= numeric_paths:
            raise ValueError("monotone paths must identify numeric inputs")
        has_legacy_training = any((
            self.model_family is not None,
            self.ridge_alpha is not None,
            self.num_boost_round is not None,
            bool(self.monotone_decreasing_paths),
        ))
        if has_legacy_training and self.model_family is None:
            raise ValueError(
                "legacy estimator settings require model_family; "
                "new settings belong in a Training Recipe"
            )
        if (
            self.model_family is not None
            and (self.model_family == "lightgbm_monotone")
            != bool(self.monotone_decreasing_paths)
        ):
            raise ValueError("only lightgbm_monotone accepts monotone_decreasing_paths")
        if (
            self.model_family not in {None, "ridge"}
            and self.ridge_alpha not in {None, 1}
        ):
            raise ValueError("ridge_alpha is only valid for ridge")
        if (
            self.model_family is not None
            and self.model_family.startswith("lightgbm")
        ) != (self.num_boost_round is not None):
            raise ValueError("LightGBM profiles require a fixed num_boost_round")
        if self.curation_recipe is not None:
            declared = set(self.curation_recipe.columns)
            required = {
                *(item.column for item in self.inputs),
                *(item.column for item in self.outputs),
            }
            if not required <= declared:
                raise ValueError("curation recipe must declare every input and output column")
        return self


def load_tabular_profile(path: str | Path) -> TabularDatasetProfile:
    return TabularDatasetProfile.model_validate_json(Path(path).read_text(encoding="utf-8"))
