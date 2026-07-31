"""Canonical-candidate to feature-vector conversion for tabular tasks."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.feature_contracts import FeatureBundle, FeatureDefinition

from .profile import TabularDatasetProfile


def _get_path(candidate: CandidateInput, path: str) -> float | str:
    group, key = path.split(".", 1)
    values = getattr(candidate.inputs, group)
    return values[key]


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
        else:
            definitions.extend(
                FeatureDefinition(
                    f"{item.path}__{choice}",
                    "1",
                    f"{item.path} is {choice}",
                    "categorical",
                )
                for choice in item.choices
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
                    for choice in item.choices
                )
    return tuple(definitions)


def build_tabular_features(
    candidate: CandidateInput,
    profile: TabularDatasetProfile,
) -> FeatureBundle:
    values: list[float] = []
    for item in profile.inputs:
        value = _get_path(candidate, item.path)
        if not item.main_effect:
            continue
        if item.kind == "number":
            numeric = float(value)
            if item.transform == "log1p":
                if numeric < 0:
                    raise ValueError(f"{item.path} must be non-negative for log1p transform")
                values.append(math.log1p(numeric))
            elif item.transform == "quadratic":
                values.extend((numeric, numeric * numeric))
            else:
                values.append(numeric)
        else:
            values.extend(float(value == choice) for choice in item.choices)
    if profile.interaction_axis_path is not None:
        axis_value = float(_get_path(candidate, profile.interaction_axis_path))
        for item in profile.inputs:
            if not item.interact_with_axis:
                continue
            value = _get_path(candidate, item.path)
            if item.kind == "number":
                values.append(axis_value * float(value))
            else:
                values.extend(axis_value * float(value == choice) for choice in item.choices)
    return FeatureBundle(
        pipeline_id=f"{profile.task_id}-profile-transform",
        pipeline_version="1.0.0",
        definitions=feature_definitions(profile),
        values=np.asarray(values, dtype=float),
    )


def candidate_from_observation(row: dict[str, Any], profile: TabularDatasetProfile) -> CandidateInput:
    return CandidateInput(
        name=str(row["id"]),
        inputs={
            "composition": dict(row["composition"] or {}),
            "process": dict(row["features"] or {}),
            "categorical": dict(row["categorical"] or {}),
            "heat_pattern": None,
        },
    )


def build_tabular_features_from_observation(
    row: dict[str, Any],
    _medians: dict[str, float],
    profile: TabularDatasetProfile,
) -> FeatureBundle:
    return build_tabular_features(candidate_from_observation(row, profile), profile)
