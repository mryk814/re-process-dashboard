"""Allow-listed candidate generators that consume only a Design Space."""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import qmc

from material_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from material_workbench.contracts.schemas import Candidate


def _set_value(candidate: Candidate, path: str, value: float | str) -> Candidate:
    updated = candidate.model_copy(deep=True)
    parts = path.split(".")
    if (
        len(parts) == 3
        and parts[0] == "heat_pattern"
        and parts[1].isdigit()
        and parts[2] in {"time_s", "temperature_c"}
    ):
        points = updated.inputs.heat_pattern
        index = int(parts[1])
        if points is None or index >= len(points):
            raise ValueError(f"ヒートパターンに存在しない点です: {path}")
        setattr(points[index], parts[2], float(value))
        return updated
    if len(parts) != 2 or parts[0] not in {"composition", "process", "categorical"}:
        raise ValueError(f"候補生成の対象外です: {path}")
    values = getattr(updated.inputs, parts[0])
    values[parts[1]] = str(value) if parts[0] == "categorical" else float(value)
    return updated


def _latin_hypercube_unit(count: int, dimensions: int, seed: int) -> np.ndarray:
    """Preserve the existing permutation+jitter sequence for seeded LHS."""

    rng = np.random.default_rng(seed)
    columns = []
    for _ in range(dimensions):
        permutation = rng.permutation(count)
        columns.append((permutation + rng.random(count)) / count)
    return np.column_stack(columns) if columns else np.empty((count, 0))


def _sobol_unit(count: int, dimensions: int, seed: int) -> np.ndarray:
    if dimensions == 0:
        return np.empty((count, 0))
    sampler = qmc.Sobol(d=dimensions, scramble=True, seed=seed)
    power = math.ceil(math.log2(count))
    return sampler.random_base2(power)[:count]


def generate_candidates(
    generator_id: str,
    base: Candidate,
    design_space: DesignSpaceDefinition,
    *,
    count: int,
    seed: int,
) -> list[tuple[Candidate, dict[str, float | str]]]:
    numeric = (*design_space.numeric_domains, *design_space.heat_pattern_domains)
    categorical = design_space.categorical_domains
    dimensions = len(numeric) + len(categorical)
    if generator_id == "latin_hypercube":
        unit = _latin_hypercube_unit(count, dimensions, seed)
    elif generator_id == "sobol":
        unit = _sobol_unit(count, dimensions, seed)
    else:
        raise ValueError(f"未登録のCandidate Generatorです: {generator_id}")

    generated: list[tuple[Candidate, dict[str, float | str]]] = []
    for row_index in range(count):
        candidate = base.model_copy(deep=True)
        applied = dict(design_space.fixed_values)
        for path, value in design_space.fixed_values.items():
            candidate = _set_value(candidate, path, value)
        column = 0
        for domain in numeric:
            position = float(unit[row_index, column])
            column += 1
            if domain.range is not None:
                value = domain.range.min + position * (
                    domain.range.max - domain.range.min
                )
            else:
                value = domain.values[min(int(position * len(domain.values)), len(domain.values) - 1)]
            applied[domain.path] = float(value)
            candidate = _set_value(candidate, domain.path, float(value))
        for domain in categorical:
            position = float(unit[row_index, column])
            column += 1
            value = domain.choices[
                min(int(position * len(domain.choices)), len(domain.choices) - 1)
            ]
            applied[domain.path] = value
            candidate = _set_value(candidate, domain.path, value)
        for conditional in design_space.conditional_constraints:
            controller = _read_scalar(candidate, conditional.controller_path)
            if controller not in conditional.active_choices:
                for path, value in conditional.inactive_values.items():
                    applied[path] = value
                    candidate = _set_value(candidate, path, value)
        for constraint in design_space.composition_constraints:
            if constraint.balance_path is None:
                continue
            remainder = constraint.total - sum(
                float(_read_scalar(candidate, path))
                for path in constraint.component_paths
                if path != constraint.balance_path
            )
            applied[constraint.balance_path] = remainder
            candidate = _set_value(candidate, constraint.balance_path, remainder)
        generated.append((candidate, applied))
    return generated


def _read_scalar(candidate: Candidate, path: str) -> float | str:
    group, key = path.split(".", 1)
    return getattr(candidate.inputs, group)[key]
