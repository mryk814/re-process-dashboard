from __future__ import annotations

import json
from pathlib import Path

from decision_workbench.research.real_task_design_prior_replay import (
    GENERATORS,
    POLICIES,
    build_report,
    load_replay_data,
    render_memo,
)


def test_fixed_public_task_split_has_training_and_historical_holdout() -> None:
    data = load_replay_data()

    assert len(data.train_numeric) > len(data.holdout_numeric) > 0
    assert data.train_group_count > data.holdout_group_count > 0
    assert len(data.numeric_paths) == 22
    assert len(data.categorical_paths) == 4


def test_replay_compares_five_generators_and_keeps_promotion_explicit() -> None:
    report = build_report(seeds=(17,), budget=32, batch_size=4)

    assert report["protocol"]["generators"] == GENERATORS
    assert report["protocol"]["selection_policies"] == POLICIES
    assert len(report["runs"]) == 10
    assert report["decisions"]["knn_local"]["status"] == "experimental"
    assert report["decisions"]["gaussian_rank_copula"]["status"] == "no_adopt"
    assert report["decisions"]["production_promotion"] is False
    assert report["protocol"]["production_registry_changed"] is False
    assert all(
        set(run) >= {
            "feasibility",
            "plausibility",
            "predictive_safety",
            "decision_value",
            "operation",
        }
        for run in report["runs"]
    )


def test_committed_real_task_replay_is_current() -> None:
    report = json.loads(
        Path("docs/research/real-task-design-prior-replay-report.json").read_text(
            encoding="utf-8"
        )
    )

    assert report["result_digest"].startswith("sha256:")
    assert report["protocol"]["source"]["digest"].startswith("sha256:")
    assert report["protocol"]["predictive_model_package"]["manifest_digest"].startswith(
        "sha256:"
    )
    assert Path("docs/research/real-task-design-prior-replay.md").read_text(
        encoding="utf-8"
    ) == render_memo(report)
