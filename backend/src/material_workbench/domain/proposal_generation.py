"""Allow-listed candidate generators that consume only a Design Space."""
from __future__ import annotations

import math

import numpy as np
from scipy.stats import qmc

from material_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from material_workbench.contracts.candidate_project_contracts import Candidate


BOUNDED_SIMPLEX_GENERATOR_ID = "bounded_simplex_hit_and_run"
SUPPORTED_GENERATORS = {
    ("latin_hypercube", "1.0.0"),
    ("sobol", "1.0.0"),
    (BOUNDED_SIMPLEX_GENERATOR_ID, "1.0.0"),
}


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
    generator_version: str = "1.0.0",
    parameters: dict[str, float | str | bool] | None = None,
) -> list[tuple[Candidate, dict[str, float | str]]]:
    if (generator_id, generator_version) not in SUPPORTED_GENERATORS:
        raise ValueError(
            f"未登録のCandidate Generatorです: {generator_id}@{generator_version}"
        )
    numeric = (*design_space.numeric_domains, *design_space.heat_pattern_domains)
    categorical = design_space.categorical_domains
    dimensions = len(numeric) + len(categorical)
    if generator_id in {"latin_hypercube", BOUNDED_SIMPLEX_GENERATOR_ID}:
        unit = _latin_hypercube_unit(count, dimensions, seed)
    elif generator_id == "sobol":
        unit = _sobol_unit(count, dimensions, seed)
    else:
        raise ValueError(f"未登録のCandidate Generatorです: {generator_id}")

    generated: list[tuple[Candidate, dict[str, float | str]]] = []
    simplex_rows = (
        _sample_bounded_simplex_points(
            design_space,
            count=count,
            seed=seed,
            minimum_balance=float((parameters or {}).get("minimum_balance", 0.0)),
            burn_in_steps=int((parameters or {}).get("burn_in_steps", 256)),
            thinning_steps=int((parameters or {}).get("thinning_steps", 16)),
        )
        if generator_id == BOUNDED_SIMPLEX_GENERATOR_ID
        else None
    )
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
        if generator_id == BOUNDED_SIMPLEX_GENERATOR_ID:
            assert simplex_rows is not None
            simplex_values = simplex_rows[row_index]
            for path, value in simplex_values.items():
                candidate = _set_value(candidate, path, value)
            applied.update(simplex_values)
        else:
            candidate, simplex_values = _apply_balance_remainder(
                candidate,
                design_space,
            )
            applied.update(simplex_values)
        generated.append((candidate, applied))
    return generated


def _apply_balance_remainder(
    candidate: Candidate,
    design_space: DesignSpaceDefinition,
) -> tuple[Candidate, dict[str, float]]:
    applied: dict[str, float] = {}
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
    return candidate, applied


def bounded_simplex_compatibility(
    design_space: DesignSpaceDefinition,
    *,
    minimum_balance: float = 0.0,
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    constraints = [
        item for item in design_space.composition_constraints
        if item.balance_path is not None
    ]
    if len(constraints) != 1:
        reasons.append("balance成分を持つ組成合計制約が1件必要です")
        return False, tuple(reasons)
    constraint = constraints[0]
    domains = {item.path: item for item in design_space.numeric_domains}
    conditional_composition_paths = {
        path
        for item in design_space.conditional_constraints
        for path in item.inactive_values
        if path in constraint.component_paths
    }
    if conditional_composition_paths:
        reasons.append(
            "条件付きで固定される組成成分には未対応です: "
            + ", ".join(sorted(conditional_composition_paths))
        )
    varied = [
        path
        for path in constraint.component_paths
        if path != constraint.balance_path and path in domains
    ]
    if len(varied) < 2:
        reasons.append("balance以外の組成rangeを2項目以上選んでください")
    if any(domains[path].range is None for path in varied):
        reasons.append("bounded simplexは連続rangeの組成だけに対応します")
    balance_domain = domains.get(constraint.balance_path or "")
    if balance_domain is not None and balance_domain.range is None:
        reasons.append("balance成分を動かす場合は連続rangeにしてください")
    fixed_paths = [
        path
        for path in constraint.component_paths
        if path not in varied and path != constraint.balance_path
    ]
    missing_fixed = [
        path for path in fixed_paths if path not in design_space.fixed_values
    ]
    if missing_fixed:
        reasons.append(
            "range外の組成成分は固定値を指定してください: "
            + ", ".join(missing_fixed)
        )
    if not reasons:
        fixed_sum = sum(
            float(design_space.fixed_values[path]) for path in fixed_paths
        )
        lower_sum = sum(domains[path].range.min for path in varied)  # type: ignore[union-attr]
        upper_sum = sum(domains[path].range.max for path in varied)  # type: ignore[union-attr]
        balance_lower = max(
            minimum_balance,
            balance_domain.range.min
            if balance_domain is not None and balance_domain.range is not None
            else 0.0,
        )
        balance_upper = (
            balance_domain.range.max
            if balance_domain is not None and balance_domain.range is not None
            else constraint.total
        )
        if (
            fixed_sum + lower_sum + balance_lower
            > constraint.total + constraint.tolerance
            or fixed_sum + upper_sum + balance_upper
            < constraint.total - constraint.tolerance
        ):
            reasons.append(
                "組成rangeの上下限では合計制約を満たす点を構成できません"
            )
    return not reasons, tuple(reasons)


def _bounded_simplex_bounds(
    design_space: DesignSpaceDefinition,
    *,
    minimum_balance: float,
) -> tuple[list[str], np.ndarray, np.ndarray, float, float]:
    compatible, reasons = bounded_simplex_compatibility(
        design_space,
        minimum_balance=minimum_balance,
    )
    if not compatible:
        raise ValueError(" / ".join(reasons))
    constraint = next(
        item
        for item in design_space.composition_constraints
        if item.balance_path is not None
    )
    assert constraint.balance_path is not None
    domains = {item.path: item for item in design_space.numeric_domains}
    variable_paths = [
        path
        for path in constraint.component_paths
        if path != constraint.balance_path and path in domains
    ]
    fixed_sum = sum(
        float(design_space.fixed_values[path])
        for path in constraint.component_paths
        if path not in variable_paths and path != constraint.balance_path
    )
    balance_domain = domains.get(constraint.balance_path)
    balance_lower = max(
        minimum_balance,
        balance_domain.range.min
        if balance_domain is not None and balance_domain.range is not None
        else 0.0,
    )
    balance_upper = (
        balance_domain.range.max
        if balance_domain is not None and balance_domain.range is not None
        else constraint.total
    )
    free_paths = [*variable_paths, constraint.balance_path]
    lower = np.array(
        [
            domains[path].range.min
            if path != constraint.balance_path
            else balance_lower
            for path in free_paths
        ],
        dtype=float,
    )
    upper = np.array(
        [
            domains[path].range.max
            if path != constraint.balance_path
            else balance_upper
            for path in free_paths
        ],
        dtype=float,
    )
    free_total = constraint.total - fixed_sum
    return free_paths, lower, upper, free_total, constraint.tolerance


def _sample_bounded_simplex_points(
    design_space: DesignSpaceDefinition,
    *,
    count: int,
    seed: int,
    minimum_balance: float,
    burn_in_steps: int,
    thinning_steps: int,
) -> list[dict[str, float]]:
    """Sample the box-bounded simplex with deterministic hit-and-run.

    Directions live in the sum-zero subspace, so every step preserves the
    composition total. Unlike rejection sampling, runtime does not depend on
    the bounded polytope's volume inside an unconstrained simplex.
    """

    if burn_in_steps < 1 or thinning_steps < 1:
        raise ValueError("hit-and-runのburn-in/thinningは1以上が必要です")
    paths, lower, upper, total, tolerance = _bounded_simplex_bounds(
        design_space,
        minimum_balance=minimum_balance,
    )
    capacity = upper - lower
    residual = total - float(lower.sum())
    if residual <= tolerance:
        point = lower.copy()
    else:
        point = lower + residual * capacity / float(capacity.sum())
    rng = np.random.default_rng(np.random.SeedSequence([seed, 213]))
    samples: list[dict[str, float]] = []
    total_steps = burn_in_steps + count * thinning_steps
    for step in range(total_steps):
        direction = rng.normal(size=len(paths))
        direction -= direction.mean()
        norm = float(np.linalg.norm(direction))
        if norm <= 1e-15:
            continue
        direction /= norm
        t_min = -math.inf
        t_max = math.inf
        for value, low, high, delta in zip(
            point, lower, upper, direction, strict=True
        ):
            if delta > 1e-15:
                t_min = max(t_min, (low - value) / delta)
                t_max = min(t_max, (high - value) / delta)
            elif delta < -1e-15:
                t_min = max(t_min, (high - value) / delta)
                t_max = min(t_max, (low - value) / delta)
        if t_max < t_min - tolerance:
            raise ValueError("bounded simplexのhit-and-run区間を構成できません")
        point = point + rng.uniform(t_min, t_max) * direction
        point = np.minimum(upper, np.maximum(lower, point))
        # Correct floating drift on the final component without changing the
        # sampled geometry at material scale.
        point[-1] += total - float(point.sum())
        if (
            step >= burn_in_steps
            and (step - burn_in_steps) % thinning_steps == 0
        ):
            samples.append(
                {
                    path: float(value)
                    for path, value in zip(paths, point, strict=True)
                }
            )
            if len(samples) == count:
                break
    if len(samples) != count:
        raise ValueError("bounded simplexのhit-and-run標本数が不足しました")
    return samples


def _read_scalar(candidate: Candidate, path: str) -> float | str:
    group, key = path.split(".", 1)
    return getattr(candidate.inputs, group)[key]
