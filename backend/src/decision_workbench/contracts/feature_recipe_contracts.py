"""Data-only contracts for allow-listed tabular feature transformations."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import ContractModel

FeatureGroup = Literal[
    "composition",
    "process",
    "categorical",
    "metallurgy",
    "heat_pattern",
    "other",
]


class RecipeFeature(ContractModel):
    name: Annotated[str, Field(min_length=1)]
    unit: str
    meaning: Annotated[str, Field(min_length=1)]
    group: FeatureGroup


class PassthroughOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["passthrough"]
    input: Annotated[str, Field(min_length=1)]
    output: Annotated[str, Field(min_length=1)]


class StandardizeOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["standardize"]
    input: Annotated[str, Field(min_length=1)]
    output: Annotated[str, Field(min_length=1)]


class RobustScaleOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["robust_scale"]
    input: Annotated[str, Field(min_length=1)]
    output: Annotated[str, Field(min_length=1)]


class Log1pOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["log1p"]
    input: Annotated[str, Field(min_length=1)]
    output: Annotated[str, Field(min_length=1)]


class PolynomialDegree2Operation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["polynomial_degree_2"]
    input: Annotated[str, Field(min_length=1)]
    linear_output: Annotated[str, Field(min_length=1)]
    square_output: Annotated[str, Field(min_length=1)]


class OneHotOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["one_hot"]
    input: Annotated[str, Field(min_length=1)]
    choices: Annotated[tuple[str, ...], Field(min_length=1)]
    outputs: Annotated[tuple[str, ...], Field(min_length=1)]
    unknown_policy: Literal["reject", "map_to_other", "map_to_missing"] = "reject"
    other_choice: str | None = None

    @model_validator(mode="after")
    def choices_match_outputs(self) -> OneHotOperation:
        if len(self.choices) != len(set(self.choices)):
            raise ValueError("one_hot choices must be unique")
        if len(self.outputs) != len(self.choices):
            raise ValueError("one_hot requires one output per choice")
        if len(self.outputs) != len(set(self.outputs)):
            raise ValueError("one_hot outputs must be unique")
        if self.unknown_policy == "reject" and self.other_choice is not None:
            raise ValueError("reject one_hot policy must omit other_choice")
        if self.unknown_policy == "map_to_other":
            if self.other_choice not in self.choices:
                raise ValueError("one_hot other_choice must be a declared choice")
        elif (
            self.unknown_policy == "map_to_missing"
            and ("__missing__" not in self.choices or self.other_choice is not None)
        ):
            raise ValueError(
                "map_to_missing requires the __missing__ choice and no other_choice"
            )
        return self


class MissingIndicatorOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["missing_indicator"]
    input: Annotated[str, Field(min_length=1)]
    output: Annotated[str, Field(min_length=1)]


class ImputeOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["impute"]
    input: Annotated[str, Field(min_length=1)]
    output: Annotated[str, Field(min_length=1)]
    strategy: Literal["constant", "median"]
    value: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def value_matches_strategy(self) -> ImputeOperation:
        if (self.strategy == "constant") != (self.value is not None):
            raise ValueError("constant imputation requires value; median must omit it")
        return self


class PairwiseInteractionOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["pairwise_interaction"]
    inputs: Annotated[tuple[str, str], Field(min_length=2, max_length=2)]
    output: Annotated[str, Field(min_length=1)]


class CyclicOperation(ContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["cyclic"]
    input: Annotated[str, Field(min_length=1)]
    period: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    sin_output: Annotated[str, Field(min_length=1)]
    cos_output: Annotated[str, Field(min_length=1)]


FeatureOperation = Annotated[
    PassthroughOperation
    | StandardizeOperation
    | RobustScaleOperation
    | Log1pOperation
    | PolynomialDegree2Operation
    | OneHotOperation
    | MissingIndicatorOperation
    | ImputeOperation
    | PairwiseInteractionOperation
    | CyclicOperation,
    Field(discriminator="kind"),
]


def operation_outputs(operation: FeatureOperation) -> tuple[str, ...]:
    if isinstance(operation, PolynomialDegree2Operation):
        return operation.linear_output, operation.square_output
    if isinstance(operation, OneHotOperation):
        return operation.outputs
    if isinstance(operation, CyclicOperation):
        return operation.sin_output, operation.cos_output
    return (operation.output,)


def operation_inputs(operation: FeatureOperation) -> tuple[str, ...]:
    if isinstance(operation, PairwiseInteractionOperation):
        return operation.inputs
    return (operation.input,)


class FeatureRecipe(ContractModel):
    schema_version: Literal["feature-recipe/v1"] = "feature-recipe/v1"
    id: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    canonical_input_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    operations: Annotated[tuple[FeatureOperation, ...], Field(min_length=1)]
    features: Annotated[tuple[RecipeFeature, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def ordered_dataflow_is_closed(self) -> FeatureRecipe:
        if len(self.canonical_input_paths) != len(set(self.canonical_input_paths)):
            raise ValueError("feature recipe canonical inputs must be unique")
        ids = [item.id for item in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("feature recipe operation ids must be unique")
        available = set(self.canonical_input_paths)
        produced: list[str] = []
        for operation in self.operations:
            missing = set(operation_inputs(operation)) - available
            if missing:
                raise ValueError(
                    f"operation {operation.id} references unavailable inputs: "
                    + ", ".join(sorted(missing))
                )
            outputs = operation_outputs(operation)
            if len(outputs) != len(set(outputs)):
                raise ValueError(
                    f"operation {operation.id} output names must be unique"
                )
            if set(outputs) & available:
                raise ValueError(f"operation {operation.id} overwrites an existing value")
            available.update(outputs)
            produced.extend(outputs)
        names = [feature.name for feature in self.features]
        if len(names) != len(set(names)):
            raise ValueError("feature recipe output names must be unique")
        if any(name not in produced for name in names):
            raise ValueError("every declared feature must be produced by an operation")
        return self


class OperationFitState(ContractModel):
    operation_id: Annotated[str, Field(min_length=1)]
    kind: str
    parameters: dict[str, float] = Field(default_factory=dict)


class FeatureRecipeState(ContractModel):
    schema_version: Literal["feature-recipe-state/v1"] = "feature-recipe-state/v1"
    recipe_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    fit_row_count: Annotated[int, Field(ge=1)]
    operations: tuple[OperationFitState, ...]
    output_features: Annotated[tuple[str, ...], Field(min_length=1)]
    state_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
