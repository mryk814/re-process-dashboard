"""Deterministic landmark MDS with an explicit out-of-sample transform.

The two-dimensional coordinates are only a reading aid. Support and novelty
remain distances in the Task-declared metric space and are never inferred from
the drawing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np

EMBEDDING_METHOD = "landmark-classical-mds-oos"
EMBEDDING_VERSION = "1.0.0"


DistanceFunction = Callable[[np.ndarray, np.ndarray], np.ndarray]


@dataclass(frozen=True)
class LandmarkMdsTransform:
    landmark_indexes: tuple[int, ...]
    landmark_vectors: np.ndarray
    landmark_coordinates: np.ndarray
    eigenvectors: np.ndarray
    eigenvalues: np.ndarray
    column_mean_squared_distance: np.ndarray
    grand_mean_squared_distance: float
    captured_positive_eigenvalue_ratio: float
    seed: int

    def transform(
        self,
        vectors: np.ndarray,
        distance: DistanceFunction,
    ) -> np.ndarray:
        result = []
        scale = np.sqrt(self.eigenvalues)
        for vector in np.asarray(vectors, dtype=float):
            squared = distance(self.landmark_vectors, vector) ** 2
            centered = -0.5 * (
                squared
                - squared.mean()
                - self.column_mean_squared_distance
                + self.grand_mean_squared_distance
            )
            result.append((centered @ self.eigenvectors) / scale)
        return np.vstack(result)


def _stable_eigenvectors(vectors: np.ndarray) -> np.ndarray:
    stable = vectors.copy()
    for column in range(stable.shape[1]):
        pivot = int(np.argmax(np.abs(stable[:, column])))
        if stable[pivot, column] < 0:
            stable[:, column] *= -1
    return stable


def _farthest_landmarks(
    vectors: np.ndarray,
    distance: DistanceFunction,
    *,
    count: int,
    seed: int,
) -> tuple[int, ...]:
    if len(vectors) == 0:
        raise ValueError("埋め込み対象の学習条件がありません")
    count = min(max(count, 2), len(vectors))
    first = int(np.random.default_rng(seed).integers(0, len(vectors)))
    selected = [first]
    nearest = distance(vectors, vectors[first])
    nearest[first] = -np.inf
    while len(selected) < count:
        next_index = int(np.argmax(nearest))
        selected.append(next_index)
        nearest = np.minimum(nearest, distance(vectors, vectors[next_index]))
        nearest[selected] = -np.inf
    return tuple(selected)


def fit_landmark_mds(
    vectors: np.ndarray,
    distance: DistanceFunction,
    *,
    landmark_limit: int,
    seed: int,
) -> LandmarkMdsTransform:
    vectors = np.asarray(vectors, dtype=float)
    indexes = _farthest_landmarks(
        vectors,
        distance,
        count=landmark_limit,
        seed=seed,
    )
    landmarks = vectors[list(indexes)]
    distances = np.vstack([distance(landmarks, row) for row in landmarks])
    squared = distances**2
    centered = -0.5 * (
        squared
        - squared.mean(axis=0)[None, :]
        - squared.mean(axis=1)[:, None]
        + squared.mean()
    )
    values, vectors_eigen = np.linalg.eigh(centered)
    positive = np.flatnonzero(values > max(float(values.max()), 1.0) * 1e-12)
    if len(positive) < 2:
        raise ValueError("学習条件のTask距離を2次元へ配置できません")
    selected_eigen = positive[np.argsort(values[positive])[-2:][::-1]]
    eigenvalues = values[selected_eigen]
    eigenvectors = _stable_eigenvectors(vectors_eigen[:, selected_eigen])
    coordinates = eigenvectors * np.sqrt(eigenvalues)
    return LandmarkMdsTransform(
        landmark_indexes=indexes,
        landmark_vectors=landmarks,
        landmark_coordinates=coordinates,
        eigenvectors=eigenvectors,
        eigenvalues=eigenvalues,
        column_mean_squared_distance=squared.mean(axis=0),
        grand_mean_squared_distance=float(squared.mean()),
        captured_positive_eigenvalue_ratio=float(
            eigenvalues.sum() / values[positive].sum()
        ),
        seed=seed,
    )


def deterministic_display_indexes(
    vectors: np.ndarray,
    *,
    limit: int,
    seed: int,
    required: tuple[int, ...] = (),
) -> tuple[int, ...]:
    """Keep every landmark and fill the display cohort with a seeded sample.

    The landmarks already cover the metric-space extremes. Sampling the
    remaining rows is O(N), avoiding a second farthest-point O(N * limit)
    search for large reference cohorts.
    """

    if len(vectors) <= limit:
        return tuple(range(len(vectors)))
    selected = list(dict.fromkeys(required))
    required_set = set(selected)
    for index in np.random.default_rng(seed).permutation(len(vectors)):
        candidate_index = int(index)
        if candidate_index in required_set:
            continue
        selected.append(candidate_index)
        if len(selected) == limit:
            break
    return tuple(selected)
