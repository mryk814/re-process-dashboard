from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from decision_workbench.research.generative_design_lab import (
    COPULA_CORRELATION_SHRINKAGE,
    COPULA_QUANTILE_METHOD,
    DIRECT_DIVERSITY_WEIGHT,
    GENERATOR_IDS,
    KNN_ALPHA_RANGE,
    NEAR_DUPLICATE_DISTANCE_THRESHOLD,
    NEAR_DUPLICATE_POLICY_VERSION,
    SELECTION_POLICIES,
    SUPPORT_POLICY,
    VAE_BETA,
    VAE_EPOCHS,
    VAE_HIDDEN_DIMENSIONS,
    VAE_LATENT_DIMENSIONS,
    VAE_LEARNING_RATE,
    _digest,
    _hard_feasible,
    _mixed_distance,
    _predictive_value,
    _select_batch,
    build_report,
    evaluate_run,
    fixtures,
    generate_pool,
    generator_parameter_payload,
    render_adoption_memo,
)


def _summary(report, fixture_id: str, generator_id: str, policy: str):
    return next(
        item
        for item in report["generator_summaries"]
        if item["fixture_id"] == fixture_id
        and item["generator_id"] == generator_id
        and item["selection_policy"] == policy
    )


def test_fixed_protocol_is_reproducible_and_pins_all_comparison_identity() -> None:
    first = build_report(seeds=(17, 41), budget=64, batch_size=6)
    second = build_report(seeds=(17, 41), budget=64, batch_size=6)

    assert first == second
    assert first["result_digest"].startswith("sha256:")
    assert first["protocol"]["production_registry_changed"] is False
    assert first["protocol"]["generator_ids"] == (
        *GENERATOR_IDS,
        "tiny_vae_research",
    )
    assert first["protocol"]["selection_policies"] == SELECTION_POLICIES
    assert first["protocol"]["support_policy"] == SUPPORT_POLICY
    assert first["protocol"]["pool_size"] == 64
    assert first["protocol"]["rejection_budget"] == 64
    assert first["protocol"]["repair_budget"] == 0
    assert len(first["fixtures"]) == 4
    assert len(first["runs"]) == 84
    required_identity = set(first["protocol"]["fixed_identity_fields"])
    assert all(set(run["identity"]) == required_identity for run in first["runs"])
    assert all(run["budget"] == 64 for run in first["runs"])
    assert all(run["batch_size"] == 6 for run in first["runs"])
    assert {
        "training_snapshot_digest",
        "support_policy_digest",
        "pool_budget_digest",
        "predictive_capability_digest",
    } <= required_identity
    fixture_by_id = {
        fixture["fixture_id"]: fixture for fixture in first["fixtures"]
    }
    for run in first["runs"]:
        assert run["identity"]["generator_parameter_digest"] == _digest(
            generator_parameter_payload(
                run["generator_id"],
                seed=run["seed"],
                budget=run["budget"],
            )
        )
        assert (
            run["identity"]["objective_digest"]
            == _digest(
                {
                    "target": "predicted_fixture_score",
                    "direction": "maximize",
                    "source": fixture_by_id[run["fixture_id"]][
                        "predictive_model_id"
                    ],
                }
            )
        )
        assert (
            run["identity"]["objective_digest"]
            != run["identity"]["hidden_oracle_digest"]
        )


def test_knn_and_copula_preserve_modes_but_expose_different_tradeoffs() -> None:
    report = build_report(seeds=(17, 41), budget=96, batch_size=8)
    fixture_id = "mixed-categorical-modes"
    lhs = _summary(report, fixture_id, "latin_hypercube", "direct_objective")
    knn = _summary(report, fixture_id, "knn_local", "direct_objective")
    copula = _summary(
        report,
        fixture_id,
        "gaussian_rank_copula",
        "direct_objective",
    )

    assert lhs["mean_hard_violation_rate"] > 0.5
    assert knn["mean_hard_violation_rate"] == 0
    assert copula["mean_hard_violation_rate"] == 0
    assert knn["mean_nearest_distance"] < copula["mean_nearest_distance"]

    empirical_runs = [
        run
        for run in report["runs"]
        if run["fixture_id"] == fixture_id
        and run["generator_id"] == "empirical_rows"
        and run["selection_policy"] == "direct_objective"
    ]
    copula_runs = [
        run
        for run in report["runs"]
        if run["fixture_id"] == fixture_id
        and run["generator_id"] == "gaussian_rank_copula"
        and run["selection_policy"] == "direct_objective"
    ]
    assert all(run["novelty"]["observed_duplicate_rate"] == 1 for run in empirical_runs)
    assert all(run["novelty"]["pool_duplicate_rate"] > 0.4 for run in empirical_runs)
    assert all(run["novelty"]["observed_duplicate_rate"] == 0 for run in copula_runs)
    assert all(run["novelty"]["pool_duplicate_rate"] == 0 for run in copula_runs)
    assert all(run["novelty"]["unseen_mode_rate"] == 0 for run in copula_runs)

    composition_copula = _summary(
        report,
        "constrained-composition",
        "gaussian_rank_copula",
        "direct_objective",
    )
    composition_knn = _summary(
        report,
        "constrained-composition",
        "knn_local",
        "direct_objective",
    )
    assert composition_copula["mean_hard_violation_rate"] > 0
    assert composition_knn["mean_hard_violation_rate"] == 0


def test_batch_diversity_counts_exact_duplicates_and_records_threshold() -> None:
    fixture = next(
        item for item in fixtures() if item.fixture_id == "mixed-categorical-modes"
    )
    pool = generate_pool(fixture, "empirical_rows", budget=96, seed=17)
    selected, _ = _select_batch(
        fixture,
        pool,
        policy="direct_objective",
        batch_size=8,
    )
    distances = _mixed_distance(
        pool.points[selected],
        pool.categories[selected],
        pool.points[selected],
        pool.categories[selected],
    )
    pairwise = distances[np.triu_indices(len(selected), k=1)]
    assert np.any(pairwise == 0)

    run = evaluate_run(
        fixture,
        "empirical_rows",
        "direct_objective",
        budget=96,
        batch_size=8,
        seed=17,
    )
    assert run["batch_quality"]["mean_pairwise_diversity"] == round(
        float(pairwise.mean()),
        8,
    )
    assert run["batch_quality"]["near_duplicate_rate"] == round(
        float(np.mean(pairwise <= NEAR_DUPLICATE_DISTANCE_THRESHOLD)),
        8,
    )
    assert run["batch_quality"]["near_duplicate_policy"] == {
        "version": NEAR_DUPLICATE_POLICY_VERSION,
        "distance_threshold": NEAR_DUPLICATE_DISTANCE_THRESHOLD,
    }


def test_conservative_selection_reduces_ood_optimizer_exploitation() -> None:
    report = build_report(seeds=(17, 41, 83), budget=128, batch_size=8)
    for generator_id in ("latin_hypercube", "sobol"):
        direct = _summary(
            report,
            "offline-optimization-trap",
            generator_id,
            "direct_objective",
        )
        conservative = _summary(
            report,
            "offline-optimization-trap",
            generator_id,
            "conservative_diverse",
        )
        assert direct["mean_optimizer_exploitation_rate"] > 0.3
        assert (
            conservative["mean_optimizer_exploitation_rate"]
            < direct["mean_optimizer_exploitation_rate"] / 3
        )
        assert conservative["mean_batch_diversity"] > 0


def test_direct_objective_selection_has_no_hidden_diversity_term() -> None:
    fixture = next(
        item for item in fixtures() if item.fixture_id == "correlated-continuous"
    )
    pool = generate_pool(fixture, "latin_hypercube", budget=128, seed=17)
    selected, evidence = _select_batch(
        fixture,
        pool,
        policy="direct_objective",
        batch_size=8,
    )
    feasible_indexes = _hard_feasible(
        fixture,
        pool.points,
        pool.categories,
    ).nonzero()[0]
    predicted = _predictive_value(
        fixture,
        pool.points[feasible_indexes],
        pool.categories[feasible_indexes],
    )
    expected = feasible_indexes[predicted.argsort(kind="stable")[::-1][:8]]

    assert DIRECT_DIVERSITY_WEIGHT == 0
    assert evidence["diversity_weight"] == 0
    assert selected.tolist() == expected.tolist()


def test_generator_parameter_contract_covers_implemented_semantics() -> None:
    knn = generator_parameter_payload("knn_local", seed=17, budget=128)
    copula = generator_parameter_payload(
        "gaussian_rank_copula",
        seed=17,
        budget=128,
    )
    vae = generator_parameter_payload("tiny_vae_research", seed=17, budget=128)

    assert knn["parameters"]["alpha_range"] == KNN_ALPHA_RANGE
    assert (
        copula["parameters"]["correlation_shrinkage"]
        == COPULA_CORRELATION_SHRINKAGE
    )
    assert copula["parameters"]["quantile_method"] == COPULA_QUANTILE_METHOD
    assert vae["parameters"]["architecture"] == {
        "hidden_dimensions": VAE_HIDDEN_DIMENSIONS,
        "latent_dimensions": VAE_LATENT_DIMENSIONS,
        "activation": "tanh",
        "output": "sigmoid",
    }
    assert vae["parameters"]["epochs"] == VAE_EPOCHS
    assert vae["parameters"]["beta"] == VAE_BETA
    assert vae["parameters"]["learning_rate"] == VAE_LEARNING_RATE


def test_deep_candidate_is_exercised_but_cannot_promote_itself() -> None:
    report = build_report(seeds=(17, 41), budget=64, batch_size=6)
    vae_runs = [
        run for run in report["runs"] if run["generator_id"] == "tiny_vae_research"
    ]
    memo = next(
        item
        for item in report["adoption_memos"]
        if item.get("generator_id") == "tiny_vae_research"
    )

    assert len(vae_runs) == 4
    assert {run["fixture_id"] for run in vae_runs} == {
        "mixed-categorical-modes"
    }
    assert all(run["operation"]["training_required"] is True for run in vae_runs)
    assert all(run["operation"]["epochs"] == 240 for run in vae_runs)
    assert all(run["operation"]["artifact_bytes"] > 0 for run in vae_runs)
    assert all(run["operation"]["safe_production_adapter"] is False for run in vae_runs)
    assert memo["status"] == "no_adopt"
    assert memo["registry_changed"] is False
    assert "likelihood" not in {
        key
        for run in vae_runs
        for section in ("feasibility", "predictive_safety")
        for key in run[section]
    }


def test_committed_report_and_adoption_memo_are_current() -> None:
    report = build_report()
    report_path = Path("docs/research/generative-design-lab-report.json")
    memo_path = Path("docs/research/generative-design-lab-adoption-memo.md")

    assert json.loads(report_path.read_text(encoding="utf-8")) == json.loads(
        json.dumps(report, ensure_ascii=False)
    )
    assert memo_path.read_text(encoding="utf-8") == render_adoption_memo(report)
