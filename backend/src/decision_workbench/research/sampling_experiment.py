from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from time import perf_counter
from typing import Literal

import numpy as np

from decision_workbench.domain.proposal_generation import (
    _latin_hypercube_unit,
    _sobol_unit,
)


SamplingMethod = Literal["latin_hypercube", "sobol", "grid_2d_helper"]
SAMPLING_VERSION = "sampling-experiment/v1"


@dataclass(frozen=True)
class ExperimentFixture:
    fixture_id: str
    dimensions: int
    numeric_variables: int
    categorical_variables: int = 0
    list_variables: int = 0
    conditional_constraints: int = 0
    composition_constraints: int = 0
    relational_constraints: int = 0
    high_rejection: bool = False

    @property
    def applicable_methods(self) -> tuple[SamplingMethod, ...]:
        methods: list[SamplingMethod] = ["latin_hypercube", "sobol"]
        if self.dimensions <= 2:
            methods.append("grid_2d_helper")
        return tuple(methods)


@dataclass(frozen=True)
class SamplingMetrics:
    generated: int
    feasible_unique: int
    rejection_rate: float
    marginal_bin_coverage: float
    mean_nearest_distance: float | None
    best_objective: float | None
    runtime_ms: float
    model_calls: int
    support_rate: float | None
    selected_diversity: float | None


@dataclass(frozen=True)
class SampleRecord:
    sample_id: str
    point: tuple[float, ...]
    tranche: int
    position: int


@dataclass(frozen=True)
class SamplingRevision:
    family_id: str
    revision_id: str
    parent_revision_id: str | None
    method: SamplingMethod
    method_version: str
    seed: int
    fixture_id: str
    requested_generated: int
    samples: tuple[SampleRecord, ...]
    new_sample_ids: tuple[str, ...]
    saved_proposal_snapshot_id: str | None


FIXTURES = (
    ExperimentFixture("numeric-1d", 1, 1),
    ExperimentFixture("numeric-2d", 2, 2),
    ExperimentFixture("numeric-6d", 6, 6),
    ExperimentFixture(
        "mixed-6d",
        6,
        4,
        categorical_variables=1,
        list_variables=1,
        conditional_constraints=1,
    ),
    ExperimentFixture("numeric-10d", 10, 10),
    ExperimentFixture(
        "high-rejection-6d",
        6,
        6,
        conditional_constraints=1,
        composition_constraints=1,
        relational_constraints=1,
        high_rejection=True,
    ),
)


def sample_unit(
    method: SamplingMethod,
    *,
    count: int,
    dimensions: int,
    seed: int,
    offset: int = 0,
    family_budget: int | None = None,
) -> np.ndarray:
    if count < 1:
        raise ValueError("count must be positive")
    if dimensions < 1:
        raise ValueError("dimensions must be positive")
    if method == "sobol":
        return _sobol_unit(count + offset, dimensions, seed)[offset:]
    if method == "latin_hypercube":
        tranche_seed = _derived_seed(seed, offset)
        return _latin_hypercube_unit(count, dimensions, tranche_seed)
    if method == "grid_2d_helper":
        if dimensions > 2:
            raise ValueError("grid_2d_helper is limited to one or two dimensions")
        total = family_budget or count + offset
        return _grid_unit(total, dimensions, seed)[offset : offset + count]
    raise ValueError(f"unknown sampling method: {method}")


def _grid_unit(count: int, dimensions: int, seed: int) -> np.ndarray:
    side = count if dimensions == 1 else int(np.ceil(np.sqrt(count)))
    axis = (np.arange(side, dtype=float) + 0.5) / side
    if dimensions == 1:
        points = axis[:, None]
    else:
        points = np.asarray(
            [(x, y) for y in axis for x in axis],
            dtype=float,
        )
    permutation = np.random.default_rng(seed).permutation(len(points))
    return points[permutation][:count]


def _derived_seed(seed: int, offset: int) -> int:
    sequence = np.random.SeedSequence([seed, offset, 546])
    return int(sequence.generate_state(1, dtype=np.uint32)[0])


def _is_feasible(points: np.ndarray, fixture: ExperimentFixture) -> np.ndarray:
    if fixture.high_rejection:
        composition_total = np.sum(points[:, :3], axis=1)
        composition = (composition_total >= 1.20) & (composition_total <= 1.40)
        relation = points[:, 0] + points[:, 1] <= 1.10
        conditional = (points[:, 2] <= 0.50) | (points[:, 3] >= 0.65)
        return composition & relation & conditional
    if fixture.dimensions == 1:
        return points[:, 0] <= 0.9
    return points[:, 0] + points[:, 1] <= 1.55


def _materialize_domain_values(
    points: np.ndarray,
    fixture: ExperimentFixture,
) -> np.ndarray:
    materialized = points.copy()
    categorical_start = fixture.numeric_variables
    categorical_end = categorical_start + fixture.categorical_variables
    if fixture.categorical_variables:
        categorical = materialized[:, categorical_start:categorical_end]
        materialized[:, categorical_start:categorical_end] = (
            np.floor(categorical * 3).clip(0, 2) / 2
        )
    list_end = categorical_end + fixture.list_variables
    if fixture.list_variables:
        listed = materialized[:, categorical_end:list_end]
        materialized[:, categorical_end:list_end] = (
            np.floor(listed * 4).clip(0, 3) / 3
        )
    if fixture.conditional_constraints and fixture.categorical_variables:
        inactive = materialized[:, categorical_start] == 0
        materialized[inactive, categorical_end:list_end] = 0.5
    return materialized


def _canonical_point(point: np.ndarray) -> tuple[float, ...]:
    return tuple(float(value) for value in np.round(point, decimals=12))


def _objective(
    points: np.ndarray,
    fixture: ExperimentFixture,
) -> np.ndarray:
    target = np.linspace(0.25, 0.75, fixture.dimensions)
    weights = np.linspace(1.0, 2.0, fixture.dimensions)
    delta = points - target
    categorical_start = fixture.numeric_variables
    categorical_end = categorical_start + fixture.categorical_variables
    if fixture.categorical_variables:
        target[categorical_start:categorical_end] = 0.5
        delta[:, categorical_start:categorical_end] = (
            points[:, categorical_start:categorical_end]
            != target[categorical_start:categorical_end]
        )
    return np.sum(weights * delta**2, axis=1)


def _support_mask(
    points: np.ndarray,
    fixture: ExperimentFixture,
) -> np.ndarray:
    numeric = points[:, : fixture.numeric_variables]
    return np.all((numeric >= 0.10) & (numeric <= 0.90), axis=1)


def _distance_matrix(
    left: np.ndarray,
    right: np.ndarray,
    fixture: ExperimentFixture,
) -> np.ndarray:
    delta = np.abs(left[:, None, :] - right[None, :, :])
    categorical_start = fixture.numeric_variables
    categorical_end = categorical_start + fixture.categorical_variables
    if fixture.categorical_variables:
        delta[:, :, categorical_start:categorical_end] = (
            left[:, None, categorical_start:categorical_end]
            != right[None, :, categorical_start:categorical_end]
        )
    return np.sqrt(np.mean(delta**2, axis=2))


def _mean_nearest_distance(
    points: np.ndarray,
    fixture: ExperimentFixture,
) -> float | None:
    if len(points) < 2:
        return None
    distances = _distance_matrix(points, points, fixture)
    np.fill_diagonal(distances, np.inf)
    return float(np.mean(np.min(distances, axis=1)))


def _marginal_coverage(
    points: np.ndarray,
    fixture: ExperimentFixture,
    bins: int = 8,
) -> float:
    if not len(points):
        return 0.0
    occupied = []
    for index, column in enumerate(points.T):
        if (
            fixture.numeric_variables
            <= index
            < fixture.numeric_variables + fixture.categorical_variables
        ):
            occupied.append(len(np.unique(column)) / 3)
            continue
        if index >= (
            fixture.numeric_variables + fixture.categorical_variables
        ):
            list_levels = 5 if fixture.conditional_constraints else 4
            occupied.append(len(np.unique(column)) / list_levels)
            continue
        indices = np.minimum((column * bins).astype(int), bins - 1)
        occupied.append(len(np.unique(indices)) / bins)
    return float(np.mean(occupied))


def _select_diverse(
    points: np.ndarray,
    objective: np.ndarray,
    fixture: ExperimentFixture,
    count: int = 8,
) -> np.ndarray:
    if not len(points):
        return points
    selected = [int(np.argmin(objective))]
    remaining = set(range(len(points))) - set(selected)
    while remaining and len(selected) < count:
        next_index = max(
            remaining,
            key=lambda index: (
                min(
                    float(
                        _distance_matrix(
                            points[index : index + 1],
                            points[prior : prior + 1],
                            fixture,
                        )[0, 0]
                    )
                    for prior in selected
                ),
                -float(objective[index]),
            ),
        )
        selected.append(next_index)
        remaining.remove(next_index)
    return points[selected]


def _pairwise_diversity(
    points: np.ndarray,
    fixture: ExperimentFixture,
) -> float | None:
    if len(points) < 2:
        return None
    upper = np.triu_indices(len(points), k=1)
    distances = _distance_matrix(points, points, fixture)
    return float(np.mean(distances[upper]))


def _metrics_from_points(
    points: np.ndarray,
    fixture: ExperimentFixture,
    *,
    generated: int,
    runtime_ms: float,
) -> SamplingMetrics:
    objective = (
        _objective(points, fixture)
        if len(points)
        else np.asarray([], dtype=float)
    )
    selected = _select_diverse(points, objective, fixture)
    return SamplingMetrics(
        generated=generated,
        feasible_unique=len(points),
        rejection_rate=1.0 - len(points) / generated,
        marginal_bin_coverage=_marginal_coverage(points, fixture),
        mean_nearest_distance=_mean_nearest_distance(points, fixture),
        best_objective=float(np.min(objective)) if len(objective) else None,
        runtime_ms=runtime_ms,
        model_calls=len(points),
        support_rate=(
            float(np.mean(_support_mask(points, fixture)))
            if len(points)
            else None
        ),
        selected_diversity=_pairwise_diversity(selected, fixture),
    )


def evaluate_sampling(
    method: SamplingMethod,
    fixture: ExperimentFixture,
    *,
    budget: int,
    seed: int,
) -> tuple[SamplingMetrics, tuple[SampleRecord, ...]]:
    started = perf_counter()
    raw = sample_unit(
        method,
        count=budget,
        dimensions=fixture.dimensions,
        seed=seed,
        family_budget=budget,
    )
    raw = _materialize_domain_values(raw, fixture)
    feasible = raw[_is_feasible(raw, fixture)]
    unique_points = tuple(dict.fromkeys(_canonical_point(point) for point in feasible))
    points = np.asarray(unique_points, dtype=float)
    if points.size == 0:
        points = np.empty((0, fixture.dimensions))
    records = tuple(
        SampleRecord(
            sample_id=_sample_id(method, seed, 0, position, point),
            point=point,
            tranche=0,
            position=position,
        )
        for position, point in enumerate(unique_points)
    )
    metrics = _metrics_from_points(
        points,
        fixture,
        generated=budget,
        runtime_ms=0.0,
    )
    metrics = replace(
        metrics,
        runtime_ms=(perf_counter() - started) * 1000,
    )
    return metrics, records


def _sample_id(
    method: SamplingMethod,
    seed: int,
    tranche: int,
    position: int,
    point: tuple[float, ...],
) -> str:
    evidence = f"{method}|{seed}|{tranche}|{position}|{point}"
    return sha256(evidence.encode("utf-8")).hexdigest()[:20]


def _digest(*parts: object) -> str:
    evidence = "|".join(str(part) for part in parts)
    return sha256(evidence.encode("utf-8")).hexdigest()


def create_initial_revision(
    method: SamplingMethod,
    fixture: ExperimentFixture,
    *,
    budget: int,
    seed: int,
    saved_proposal_snapshot_id: str | None = None,
) -> SamplingRevision:
    _, records = evaluate_sampling(method, fixture, budget=budget, seed=seed)
    family_id = _digest(method, SAMPLING_VERSION, seed, fixture.fixture_id)
    revision_id = _digest(family_id, 0, tuple(record.sample_id for record in records))
    return SamplingRevision(
        family_id=family_id,
        revision_id=revision_id,
        parent_revision_id=None,
        method=method,
        method_version=SAMPLING_VERSION,
        seed=seed,
        fixture_id=fixture.fixture_id,
        requested_generated=budget,
        samples=records,
        new_sample_ids=tuple(record.sample_id for record in records),
        saved_proposal_snapshot_id=saved_proposal_snapshot_id,
    )


def append_revision(
    prior: SamplingRevision,
    fixture: ExperimentFixture,
    *,
    additional_budget: int,
) -> SamplingRevision:
    if prior.fixture_id != fixture.fixture_id:
        raise ValueError("fixture cannot change within a sampling family")
    tranche = 1 + max((sample.tranche for sample in prior.samples), default=-1)
    raw = sample_unit(
        prior.method,
        count=additional_budget,
        dimensions=fixture.dimensions,
        seed=prior.seed,
        offset=prior.requested_generated,
        family_budget=prior.requested_generated + additional_budget,
    )
    raw = _materialize_domain_values(raw, fixture)
    feasible = raw[_is_feasible(raw, fixture)]
    existing_points = {sample.point for sample in prior.samples}
    new_records: list[SampleRecord] = []
    for position, point_array in enumerate(feasible):
        point = _canonical_point(point_array)
        if point in existing_points:
            continue
        existing_points.add(point)
        new_records.append(
            SampleRecord(
                sample_id=_sample_id(
                    prior.method,
                    prior.seed,
                    tranche,
                    position,
                    point,
                ),
                point=point,
                tranche=tranche,
                position=position,
            )
        )
    samples = (*prior.samples, *new_records)
    requested_generated = prior.requested_generated + additional_budget
    revision_id = _digest(
        prior.family_id,
        prior.revision_id,
        requested_generated,
        tuple(record.sample_id for record in new_records),
    )
    return SamplingRevision(
        family_id=prior.family_id,
        revision_id=revision_id,
        parent_revision_id=prior.revision_id,
        method=prior.method,
        method_version=prior.method_version,
        seed=prior.seed,
        fixture_id=prior.fixture_id,
        requested_generated=requested_generated,
        samples=samples,
        new_sample_ids=tuple(record.sample_id for record in new_records),
        saved_proposal_snapshot_id=prior.saved_proposal_snapshot_id,
    )


def _revision_evidence(revision: SamplingRevision) -> dict[str, object]:
    return {
        "family_id": revision.family_id,
        "revision_id": revision.revision_id,
        "parent_revision_id": revision.parent_revision_id,
        "method": revision.method,
        "method_version": revision.method_version,
        "seed": revision.seed,
        "fixture_id": revision.fixture_id,
        "requested_generated": revision.requested_generated,
        "sample_count": len(revision.samples),
        "sample_ids": [sample.sample_id for sample in revision.samples],
        "new_sample_ids": list(revision.new_sample_ids),
        "sample_set_digest": _digest(
            tuple((sample.sample_id, sample.point) for sample in revision.samples)
        ),
        "saved_proposal_snapshot_id": revision.saved_proposal_snapshot_id,
    }


def run_comparison(
    *,
    budget: int = 128,
    seeds: tuple[int, ...] = (546, 547, 548, 549, 550, 551, 552, 553),
) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for fixture in FIXTURES:
        for method in fixture.applicable_methods:
            for seed in seeds:
                metrics, _ = evaluate_sampling(
                    method,
                    fixture,
                    budget=budget,
                    seed=seed,
                )
                rows.append(
                    {
                        "fixture": asdict(fixture),
                        "method": method,
                        "method_version": SAMPLING_VERSION,
                        "budget": budget,
                        "seed": seed,
                        "metrics": asdict(metrics),
                    }
                )

    sequential: list[dict[str, object]] = []
    fixture_by_id = {fixture.fixture_id: fixture for fixture in FIXTURES}
    for fixture_id, fixture in fixture_by_id.items():
        for method in fixture.applicable_methods:
            seed = seeds[0]
            sequential_started = perf_counter()
            initial = create_initial_revision(
                method,
                fixture,
                budget=budget // 2,
                seed=seed,
                saved_proposal_snapshot_id="saved-proposal-before-addition",
            )
            appended = append_revision(
                initial,
                fixture,
                additional_budget=budget - budget // 2,
            )
            sequential_points = np.asarray(
                [sample.point for sample in appended.samples],
                dtype=float,
            ).reshape((-1, fixture.dimensions))
            sequential_metrics = _metrics_from_points(
                sequential_points,
                fixture,
                generated=budget,
                runtime_ms=0.0,
            )
            sequential_metrics = replace(
                sequential_metrics,
                runtime_ms=(perf_counter() - sequential_started) * 1000,
            )
            one_shot_metrics, one_shot = evaluate_sampling(
                method,
                fixture,
                budget=budget,
                seed=seed,
            )
            sequential.append(
                {
                    "fixture_id": fixture_id,
                    "method": method,
                    "budget": budget,
                    "split": [budget // 2, budget - budget // 2],
                    "one_shot_feasible_unique": len(one_shot),
                    "sequential_feasible_unique": len(appended.samples),
                    "one_shot_metrics": asdict(one_shot_metrics),
                    "sequential_metrics": asdict(sequential_metrics),
                    "same_family": initial.family_id == appended.family_id,
                    "parent_revision_preserved": (
                        appended.parent_revision_id == initial.revision_id
                    ),
                    "prior_sample_ids_preserved": (
                        tuple(sample.sample_id for sample in appended.samples[: len(initial.samples)])
                        == tuple(sample.sample_id for sample in initial.samples)
                    ),
                    "duplicate_points": len(appended.samples)
                    - len({sample.point for sample in appended.samples}),
                    "saved_proposal_snapshot_preserved": (
                        appended.saved_proposal_snapshot_id
                        == initial.saved_proposal_snapshot_id
                    ),
                    "one_shot_point_set_match": (
                        {sample.point for sample in one_shot}
                        == {sample.point for sample in appended.samples}
                    ),
                    "initial_revision": _revision_evidence(initial),
                    "appended_revision": _revision_evidence(appended),
                }
            )
    return {
        "schema_version": SAMPLING_VERSION,
        "purpose": "research_only_sampling_comparison",
        "production_ui_changed": False,
        "budget": budget,
        "seeds": list(seeds),
        "results": rows,
        "sequential_comparison": sequential,
        "decision": {
            "sequential_sampling": "adopt_sobol_prefix_as_candidate",
            "one_shot_sampling": "retain_lhs_and_sobol",
            "grid_2d_helper": "do_not_adopt",
            "automatic_budget_expansion": "do_not_adopt",
            "production_change_in_this_experiment": False,
        },
    }
