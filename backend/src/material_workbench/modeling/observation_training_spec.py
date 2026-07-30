"""Derive an Observation-family training spec from declared contracts.

Feature order, per-target feature sets, and target-to-family mapping are not
written down twice: they are derived from the Observation Profile's declared
families and the TaskDefinition's declared categorical choices.

What a Task must still declare explicitly is small and data-only, and lives in
``task_composition/builtin/welding.py`` next to the welding Task bindings:

- which Observation Profile document to load
- the feature-transform id and version that the Model Package pins
- the support policy id
- the physical output bounds used by Chain sampling (an exact allow-list; the
  TaskDefinition's ``plausibility_range`` is a display/validation range and is
  deliberately not reused as a runtime clamp)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from material_workbench.contracts.feature_contracts import FeatureDefinition
from material_workbench.contracts.task_contracts import TaskDefinition
from material_workbench.data.observation_profile import ObservationDatasetProfile


ONE_HOT_UNIT = "1"


class ObservationSpecError(ValueError):
    """The Profile and TaskDefinition do not agree on the training surface."""


@dataclass(frozen=True)
class ObservationRuntimeDeclaration:
    """The data-only bindings an Observation-family Task declares."""

    task_id: str
    profile_path: Path
    feature_transform_id: str
    feature_transform_version: str
    support_policy_id: str
    output_bounds: Mapping[str, tuple[float | None, float | None]]


@dataclass(frozen=True)
class ObservationTrainingSpec:
    declaration: ObservationRuntimeDeclaration
    profile: ObservationDatasetProfile
    pipeline_features: tuple[str, ...]
    feature_definitions: tuple[FeatureDefinition, ...]
    target_family: Mapping[str, str]
    target_features: Mapping[str, tuple[str, ...]]
    numeric_feature_paths: tuple[str, ...]
    categorical_choices: Mapping[str, tuple[str, ...]]

    @property
    def task_id(self) -> str:
        return self.declaration.task_id

    @property
    def feature_transform_id(self) -> str:
        return self.declaration.feature_transform_id

    @property
    def feature_transform_version(self) -> str:
        return self.declaration.feature_transform_version

    @property
    def support_policy_id(self) -> str:
        return self.declaration.support_policy_id

    def definition_for(self, name: str) -> FeatureDefinition:
        for item in self.feature_definitions:
            if item.name == name:
                return item
        raise ObservationSpecError(f"未宣言の特徴量です: {name}")

    def output_bounds_for(self, target: str) -> tuple[float | None, float | None]:
        try:
            return self.declaration.output_bounds[target]
        except KeyError as exc:
            raise ObservationSpecError(
                f"出力の物理境界が宣言されていません: {target}"
            ) from exc

    def feature_values(self, inputs: Mapping[str, Any]) -> dict[str, float]:
        """Expand canonical inputs into the declared numeric feature space."""

        values = {
            name: float(inputs[name])
            for name in self.numeric_feature_paths
            if inputs.get(name) is not None
        }
        for path, choices in self.categorical_choices.items():
            selected = inputs.get(path)
            if selected is None:
                continue
            values.update({
                f"{path}::{choice}": float(selected == choice) for choice in choices
            })
        return values


def _categorical_choices(task: TaskDefinition) -> dict[str, tuple[str, ...]]:
    return {
        field.path: tuple(field.choices)
        for group in task.input_groups
        for field in group.fields
        if field.kind == "categorical"
    }


def _feature_group(path: str) -> str:
    prefix = path.split(".", 1)[0]
    return prefix if prefix in {"composition", "process", "categorical"} else "other"


def observation_training_spec(
    declaration: ObservationRuntimeDeclaration,
    profile: ObservationDatasetProfile,
    task: TaskDefinition,
) -> ObservationTrainingSpec:
    if profile.task_id != declaration.task_id or task.id != declaration.task_id:
        raise ObservationSpecError(
            "Observation Profile、TaskDefinition、宣言のtask_idが一致しません"
        )
    choices = _categorical_choices(task)

    pipeline: list[str] = []
    numeric_paths: list[str] = []
    units: dict[str, str] = {}
    used_choices: dict[str, tuple[str, ...]] = {}
    target_family: dict[str, str] = {}
    target_features: dict[str, tuple[str, ...]] = {}

    for family in profile.families:
        family_features: list[str] = []
        for mapping in family.inputs:
            if mapping.kind == "numeric":
                if not mapping.canonical_unit:
                    raise ObservationSpecError(
                        f"数値入力にcanonical_unitがありません: {mapping.path}"
                    )
                names = (mapping.path,)
                units.setdefault(mapping.path, mapping.canonical_unit)
                if mapping.path not in numeric_paths:
                    numeric_paths.append(mapping.path)
            else:
                declared = choices.get(mapping.path)
                if not declared:
                    raise ObservationSpecError(
                        f"TaskDefinitionにカテゴリの選択肢がありません: {mapping.path}"
                    )
                used_choices[mapping.path] = declared
                names = tuple(f"{mapping.path}::{choice}" for choice in declared)
                for name in names:
                    units.setdefault(name, ONE_HOT_UNIT)
            for name in names:
                family_features.append(name)
                if name not in pipeline:
                    pipeline.append(name)
        for output in family.outputs:
            if output.key in target_family:
                raise ObservationSpecError(
                    f"同じ出力を複数のfamilyが宣言しています: {output.key}"
                )
            target_family[output.key] = family.id
            target_features[output.key] = tuple(family_features)

    declared_outputs = {item.key for item in task.outputs}
    if set(target_family) != declared_outputs:
        raise ObservationSpecError(
            "Observation Profileの出力とTaskDefinitionの出力が一致しません: "
            f"profile={sorted(target_family)}, task={sorted(declared_outputs)}"
        )
    missing_bounds = sorted(declared_outputs - set(declaration.output_bounds))
    if missing_bounds:
        raise ObservationSpecError(
            f"出力の物理境界が宣言されていません: {', '.join(missing_bounds)}"
        )

    return ObservationTrainingSpec(
        declaration=declaration,
        profile=profile,
        pipeline_features=tuple(pipeline),
        feature_definitions=tuple(
            FeatureDefinition(name, units[name], name, _feature_group(name))
            for name in pipeline
        ),
        target_family=target_family,
        target_features=target_features,
        numeric_feature_paths=tuple(numeric_paths),
        categorical_choices=used_choices,
    )
