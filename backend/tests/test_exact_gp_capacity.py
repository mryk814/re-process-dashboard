from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from decision_workbench.modeling.training.capacity import (
    CAPACITY_POLICY_VERSION,
    capacity_context_from_training_set,
    resolve_exact_gp_capacity,
)
from decision_workbench.modeling.training.feature_dataset import _evaluation_identity
from decision_workbench.modeling.training.readiness import (
    EstimatorReadinessContext,
    resolve_estimator_readiness,
)


def _context(
    *,
    raw: int = 8,
    effective: int = 5,
    features: int = 8,
    folds: int = 3,
    restarts: int = 1,
    groups: int = 5,
    max_rows: int = 500,
) -> SimpleNamespace:
    repeat_counts = (raw - effective + 1,) + (1,) * (effective - 1)
    return SimpleNamespace(
        repeat_counts=repeat_counts,
        y=np.zeros(effective),
        x=np.zeros((effective, features)),
        validation_groups=tuple(f"group-{i}" for i in range(effective)),
        folds=folds,
        validation_plan=SimpleNamespace(strategy="grouped_kfold"),
        cohort_digest="sha256:cohort",
        fold_digest="sha256:fold",
        validation_plan_digest="sha256:plan",
        target="target",
    ), SimpleNamespace(
        restarts=restarts,
        max_rows=max_rows,
        seed=11,
    )


def test_capacity_context_preserves_raw_and_effective_counts() -> None:
    data, recipe = _context(raw=8, effective=5)

    context = capacity_context_from_training_set(data, recipe)

    assert context.raw_observation_count == 8
    assert context.effective_replicate_context_count == 5
    assert context.effective_training_rows == 5
    assert context.total_fit_count == 4


def test_capacity_resolution_is_explicit_at_the_effective_row_boundary() -> None:
    data, recipe = _context(raw=600, effective=501, folds=5, restarts=3)
    resolution = resolve_exact_gp_capacity(
        capacity_context_from_training_set(data, recipe)
    )

    assert resolution.policy_version == CAPACITY_POLICY_VERSION
    assert resolution.decision == "approximate_required"
    assert resolution.recommended_path == "alternative_estimator"
    assert resolution.automatic_switch is False
    assert resolution.row_reduction == "forbidden"
    assert resolution.fold_reduction == "forbidden"
    assert any(
        item.path_id == "fixed-random-feature-gp-spike.v1"
        and item.availability == "experimental_no_adopt"
        for item in resolution.paths
    )
    assert any(
        item.path_id == "ridge.v1" and item.recommended
        for item in resolution.paths
    )
    assert any(
        "effective replicate contexts=501" in reason
        for reason in resolution.reasons
    )


def test_capacity_context_is_exposed_by_readiness_resolver() -> None:
    data, recipe = _context(raw=600, effective=501, folds=5, restarts=3)
    capacity = capacity_context_from_training_set(data, recipe)
    resolution = resolve_estimator_readiness(
        EstimatorReadinessContext(
            estimator_id="exact-gp-rbf.v1",
            target_kind="continuous",
            row_count=capacity.effective_training_rows,
            independent_group_count=capacity.independent_validation_group_count,
            feature_count=capacity.feature_count,
            target_contract="ready",
            validation_plan="ready",
            validation_strategy="grouped_kfold",
            feature_recipe="ready",
            capacity=capacity,
        )
    )

    assert resolution.status == "capacity_exceeded"
    assert resolution.capacity is not None
    assert resolution.capacity.recommended_path == "alternative_estimator"


def test_default_training_path_never_reduces_requested_folds() -> None:
    with pytest.raises(ValueError, match="fold count is never reduced implicitly"):
        _evaluation_identity(
            target="target",
            replicate_contexts=("r1", "r2", "r3"),
            validation_groups=("g1", "g2", "g3"),
            observation_ids=(("o1",), ("o2",), ("o3",)),
            requested_folds=5,
            seed=11,
        )
