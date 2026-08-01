"""Canonical-candidate to feature-vector conversion for tabular tasks."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.feature_contracts import FeatureBundle, FeatureDefinition

from .profile import MISSING_CATEGORY, TabularDatasetProfile


def _categorical_choices(item: Any) -> tuple[str, ...]:
    return (
        (*item.choices, MISSING_CATEGORY)
        if (
            item.categorical_missing.strategy == "map_to_missing_category"
            or item.unknown_category.strategy == "map_to_missing_category"
        )
        else item.choices
    )


def _get_path(candidate: CandidateInput, path: str) -> float | str | None:
    group, key = path.split(".", 1)
    values = getattr(candidate.inputs, group)
    return values.get(key)


def _set_path(candidate: CandidateInput, path: str, value: float) -> None:
    group, key = path.split(".", 1)
    getattr(candidate.inputs, group)[key] = float(value)


def feature_definitions(profile: TabularDatasetProfile) -> tuple[FeatureDefinition, ...]:
    definitions: list[FeatureDefinition] = []
    for item in profile.inputs:
        if not item.main_effect:
            continue
        group = item.path.split(".", 1)[0]
        feature_group = group if group in {"composition", "process", "categorical"} else "other"
        if item.kind == "number":
            if item.transform == "log1p":
                definitions.append(
                    FeatureDefinition(
                        f"{item.path}__log1p",
                        "1",
                        f"log1p transformed {item.path}",
                        feature_group,
                    )
                )
            elif item.transform == "quadratic":
                definitions.extend((
                    FeatureDefinition(item.path, item.unit, f"{item.path} raw value", feature_group),
                    FeatureDefinition(f"{item.path}__square", f"{item.unit}^2", f"{item.path} quadratic term", feature_group),
                ))
            else:
                definitions.append(
                    FeatureDefinition(item.path, item.unit, f"{item.path} raw value", feature_group)
                )
            if item.numeric_missing.strategy == "training_median_with_indicator":
                definitions.append(
                    FeatureDefinition(
                        f"{item.path}__missing",
                        "1",
                        f"{item.path} was missing before training-median imputation",
                        feature_group,
                    )
                )
        else:
            definitions.extend(
                FeatureDefinition(
                    f"{item.path}__{choice}",
                    "1",
                    f"{item.path} is {choice}",
                    "categorical",
                )
                for choice in _categorical_choices(item)
            )
    if profile.interaction_axis_path is not None:
        axis = next(item for item in profile.inputs if item.path == profile.interaction_axis_path)
        for item in profile.inputs:
            if not item.interact_with_axis:
                continue
            group = item.path.split(".", 1)[0]
            feature_group = group if group in {"composition", "process", "categorical"} else "other"
            if item.kind == "number":
                definitions.append(FeatureDefinition(
                    f"{axis.path}__x__{item.path}",
                    f"{axis.unit}*{item.unit}",
                    f"{axis.path} interaction with {item.path}",
                    feature_group,
                ))
            else:
                definitions.extend(
                    FeatureDefinition(
                        f"{axis.path}__x__{item.path}__{choice}",
                        axis.unit,
                        f"{axis.path} interaction with {item.path} is {choice}",
                        "categorical",
                    )
                    for choice in _categorical_choices(item)
                )
    return tuple(definitions)


def build_tabular_features(
    candidate: CandidateInput,
    profile: TabularDatasetProfile,
    imputation_values: dict[str, float] | None = None,
    *,
    allow_normalized_missing_category: bool = False,
) -> FeatureBundle:
    values: list[float] = []
    for item in profile.inputs:
        value = _get_path(candidate, item.path)
        if not item.main_effect:
            continue
        if item.kind == "number":
            missing = value is None or value == ""
            if missing:
                policy = item.numeric_missing
                if policy.strategy == "reject":
                    raise ValueError(f"{item.path} is missing and Profile policy is reject")
                if policy.strategy == "constant":
                    assert policy.value is not None
                    numeric = policy.value
                else:
                    if imputation_values is None or item.path not in imputation_values:
                        raise ValueError(
                            f"{item.path} requires a fitted training-median artifact"
                        )
                    numeric = float(imputation_values[item.path])
            else:
                numeric = float(value)
            if not math.isfinite(numeric):
                raise ValueError(f"{item.path} must be finite")
            if item.transform == "log1p":
                if numeric < 0:
                    raise ValueError(f"{item.path} must be non-negative for log1p transform")
                values.append(math.log1p(numeric))
            elif item.transform == "quadratic":
                values.extend((numeric, numeric * numeric))
            else:
                values.append(numeric)
            if item.numeric_missing.strategy == "training_median_with_indicator":
                values.append(float(missing))
        else:
            normalized = "" if value is None else str(value)
            if not normalized:
                policy = item.categorical_missing
                if policy.strategy == "reject":
                    raise ValueError(f"{item.path} is missing and Profile policy is reject")
                normalized = (
                    MISSING_CATEGORY
                    if policy.strategy == "map_to_missing_category"
                    else policy.category
                )
                assert normalized is not None
            elif (
                normalized == MISSING_CATEGORY
                and not allow_normalized_missing_category
            ):
                raise ValueError(f"{item.path} uses a reserved missing category")
            elif normalized not in _categorical_choices(item):
                policy = item.unknown_category
                if policy.strategy == "reject":
                    raise ValueError(f"{item.path} is not a declared category")
                normalized = (
                    MISSING_CATEGORY
                    if policy.strategy == "map_to_missing_category"
                    else policy.other_choice
                )
                assert normalized is not None
            values.extend(
                float(normalized == choice)
                for choice in _categorical_choices(item)
            )
    if profile.interaction_axis_path is not None:
        axis_item = next(
            item for item in profile.inputs if item.path == profile.interaction_axis_path
        )
        raw_axis = _get_path(candidate, profile.interaction_axis_path)
        if raw_axis is None or raw_axis == "":
            if axis_item.numeric_missing.strategy == "constant":
                assert axis_item.numeric_missing.value is not None
                axis_value = axis_item.numeric_missing.value
            elif (
                axis_item.numeric_missing.strategy == "training_median_with_indicator"
                and imputation_values is not None
                and axis_item.path in imputation_values
            ):
                axis_value = imputation_values[axis_item.path]
            else:
                raise ValueError(f"{axis_item.path} is missing")
        else:
            axis_value = float(raw_axis)
        for item in profile.inputs:
            if not item.interact_with_axis:
                continue
            value = _get_path(candidate, item.path)
            if item.kind == "number":
                if value is None or value == "":
                    if item.numeric_missing.strategy == "constant":
                        assert item.numeric_missing.value is not None
                        numeric = item.numeric_missing.value
                    elif (
                        item.numeric_missing.strategy == "training_median_with_indicator"
                        and imputation_values is not None
                        and item.path in imputation_values
                    ):
                        numeric = imputation_values[item.path]
                    else:
                        raise ValueError(f"{item.path} is missing")
                else:
                    numeric = float(value)
                values.append(axis_value * numeric)
            else:
                normalized = "" if value is None else str(value)
                if not normalized:
                    policy = item.categorical_missing
                    if policy.strategy == "reject":
                        raise ValueError(f"{item.path} is missing")
                    normalized = (
                        MISSING_CATEGORY
                        if policy.strategy == "map_to_missing_category"
                        else policy.category
                    )
                    assert normalized is not None
                elif (
                    normalized == MISSING_CATEGORY
                    and not allow_normalized_missing_category
                ):
                    raise ValueError(f"{item.path} uses a reserved missing category")
                elif normalized not in _categorical_choices(item):
                    policy = item.unknown_category
                    if policy.strategy == "reject":
                        raise ValueError(f"{item.path} is not a declared category")
                    normalized = (
                        MISSING_CATEGORY
                        if policy.strategy == "map_to_missing_category"
                        else policy.other_choice
                    )
                    assert normalized is not None
                values.extend(
                    axis_value * float(normalized == choice)
                    for choice in _categorical_choices(item)
                )
    return FeatureBundle(
        pipeline_id=f"{profile.task_id}-profile-transform",
        pipeline_version="1.0.0",
        definitions=feature_definitions(profile),
        values=np.asarray(values, dtype=float),
    )


def candidate_from_observation(
    row: dict[str, Any],
    profile: TabularDatasetProfile,
    *,
    preserve_normalized_missing: bool = False,
) -> CandidateInput:
    categorical = dict(row["categorical"] or {})
    if not preserve_normalized_missing:
        categorical = {
            key: value
            for key, value in categorical.items()
            if value != MISSING_CATEGORY
        }
    return CandidateInput(
        name=str(row["id"]),
        inputs={
            "composition": dict(row["composition"] or {}),
            "process": dict(row["features"] or {}),
            "categorical": categorical,
            "heat_pattern": None,
        },
    )


def build_tabular_features_from_observation(
    row: dict[str, Any],
    imputation_values: dict[str, float],
    profile: TabularDatasetProfile,
) -> FeatureBundle:
    return build_tabular_features(
        candidate_from_observation(
            row,
            profile,
            preserve_normalized_missing=True,
        ),
        profile,
        imputation_values,
        allow_normalized_missing_category=True,
    )


def build_tabular_training_features_from_observation(
    row: dict[str, Any],
    imputation_values: dict[str, float],
    profile: TabularDatasetProfile,
) -> tuple[FeatureBundle, dict[str, float]]:
    """Keep runtime bundles finite while preserving raw missing for fold fitting."""

    bundle = build_tabular_features_from_observation(
        row,
        imputation_values,
        profile,
    )
    values = bundle.as_dict()
    predictor_evidence = row.get("run_context", {}).get(
        "curation",
        {},
    ).get("predictor_policies", {})
    for item in profile.inputs:
        if (
            item.numeric_missing.strategy == "training_median_with_indicator"
            and predictor_evidence.get(item.column, {}).get("source_state")
            == "missing"
        ):
            values[item.path] = float("nan")
    return bundle, values
