"""Deterministic P0 empirical and kNN-local input samplers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from decision_workbench.design_priors.contracts import DesignPriorSampleEvidence
from decision_workbench.design_priors.loader import VerifiedDesignPriorPackage


@dataclass(frozen=True)
class PriorSample:
    values: dict[str, float | str]
    evidence: DesignPriorSampleEvidence


def sample_prior(
    package: VerifiedDesignPriorPackage,
    *,
    generator_id: str,
    lane: Literal["conservative", "balanced", "frontier"],
    count: int,
    seed: int,
    fixed_context: dict[str, float | str],
) -> list[PriorSample]:
    if count < 1:
        raise ValueError("Design Prior sample count must be positive")
    rows = [row for row in package.observations_for(generator_id).rows if all(row.inputs.get(path) == value for path, value in fixed_context.items())]
    if len(rows) < 2:
        raise ValueError("fixed contextに一致するDesign Prior観測が2件未満です")
    rng = np.random.default_rng(seed)
    if generator_id == "empirical_rows":
        return [_empirical(package, rows, lane=lane, seed=seed, index=int(rng.integers(len(rows)))) for _ in range(count)]
    if generator_id == "knn_local":
        return [_knn(package, rows, lane=lane, seed=seed, rng=rng) for _ in range(count)]
    raise ValueError(f"unregistered Design Prior generator: {generator_id}")


def _empirical(package: VerifiedDesignPriorPackage, rows: list, *, lane: str, seed: int, index: int) -> PriorSample:
    row = rows[index]
    return PriorSample(
        values=dict(row.inputs),
        evidence=_evidence(package, generator_id="empirical_rows", lane=lane, seed=seed, raw_id=row.sample_id),
    )


def _knn(package: VerifiedDesignPriorPackage, rows: list, *, lane: str, seed: int, rng: np.random.Generator) -> PriorSample:
    anchor_index = int(rng.integers(len(rows)))
    anchor = rows[anchor_index]
    numeric_paths = [path for path in package.manifest.canonical_input_paths if isinstance(anchor.inputs[path], (int, float)) and not isinstance(anchor.inputs[path], bool)]
    if not numeric_paths:
        return _empirical(package, rows, lane=lane, seed=seed, index=anchor_index)
    matrix = np.asarray([[float(row.inputs[path]) for path in numeric_paths] for row in rows], dtype=float)
    scale = np.maximum(matrix.max(axis=0) - matrix.min(axis=0), 1e-12)
    distances = np.sqrt(np.mean(((matrix - matrix[anchor_index]) / scale) ** 2, axis=1))
    categorical_paths = [path for path in package.manifest.canonical_input_paths if isinstance(anchor.inputs[path], str)]
    # P0 never synthesizes a category co-occurrence.  Local numeric perturbation
    # happens within the complete observed categorical combination.
    order = [
        int(index)
        for index in np.argsort(distances)
        if int(index) != anchor_index
        and all(rows[int(index)].inputs[path] == anchor.inputs[path] for path in categorical_paths)
    ]
    if not order:
        return _empirical(package, rows, lane=lane, seed=seed, index=anchor_index)
    neighbor_limit = next(item.max_neighbors for item in package.manifest.generators if item.generator_id == "knn_local")
    neighbor_index = order[int(rng.integers(min(len(order), neighbor_limit)))]
    neighbor = rows[neighbor_index]
    alpha_low, alpha_high, band = {
        "conservative": (0.0, 0.15, "typical"),
        "balanced": (0.15, 0.65, "near_edge"),
        "frontier": (1.0, 1.35, "low_density"),
    }[lane]
    alpha = float(rng.uniform(alpha_low, alpha_high))
    values = dict(anchor.inputs)
    for path in numeric_paths:
        values[path] = float((1.0 - alpha) * float(anchor.inputs[path]) + alpha * float(neighbor.inputs[path]))
    return PriorSample(
        values=values,
        evidence=_evidence(
            package,
            generator_id="knn_local",
            lane=lane,
            seed=seed,
            raw_id=anchor.sample_id,
            neighbor_id=neighbor.sample_id,
            distance=float(distances[neighbor_index]),
            band=band,
        ),
    )


def _evidence(package: VerifiedDesignPriorPackage, *, generator_id: str, lane: str, seed: int, raw_id: str, neighbor_id: str | None = None, distance: float | None = None, band: str = "typical") -> DesignPriorSampleEvidence:
    return DesignPriorSampleEvidence(
        package_id=package.manifest.package_id,
        package_version=package.manifest.package_version,
        manifest_digest=f"sha256:{package.manifest_sha256}",
        generator_id=generator_id,
        generator_version="1.0.0",
        lane=lane,
        seed=seed,
        raw_sample_id=raw_id,
        neighbor_sample_id=neighbor_id,
        nearest_neighbor_distance=distance,
        typicality_band=band,
    )
