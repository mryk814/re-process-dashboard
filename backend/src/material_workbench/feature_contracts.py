from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np


FeatureGroup = Literal["composition", "process", "categorical", "metallurgy", "heat_pattern", "other"]


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    unit: str
    meaning: str
    group: FeatureGroup
    label: str | None = None
    family: str | None = None
    formula: str | None = None
    caveat: str | None = None


@dataclass(frozen=True)
class FeatureValue:
    name: str
    value: float
    unit: str
    group: FeatureGroup


@dataclass(frozen=True)
class FeatureBundle:
    pipeline_id: str
    pipeline_version: str
    definitions: tuple[FeatureDefinition, ...]
    values: np.ndarray

    def __post_init__(self) -> None:
        if self.values.shape != (len(self.definitions),):
            raise ValueError("feature values must match the declared feature contract")
        if not np.isfinite(self.values).all():
            raise ValueError("feature values must be finite")
        if len({item.name for item in self.definitions}) != len(self.definitions):
            raise ValueError("feature names must be unique")
        self.values.setflags(write=False)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(item.name for item in self.definitions)

    @property
    def units(self) -> tuple[str, ...]:
        return tuple(item.unit for item in self.definitions)

    @property
    def features(self) -> tuple[FeatureValue, ...]:
        return tuple(
            FeatureValue(definition.name, float(value), definition.unit, definition.group)
            for definition, value in zip(self.definitions, self.values, strict=True)
        )

    def as_dict(self) -> dict[str, float]:
        return {item.name: item.value for item in self.features}

    def indices_by_group(self) -> dict[FeatureGroup, tuple[int, ...]]:
        groups: dict[FeatureGroup, list[int]] = {}
        for index, definition in enumerate(self.definitions):
            groups.setdefault(definition.group, []).append(index)
        return {group: tuple(indexes) for group, indexes in groups.items()}

    def explanation_rows(self) -> list[dict[str, str | float]]:
        return [
            {
                "name": definition.name,
                "label": definition.label,
                "value": float(value),
                "unit": definition.unit,
                "group": definition.group,
                "family": definition.family or definition.group,
                "formula": definition.formula or "",
                "meaning": definition.meaning,
                "caveat": definition.caveat or "",
            }
            for definition, value in zip(self.definitions, self.values, strict=True)
            if definition.label is not None
        ]


def feature_indices_by_group(
    definitions: Sequence[FeatureDefinition],
) -> dict[FeatureGroup, tuple[int, ...]]:
    groups: dict[FeatureGroup, list[int]] = {}
    for index, definition in enumerate(definitions):
        groups.setdefault(definition.group, []).append(index)
    return {group: tuple(indexes) for group, indexes in groups.items()}


def feature_index_families(
    definitions: Sequence[FeatureDefinition],
    families: dict[str, tuple[FeatureGroup, ...]],
) -> dict[str, tuple[int, ...]]:
    """Resolve model-specific distance families from semantic feature metadata."""
    semantic_groups = feature_indices_by_group(definitions)
    resolved: dict[str, tuple[int, ...]] = {}
    for family, members in families.items():
        indexes = tuple(index for member in members for index in semantic_groups.get(member, ()))
        if not indexes:
            raise ValueError(f"feature family {family!r} does not resolve to any features")
        resolved[family] = indexes
    return resolved
