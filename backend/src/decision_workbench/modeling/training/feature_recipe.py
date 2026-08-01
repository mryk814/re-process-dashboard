"""Fit and execute the fixed feature-recipe/v1 operation registry."""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.feature_recipe_contracts import (
    CyclicOperation,
    FeatureOperation,
    FeatureRecipe,
    FeatureRecipeState,
    ImputeOperation,
    Log1pOperation,
    MissingIndicatorOperation,
    OneHotOperation,
    OperationFitState,
    PairwiseInteractionOperation,
    PassthroughOperation,
    PolynomialDegree2Operation,
    RobustScaleOperation,
    StandardizeOperation,
    operation_outputs,
)


def semantic_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def recipe_digest(recipe: FeatureRecipe) -> str:
    return semantic_digest(recipe.model_dump(mode="json"))


def validate_recipe_canonical_inputs(
    recipe: FeatureRecipe,
    allowed_paths: Sequence[str],
) -> None:
    unknown = set(recipe.canonical_input_paths) - set(allowed_paths)
    if unknown:
        raise ValueError(
            "feature recipe references canonical paths outside the Task contract: "
            + ", ".join(sorted(unknown))
        )


def _missing(value: Any) -> bool:
    return value is None or value == "" or (
        isinstance(value, (float, np.floating)) and not math.isfinite(float(value))
    )


def _finite(value: Any, *, label: str) -> float:
    if _missing(value):
        raise ValueError(f"{label} is missing")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _state_parameters(
    operation: FeatureOperation,
    workspaces: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    if isinstance(operation, StandardizeOperation):
        values = np.asarray(
            [_finite(row[operation.input], label=operation.input) for row in workspaces],
            dtype=float,
        )
        scale = float(values.std())
        if scale <= 1e-12:
            raise ValueError(f"{operation.id}: standardize input has zero variance")
        return {"mean": float(values.mean()), "scale": scale}
    if isinstance(operation, RobustScaleOperation):
        values = np.asarray(
            [_finite(row[operation.input], label=operation.input) for row in workspaces],
            dtype=float,
        )
        q25, median, q75 = np.quantile(values, (0.25, 0.5, 0.75))
        scale = float(q75 - q25)
        if scale <= 1e-12:
            raise ValueError(f"{operation.id}: robust_scale input has zero IQR")
        return {"median": float(median), "scale": scale}
    if isinstance(operation, ImputeOperation) and operation.strategy == "median":
        values = [
            _finite(row[operation.input], label=operation.input)
            for row in workspaces
            if not _missing(row[operation.input])
        ]
        if not values:
            raise ValueError(f"{operation.id}: median imputation has no observed values")
        return {"value": float(np.median(np.asarray(values, dtype=float)))}
    if isinstance(operation, ImputeOperation):
        assert operation.value is not None
        return {"value": float(operation.value)}
    return {}


def _apply(
    operation: FeatureOperation,
    workspace: dict[str, Any],
    parameters: Mapping[str, float],
) -> None:
    if isinstance(operation, PassthroughOperation):
        workspace[operation.output] = _finite(
            workspace[operation.input], label=operation.input
        )
    elif isinstance(operation, StandardizeOperation):
        value = _finite(workspace[operation.input], label=operation.input)
        workspace[operation.output] = (
            value - parameters["mean"]
        ) / parameters["scale"]
    elif isinstance(operation, RobustScaleOperation):
        value = _finite(workspace[operation.input], label=operation.input)
        workspace[operation.output] = (
            value - parameters["median"]
        ) / parameters["scale"]
    elif isinstance(operation, Log1pOperation):
        value = _finite(workspace[operation.input], label=operation.input)
        if value < 0:
            raise ValueError(f"{operation.input} must be non-negative for log1p")
        workspace[operation.output] = math.log1p(value)
    elif isinstance(operation, PolynomialDegree2Operation):
        value = _finite(workspace[operation.input], label=operation.input)
        workspace[operation.linear_output] = value
        workspace[operation.square_output] = value * value
    elif isinstance(operation, OneHotOperation):
        raw = workspace[operation.input]
        value = "__missing__" if _missing(raw) else str(raw)
        if value not in operation.choices:
            if operation.unknown_policy == "reject":
                raise ValueError(
                    f"{operation.input} is not a declared one_hot category"
                )
            value = (
                "__missing__"
                if operation.unknown_policy == "map_to_missing"
                else operation.other_choice
            )
        for choice, output in zip(
            operation.choices, operation.outputs, strict=True
        ):
            workspace[output] = float(value == choice)
    elif isinstance(operation, MissingIndicatorOperation):
        workspace[operation.output] = float(_missing(workspace[operation.input]))
    elif isinstance(operation, ImputeOperation):
        raw = workspace[operation.input]
        workspace[operation.output] = (
            parameters["value"]
            if _missing(raw)
            else _finite(raw, label=operation.input)
        )
    elif isinstance(operation, PairwiseInteractionOperation):
        left, right = operation.inputs
        workspace[operation.output] = _finite(
            workspace[left], label=left
        ) * _finite(workspace[right], label=right)
    elif isinstance(operation, CyclicOperation):
        value = _finite(workspace[operation.input], label=operation.input)
        angle = 2.0 * math.pi * value / operation.period
        workspace[operation.sin_output] = math.sin(angle)
        workspace[operation.cos_output] = math.cos(angle)
    else:  # pragma: no cover - the discriminated contract closes the registry
        raise TypeError(f"unsupported feature operation: {operation.kind}")


def fit_feature_recipe(
    recipe: FeatureRecipe,
    rows: Sequence[Mapping[str, Any]],
) -> FeatureRecipeState:
    if not rows:
        raise ValueError("feature recipe fit requires at least one row")
    required = set(recipe.canonical_input_paths)
    workspaces = []
    for row in rows:
        missing = required - set(row)
        if missing:
            raise ValueError(
                "canonical feature row is missing paths: "
                + ", ".join(sorted(missing))
            )
        workspaces.append(dict(row))
    states: list[OperationFitState] = []
    for operation in recipe.operations:
        parameters = _state_parameters(operation, workspaces)
        states.append(
            OperationFitState(
                operation_id=operation.id,
                kind=operation.kind,
                parameters=parameters,
            )
        )
        for workspace in workspaces:
            _apply(operation, workspace, parameters)
    payload = {
        "schema_version": "feature-recipe-state/v1",
        "recipe_digest": recipe_digest(recipe),
        "fit_row_count": len(rows),
        "operations": [state.model_dump(mode="json") for state in states],
        "output_features": [feature.name for feature in recipe.features],
    }
    return FeatureRecipeState(
        **payload,
        state_digest=semantic_digest(payload),
    )


def verify_feature_recipe_state(
    recipe: FeatureRecipe,
    state: FeatureRecipeState,
) -> None:
    if state.recipe_digest != recipe_digest(recipe):
        raise ValueError("feature recipe state does not match recipe digest")
    if state.output_features != tuple(item.name for item in recipe.features):
        raise ValueError("feature recipe state output order does not match recipe")
    expected = [(item.id, item.kind) for item in recipe.operations]
    actual = [(item.operation_id, item.kind) for item in state.operations]
    if actual != expected:
        raise ValueError("feature recipe state operation order or kind is invalid")
    payload = state.model_dump(mode="json", exclude={"state_digest"})
    if state.state_digest != semantic_digest(payload):
        raise ValueError("feature recipe state digest is invalid")
    for operation, operation_state in zip(
        recipe.operations, state.operations, strict=True
    ):
        expected_parameters = (
            {"mean", "scale"}
            if isinstance(operation, StandardizeOperation)
            else {"median", "scale"}
            if isinstance(operation, RobustScaleOperation)
            else {"value"}
            if isinstance(operation, ImputeOperation)
            else set()
        )
        if set(operation_state.parameters) != expected_parameters:
            raise ValueError(
                f"{operation.id}: feature recipe state shape is invalid"
            )
        if (
            "scale" in operation_state.parameters
            and operation_state.parameters["scale"] <= 0
        ):
            raise ValueError(f"{operation.id}: fitted scale must be positive")


def transform_feature_recipe(
    recipe: FeatureRecipe,
    state: FeatureRecipeState,
    rows: Sequence[Mapping[str, Any]],
) -> np.ndarray:
    verify_feature_recipe_state(recipe, state)
    by_operation = {
        item.operation_id: item.parameters for item in state.operations
    }
    output_names = tuple(item.name for item in recipe.features)
    transformed: list[list[float]] = []
    for raw in rows:
        workspace = dict(raw)
        missing = set(recipe.canonical_input_paths) - set(workspace)
        if missing:
            raise ValueError(
                "canonical feature row is missing paths: "
                + ", ".join(sorted(missing))
            )
        for operation in recipe.operations:
            _apply(operation, workspace, by_operation[operation.id])
        transformed.append(
            [_finite(workspace[name], label=name) for name in output_names]
        )
    values = np.asarray(transformed, dtype=np.float64)
    if values.shape != (len(rows), len(output_names)) or not np.isfinite(values).all():
        raise ValueError("feature recipe produced an invalid matrix")
    return values


def inspect_feature_recipe(
    recipe: FeatureRecipe,
    state: FeatureRecipeState,
    canonical_input: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the deterministic operation trace used by the developer surface."""

    verify_feature_recipe_state(recipe, state)
    missing = set(recipe.canonical_input_paths) - set(canonical_input)
    if missing:
        raise ValueError(
            "canonical feature row is missing paths: "
            + ", ".join(sorted(missing))
        )
    workspace = dict(canonical_input)
    by_operation = {
        item.operation_id: item.parameters for item in state.operations
    }
    steps: list[dict[str, Any]] = []
    for operation in recipe.operations:
        _apply(operation, workspace, by_operation[operation.id])
        steps.append(
            {
                "operation_id": operation.id,
                "kind": operation.kind,
                "outputs": {
                    name: workspace[name]
                    for name in operation_outputs(operation)
                },
            }
        )
    return {
        "recipe_id": recipe.id,
        "recipe_digest": state.recipe_digest,
        "state_digest": state.state_digest,
        "canonical_input": {
            path: canonical_input.get(path)
            for path in recipe.canonical_input_paths
        },
        "steps": steps,
        "features": {
            item.name: _finite(workspace[item.name], label=item.name)
            for item in recipe.features
        },
    }


def canonical_recipe_inputs(
    candidate: CandidateInput,
    recipe: FeatureRecipe,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for path in recipe.canonical_input_paths:
        group, separator, key = path.partition(".")
        if not separator or group not in {"composition", "process", "categorical"}:
            raise ValueError(
                f"feature recipe input is not a scalar canonical path: {path}"
            )
        values[path] = getattr(candidate.inputs, group).get(key)
    return values


def apply_feature_recipe_to_canonical_dataset(
    canonical: dict[str, Any],
    data: Any,
    candidate_builder: Callable[[dict[str, Any], Any], CandidateInput | None],
    recipe: FeatureRecipe,
    state: FeatureRecipeState | None = None,
) -> FeatureRecipeState:
    """Materialize one Recipe without changing source/Profile interpretation."""

    observations = {str(item["id"]): item for item in data.observations}
    raw_rows: list[dict[str, Any]] = []
    for row in canonical["rows"]:
        observation = observations.get(str(row["observation_id"]))
        candidate = (
            None
            if observation is None
            else candidate_builder(observation, data)
        )
        if candidate is None:
            raise ValueError(
                f"feature recipe cannot resolve canonical row {row['observation_id']}"
            )
        inputs = canonical_recipe_inputs(candidate, recipe)
        row["canonical_inputs"] = inputs
        raw_rows.append(inputs)
    fitted = state or fit_feature_recipe(recipe, raw_rows)
    transformed = transform_feature_recipe(recipe, fitted, raw_rows)
    names = tuple(item.name for item in recipe.features)
    for row, values in zip(canonical["rows"], transformed, strict=True):
        row["features"] = dict(zip(names, map(float, values), strict=True))
    canonical["feature_pipeline"] = {
        "id": recipe.id,
        "version": recipe.version,
        "features": [
            {
                "name": item.name,
                "unit": item.unit,
                "meaning": item.meaning,
                "group": item.group,
            }
            for item in recipe.features
        ],
        "feature_recipe": {
            "recipe_digest": fitted.recipe_digest,
            "state_digest": fitted.state_digest,
        },
    }
    return fitted


def save_feature_recipe_artifacts(
    recipe: FeatureRecipe,
    state: FeatureRecipeState,
    recipe_path: Path,
    state_path: Path,
) -> None:
    verify_feature_recipe_state(recipe, state)
    for path, document in ((recipe_path, recipe), (state_path, state)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            document.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def load_feature_recipe_artifacts(
    recipe_path: Path,
    state_path: Path,
) -> tuple[FeatureRecipe, FeatureRecipeState]:
    recipe = FeatureRecipe.model_validate_json(recipe_path.read_text(encoding="utf-8"))
    state = FeatureRecipeState.model_validate_json(state_path.read_text(encoding="utf-8"))
    verify_feature_recipe_state(recipe, state)
    return recipe, state
