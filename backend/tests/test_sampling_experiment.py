from dataclasses import asdict

import pytest

from decision_workbench.research.sampling_experiment import (
    FIXTURES,
    _distance_matrix,
    _objective,
    append_revision,
    create_initial_revision,
    evaluate_sampling,
    run_comparison,
    sample_unit,
)


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda item: item.fixture_id)
def test_lhs_and_sobol_are_reproducible_and_report_budget_metrics(fixture) -> None:
    for method in ("latin_hypercube", "sobol"):
        first, first_records = evaluate_sampling(
            method,
            fixture,
            budget=64,
            seed=546,
        )
        second, second_records = evaluate_sampling(
            method,
            fixture,
            budget=64,
            seed=546,
        )

        assert first_records == second_records
        assert first.generated == 64
        assert first.feasible_unique == first.model_calls
        assert 0 <= first.rejection_rate <= 1
        assert 0 <= first.marginal_bin_coverage <= 1
        assert first.runtime_ms >= 0
        assert first.best_objective is not None or first.feasible_unique == 0
        assert first.support_rate is not None or first.feasible_unique == 0


def test_grid_helper_is_research_only_and_limited_to_two_dimensions() -> None:
    first = sample_unit(
        "grid_2d_helper",
        count=20,
        dimensions=2,
        seed=546,
        family_budget=20,
    )
    second = sample_unit(
        "grid_2d_helper",
        count=20,
        dimensions=2,
        seed=546,
        family_budget=20,
    )

    assert first.tolist() == second.tolist()
    with pytest.raises(ValueError, match="limited"):
        sample_unit(
            "grid_2d_helper",
            count=20,
            dimensions=3,
            seed=546,
        )


def test_mixed_fixture_treats_categories_as_nominal_not_ordinal() -> None:
    fixture = next(item for item in FIXTURES if item.fixture_id == "mixed-6d")
    baseline = sample_unit(
        "sobol",
        count=1,
        dimensions=fixture.dimensions,
        seed=546,
    )
    first = baseline.copy()
    second = baseline.copy()
    third = baseline.copy()
    category_index = fixture.numeric_variables
    first[:, category_index] = 0
    second[:, category_index] = 0.5
    third[:, category_index] = 1

    assert _distance_matrix(first, second, fixture)[0, 0] == pytest.approx(
        _distance_matrix(first, third, fixture)[0, 0]
    )
    assert _objective(first, fixture)[0] == pytest.approx(
        _objective(third, fixture)[0]
    )


@pytest.mark.parametrize("method", ["latin_hypercube", "sobol", "grid_2d_helper"])
def test_sequential_revision_preserves_identity_and_immutable_boundary(method) -> None:
    fixture = next(item for item in FIXTURES if item.fixture_id == "numeric-2d")
    initial = create_initial_revision(
        method,
        fixture,
        budget=32,
        seed=546,
        saved_proposal_snapshot_id="proposal-snapshot-1",
    )
    before = asdict(initial)

    appended = append_revision(initial, fixture, additional_budget=32)

    assert asdict(initial) == before
    assert appended.family_id == initial.family_id
    assert appended.revision_id != initial.revision_id
    assert appended.parent_revision_id == initial.revision_id
    assert appended.samples[: len(initial.samples)] == initial.samples
    assert appended.new_sample_ids
    assert not set(appended.new_sample_ids) & {
        sample.sample_id for sample in initial.samples
    }
    assert len({sample.point for sample in appended.samples}) == len(appended.samples)
    assert appended.saved_proposal_snapshot_id == "proposal-snapshot-1"


def test_comparison_covers_dimensions_rejection_and_sequential_evidence() -> None:
    report = run_comparison(budget=32, seeds=(546,))
    fixtures = {
        row["fixture"]["fixture_id"]
        for row in report["results"]
    }

    assert fixtures == {
        "numeric-1d",
        "numeric-2d",
        "numeric-6d",
        "mixed-6d",
        "numeric-10d",
        "high-rejection-6d",
    }
    assert all(
        row["fixture"]["dimensions"] <= 2
        for row in report["results"]
        if row["method"] == "grid_2d_helper"
    )
    assert all(
        row["same_family"]
        and row["parent_revision_preserved"]
        and row["prior_sample_ids_preserved"]
        and row["duplicate_points"] == 0
        and row["saved_proposal_snapshot_preserved"]
        for row in report["sequential_comparison"]
    )
    assert {
        row["fixture_id"] for row in report["sequential_comparison"]
    } == fixtures
    assert all(
        {
            "marginal_bin_coverage",
            "mean_nearest_distance",
            "best_objective",
            "runtime_ms",
            "model_calls",
            "support_rate",
            "selected_diversity",
        }
        <= row["sequential_metrics"].keys()
        for row in report["sequential_comparison"]
    )
    assert report["production_ui_changed"] is False
    assert report["decision"]["sequential_sampling"] == (
        "adopt_sobol_prefix_as_candidate"
    )
    assert report["decision"]["grid_2d_helper"] == "do_not_adopt"
