"""Candidate validation against an immutable Project Design Space."""
from __future__ import annotations

import math
from collections.abc import Mapping

from decision_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
)


def _value_at(candidate: Candidate | CandidateInput, path: str) -> float | str:
    parts = path.split(".")
    if len(parts) == 2 and parts[0] in {"composition", "process", "categorical"}:
        values: Mapping[str, float | str] = getattr(candidate.inputs, parts[0])
        if parts[1] not in values:
            raise ValueError(f"Project Design Spaceに必要な入力がありません: {path}")
        return values[parts[1]]
    if (
        len(parts) == 3
        and parts[0] == "heat_pattern"
        and parts[1].isdigit()
        and parts[2] in {"time_s", "temperature_c"}
    ):
        points = candidate.inputs.heat_pattern
        index = int(parts[1])
        if points is None or index >= len(points):
            raise ValueError(f"Project Design Spaceに必要なヒートパターン点がありません: {path}")
        return float(getattr(points[index], parts[2]))
    raise ValueError(f"Project Design Spaceの入力パスを解決できません: {path}")


def _same(actual: float | str, expected: float | str, *, tolerance: float = 1e-9) -> bool:
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=0.0, abs_tol=tolerance)
    return actual == expected


def validate_candidate_in_design_space(
    candidate: Candidate | CandidateInput,
    design_space: DesignSpaceDefinition | None,
) -> None:
    """Reject candidates outside the Project-scoped scientific search space."""

    if design_space is None:
        return

    for path, expected in design_space.fixed_values.items():
        if not _same(_value_at(candidate, path), expected):
            raise ValueError(f"Project Design Spaceの固定値と一致しません: {path}")

    for domain in (*design_space.numeric_domains, *design_space.heat_pattern_domains):
        actual = _value_at(candidate, domain.path)
        if not isinstance(actual, (int, float)):
            raise ValueError(f"Project Design Spaceの数値項目ではありません: {domain.path}")
        numeric = float(actual)
        if domain.range is not None and not domain.range.min <= numeric <= domain.range.max:
            raise ValueError(f"Project Design Spaceの範囲外です: {domain.path}")
        if domain.values and not any(_same(numeric, allowed) for allowed in domain.values):
            raise ValueError(f"Project Design Spaceの候補値にありません: {domain.path}")

    for domain in design_space.categorical_domains:
        if _value_at(candidate, domain.path) not in domain.choices:
            raise ValueError(f"Project Design Spaceの選択肢にありません: {domain.path}")

    for conditional in design_space.conditional_constraints:
        controller = _value_at(candidate, conditional.controller_path)
        if controller not in conditional.active_choices:
            for path, expected in conditional.inactive_values.items():
                if not _same(_value_at(candidate, path), expected):
                    raise ValueError(f"Project Design Spaceの条件付き固定値と一致しません: {path}")

    operators = {
        "lt": lambda left, right: left < right,
        "lte": lambda left, right: left <= right,
        "gt": lambda left, right: left > right,
        "gte": lambda left, right: left >= right,
    }
    for constraint in design_space.relational_constraints:
        left = float(_value_at(candidate, constraint.left_path))
        right = float(_value_at(candidate, constraint.right_path))
        if not operators[constraint.operator](left, right):
            raise ValueError(constraint.message)

    for constraint in design_space.composition_constraints:
        total = sum(float(_value_at(candidate, path)) for path in constraint.component_paths)
        if not math.isclose(
            total,
            constraint.total,
            rel_tol=0.0,
            abs_tol=constraint.tolerance,
        ):
            raise ValueError(
                f"Project Design Spaceの組成合計が{constraint.total:g}{constraint.unit}になっていません"
            )

    if design_space.fixed_heat_pattern is not None:
        points = candidate.inputs.heat_pattern
        if points is None or len(points) != len(design_space.fixed_heat_pattern):
            raise ValueError("Project Design Spaceの固定ヒートパターンと一致しません")
        for index, expected in enumerate(design_space.fixed_heat_pattern):
            actual = points[index]
            for field in ("time_s", "temperature_c"):
                if field in expected and not _same(getattr(actual, field), expected[field]):
                    raise ValueError(
                        f"Project Design Spaceの固定ヒートパターンと一致しません: "
                        f"heat_pattern.{index}.{field}"
                    )
