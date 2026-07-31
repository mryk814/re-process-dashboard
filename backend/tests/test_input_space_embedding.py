from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from decision_workbench.modeling.training_distance import _vector_space_digest
from decision_workbench.modeling.input_space_embedding import fit_landmark_mds


def _euclidean(reference: np.ndarray, query: np.ndarray) -> np.ndarray:
    return np.sqrt(((reference - query) ** 2).sum(axis=1))


def test_landmark_mds_is_reproducible_and_places_new_points_without_refit() -> None:
    training = np.asarray(
        [
            [0.0, 0.0],
            [1.0, 0.0],
            [0.0, 1.0],
            [1.0, 1.0],
            [3.0, 2.0],
        ]
    )
    query = np.asarray([[0.1, 0.1], [2.9, 2.1]])

    first = fit_landmark_mds(
        training,
        _euclidean,
        landmark_limit=len(training),
        seed=508,
    )
    second = fit_landmark_mds(
        training,
        _euclidean,
        landmark_limit=len(training),
        seed=508,
    )
    first_training = first.transform(training, _euclidean)
    first_query = first.transform(query, _euclidean)

    assert first.landmark_indexes == second.landmark_indexes
    assert np.allclose(
        first_training,
        second.transform(training, _euclidean),
    )
    assert np.allclose(first_query, second.transform(query, _euclidean))
    assert np.argmin(_euclidean(first_training, first_query[0])) == 0
    assert np.argmin(_euclidean(first_training, first_query[1])) == 4
    assert first.captured_positive_eigenvalue_ratio == pytest.approx(1.0)


def test_vector_space_identity_covers_pipeline_package_features_groups_and_vectors() -> None:
    def runtime(manifest_sha256: str = "sha256:package-a") -> SimpleNamespace:
        pipeline = SimpleNamespace(id="pipeline", version="1", output_features=("a", "b"))
        manifest = SimpleNamespace(
            package_id="package",
            package_version="1",
            feature_pipeline=pipeline,
        )
        package = SimpleNamespace(
            manifest=manifest,
            manifest_sha256=manifest_sha256,
        )
        return SimpleNamespace(model_package=package, task_id="task")

    kwargs = {
        "runtime": runtime(),
        "context_ids": ("row-1", "row-2"),
        "vectors": np.asarray([[0.0, 1.0], [1.0, 0.0]]),
        "groups": {"first": (0,), "second": (1,)},
        "feature_order": ("a", "b"),
    }
    digest = _vector_space_digest(**kwargs)
    assert digest.startswith("sha256:")
    assert _vector_space_digest(
        **{**kwargs, "feature_order": ("b", "a")}
    ) != digest
    assert _vector_space_digest(
        **{**kwargs, "vectors": np.asarray([[0.0, 1.0], [1.1, 0.0]])}
    ) != digest
    assert _vector_space_digest(
        **{**kwargs, "groups": {"all": (0, 1)}}
    ) != digest
    assert _vector_space_digest(
        **{**kwargs, "runtime": runtime("sha256:package-b")}
    ) != digest
