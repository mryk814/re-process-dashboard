"""Deterministic P0 empirical and kNN-local input samplers."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

import numpy as np

from decision_workbench.design_priors.contracts import DesignPriorSampleEvidence
from decision_workbench.design_priors.loader import VerifiedDesignPriorPackage


@dataclass(frozen=True)
class PriorSample:
    values: dict[str, float | str]
    evidence: DesignPriorSampleEvidence


_LANE_ALPHA_RANGES = {
    "conservative": (0.0, 0.15),
    "balanced": (0.15, 0.65),
    "frontier": (1.0, 1.35),
}
_DISTANCE_ID = "canonical-mixed-range-rms"
_DISTANCE_VERSION = "1.0.0"
_TYPICAL_MAX_DISTANCE = 0.05
_NEAR_EDGE_MAX_DISTANCE = 0.20


def lane_parameter_digest(
    generator_id: str,
    lane: Literal["conservative", "balanced", "frontier"],
) -> str:
    """Identify all versioned sampling parameters that give a lane meaning."""

    parameters: dict[str, object] = {
        "generator_id": generator_id,
        "generator_version": "1.0.0",
        "lane": lane,
        "category_policy": "complete-observed-combination",
        "distance_id": _DISTANCE_ID,
        "distance_version": _DISTANCE_VERSION,
        "typicality_thresholds": {
            "typical_max": _TYPICAL_MAX_DISTANCE,
            "near_edge_max": _NEAR_EDGE_MAX_DISTANCE,
        },
    }
    if generator_id == "knn_local":
        parameters["alpha_range"] = _LANE_ALPHA_RANGES[lane]
    elif generator_id == "empirical_rows":
        parameters["selection_policy"] = "uniform-observed-row"
    else:
        raise ValueError(f"unregistered Design Prior generator: {generator_id}")
    payload = json.dumps(
        parameters, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


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


def sample_conditional_completions(
    package: VerifiedDesignPriorPackage,
    *,
    generator_id: Literal["empirical_rows", "knn_local"],
    count: int,
    seed: int,
    observed: dict[str, float | str],
    missing_paths: tuple[str, ...],
) -> list[PriorSample]:
    """Complete only missing inputs; observed candidate values remain authoritative."""

    canonical = set(package.manifest.canonical_input_paths)
    if not missing_paths or not set(missing_paths) <= canonical:
        raise ValueError("completion paths must be non-empty canonical Design Prior inputs")
    if set(observed) & set(missing_paths):
        raise ValueError("observed and missing completion paths must be disjoint")
    rows = list(package.observations_for(generator_id).rows)
    numeric = set(package.quality_report.numeric_paths)
    categorical_observed = {
        path: value for path, value in observed.items() if path not in numeric
    }
    compatible = [
        row
        for row in rows
        if all(row.inputs.get(path) == value for path, value in categorical_observed.items())
    ]
    if len(compatible) < 2:
        raise ValueError("observed category contextに一致するDesign Prior観測が2件未満です")
    numeric_observed = [path for path in observed if path in numeric]
    scales = {
        path: max(
            max(float(row.inputs[path]) for row in compatible)
            - min(float(row.inputs[path]) for row in compatible),
            1e-12,
        )
        for path in numeric_observed
    }

    def distance(row: object) -> float:
        if not numeric_observed:
            return 0.0
        inputs = row.inputs  # type: ignore[attr-defined]
        return float(np.sqrt(np.mean([
            ((float(inputs[path]) - float(observed[path])) / scales[path]) ** 2
            for path in numeric_observed
        ])))

    nearest = sorted(compatible, key=lambda row: (distance(row), row.sample_id))
    neighbor_count = min(
        len(nearest),
        next(
            item.max_neighbors
            for item in package.manifest.generators
            if item.generator_id == generator_id
        ),
    )
    neighbors = nearest[:neighbor_count]
    rng = np.random.default_rng(seed)
    samples: list[PriorSample] = []
    for _ in range(count):
        anchor = neighbors[int(rng.integers(len(neighbors)))]
        values = {
            path: anchor.inputs[path]
            for path in missing_paths
        }
        neighbor_id = None
        if generator_id == "knn_local":
            other = neighbors[int(rng.integers(len(neighbors)))]
            neighbor_id = other.sample_id
            alpha = float(rng.uniform(0.15, 0.65))
            for path in missing_paths:
                if path in numeric:
                    values[path] = (
                        (1.0 - alpha) * float(anchor.inputs[path])
                        + alpha * float(other.inputs[path])
                    )
        samples.append(
            PriorSample(
                values=values,
                evidence=_evidence(
                    package,
                    generator_id=generator_id,
                    lane="balanced",
                    seed=seed,
                    raw_id=anchor.sample_id,
                    neighbor_id=neighbor_id,
                    distance=distance(anchor),
                    band=_typicality_band(distance(anchor)),
                ),
            )
        )
    return samples


def _empirical(package: VerifiedDesignPriorPackage, rows: list, *, lane: str, seed: int, index: int) -> PriorSample:
    row = rows[index]
    return PriorSample(
        values=dict(row.inputs),
        evidence=_evidence(package, generator_id="empirical_rows", lane=lane, seed=seed, raw_id=row.sample_id),
    )


def _knn(package: VerifiedDesignPriorPackage, rows: list, *, lane: str, seed: int, rng: np.random.Generator) -> PriorSample:
    anchor_index = int(rng.integers(len(rows)))
    anchor = rows[anchor_index]
    numeric_paths = list(package.quality_report.numeric_paths)
    if not numeric_paths:
        return _knn_degenerate(package, rows, lane=lane, seed=seed, anchor_index=anchor_index)
    matrix = np.asarray([[float(row.inputs[path]) for path in numeric_paths] for row in rows], dtype=float)
    scale = np.maximum(matrix.max(axis=0) - matrix.min(axis=0), 1e-12)
    distances = np.sqrt(np.mean(((matrix - matrix[anchor_index]) / scale) ** 2, axis=1))
    categorical_paths = [
        path
        for path in package.manifest.canonical_input_paths
        if path not in numeric_paths
    ]
    # P0 never synthesizes a category co-occurrence.  Local numeric perturbation
    # happens within the complete observed categorical combination.
    order = [
        int(index)
        for index in np.argsort(distances)
        if int(index) != anchor_index
        and all(rows[int(index)].inputs[path] == anchor.inputs[path] for path in categorical_paths)
    ]
    if not order:
        return _knn_degenerate(package, rows, lane=lane, seed=seed, anchor_index=anchor_index)
    neighbor_limit = next(item.max_neighbors for item in package.manifest.generators if item.generator_id == "knn_local")
    neighbor_index = order[int(rng.integers(min(len(order), neighbor_limit)))]
    neighbor = rows[neighbor_index]
    alpha_low, alpha_high = _LANE_ALPHA_RANGES[lane]
    alpha = float(rng.uniform(alpha_low, alpha_high))
    values = dict(anchor.inputs)
    for path in numeric_paths:
        values[path] = float((1.0 - alpha) * float(anchor.inputs[path]) + alpha * float(neighbor.inputs[path]))
    nearest_distance = _nearest_observation_distance(
        values,
        rows,
        canonical_paths=package.manifest.canonical_input_paths,
        numeric_paths=tuple(numeric_paths),
    )
    return PriorSample(
        values=values,
        evidence=_evidence(
            package,
            generator_id="knn_local",
            lane=lane,
            seed=seed,
            raw_id=anchor.sample_id,
            neighbor_id=neighbor.sample_id,
            distance=nearest_distance,
            band=_typicality_band(nearest_distance),
        ),
    )


def _knn_degenerate(
    package: VerifiedDesignPriorPackage,
    rows: list,
    *,
    lane: str,
    seed: int,
    anchor_index: int,
) -> PriorSample:
    anchor = rows[anchor_index]
    return PriorSample(
        values=dict(anchor.inputs),
        evidence=_evidence(
            package,
            generator_id="knn_local",
            lane=lane,
            seed=seed,
            raw_id=anchor.sample_id,
            distance=0.0,
            band="typical",
        ),
    )


def _nearest_observation_distance(
    values: dict[str, float | str],
    rows: list,
    *,
    canonical_paths: tuple[str, ...],
    numeric_paths: tuple[str, ...],
) -> float:
    numeric_path_set = set(numeric_paths)
    scales = {
        path: max(
            float(max(float(row.inputs[path]) for row in rows))
            - float(min(float(row.inputs[path]) for row in rows)),
            1e-12,
        )
        for path in numeric_paths
    }
    distances: list[float] = []
    for row in rows:
        components = [
            (
                (float(values[path]) - float(row.inputs[path])) / scales[path]
                if path in numeric_path_set
                else 0.0
                if values[path] == row.inputs[path]
                else 1.0
            )
            ** 2
            for path in canonical_paths
        ]
        distances.append(float(np.sqrt(np.mean(components))))
    return min(distances)


def _typicality_band(distance: float) -> str:
    if distance <= _TYPICAL_MAX_DISTANCE:
        return "typical"
    if distance <= _NEAR_EDGE_MAX_DISTANCE:
        return "near_edge"
    return "low_density"


def _evidence(package: VerifiedDesignPriorPackage, *, generator_id: str, lane: str, seed: int, raw_id: str, neighbor_id: str | None = None, distance: float | None = None, band: str = "typical") -> DesignPriorSampleEvidence:
    return DesignPriorSampleEvidence(
        package_id=package.manifest.package_id,
        package_version=package.manifest.package_version,
        manifest_digest=f"sha256:{package.manifest_sha256}",
        generator_id=generator_id,
        generator_version="1.0.0",
        lane=lane,
        lane_parameter_digest=lane_parameter_digest(generator_id, lane),
        seed=seed,
        raw_sample_id=raw_id,
        neighbor_sample_id=neighbor_id,
        nearest_neighbor_distance=distance,
        typicality_band=band,
    )
