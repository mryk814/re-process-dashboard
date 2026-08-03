from __future__ import annotations

import pytest
from pydantic import ValidationError

from decision_workbench.contracts.model_hypothesis_contracts import (
    ModelHypothesisCard,
    ModelHypothesisContext,
)
from decision_workbench.modeling.model_hypothesis_catalog import (
    assess_hypothesis_comparison,
    model_hypothesis_catalog,
    present_model_hypothesis,
    validate_model_hypothesis_card,
)
from decision_workbench.modeling.observation_model_builder import (
    TRAINING_CODE_REVISION,
)
from decision_workbench.modeling.training.recipe import estimator_recipe


def _cards() -> dict[str, ModelHypothesisCard]:
    return {card.id: card for card in model_hypothesis_catalog().cards}


def test_catalog_projects_existing_reviewed_model_paths() -> None:
    cards = _cards()

    assert set(cards) == {
        "ridge-linear-baseline",
        "bayesian-additive-spline",
        "exact-rbf-gaussian-process",
        "welding-charpy-observation-family",
    }
    assert cards["ridge-linear-baseline"].comparison_role == "baseline"
    assert cards["ridge-linear-baseline"].recipe_identity.recipe_id == "ridge.v1"
    assert (
        cards["bayesian-additive-spline"].recipe_identity.recipe_id
        == "bayesian-additive-spline.v1"
    )
    assert (
        cards["exact-rbf-gaussian-process"].recipe_identity.recipe_id
        == "exact-gp-rbf.v1"
    )
    for card_id in (
        "ridge-linear-baseline",
        "bayesian-additive-spline",
        "exact-rbf-gaussian-process",
    ):
        identity = cards[card_id].recipe_identity
        assert estimator_recipe(identity.recipe_id).estimator_id == (
            identity.recipe_id
        )
    charpy = cards["welding-charpy-observation-family"]
    assert charpy.lifecycle_status == "shared_specialized"
    assert charpy.data_grain == ("grouped_observation_family",)
    assert charpy.observation_protocol.group_role == "parent condition"
    assert charpy.recipe_identity.recipe_id == TRAINING_CODE_REVISION

    for card in cards.values():
        assert {
            evidence.kind
            for evidence in card.validation_protocol.required_evidence
        } == {"synthetic_recovery", "counterexample"}
        assert card.observation_protocol.observation_role != (
            card.latent_process.latent_quantity
        )


def test_llm_card_validation_rejects_code_and_under_specified_cards() -> None:
    valid = _cards()["ridge-linear-baseline"].model_dump(mode="json")

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        validate_model_hypothesis_card({**valid, "python_code": "print('unsafe')"})

    invented_recipe = dict(valid)
    invented_recipe["recipe_identity"] = {
        **valid["recipe_identity"],
        "recipe_id": "llm-generated-python.v1",
    }
    with pytest.raises(ValidationError, match="Input should be"):
        validate_model_hypothesis_card(invented_recipe)

    under_specified = dict(valid)
    under_specified["identifiability_risks"] = []
    with pytest.raises(ValidationError, match="at least 1 item"):
        validate_model_hypothesis_card(under_specified)

    unpromoted = dict(valid)
    unpromoted.pop("recipe_identity")
    unpromoted["id"] = "research-proposal"
    unpromoted["comparison_role"] = "candidate"
    unpromoted["lifecycle_status"] = "research"
    proposed = validate_model_hypothesis_card(unpromoted)
    assert proposed.recipe_identity is None

    unreviewed_standard = dict(unpromoted)
    unreviewed_standard["lifecycle_status"] = "standard"
    with pytest.raises(
        ValidationError,
        match="without a reviewed recipe must remain research",
    ):
        validate_model_hypothesis_card(unreviewed_standard)

    conflated = dict(valid)
    conflated["latent_process"] = {
        **valid["latent_process"],
        "latent_quantity": valid["observation_protocol"]["observation_role"],
    }
    with pytest.raises(
        ValidationError,
        match="observation role and latent quantity",
    ):
        validate_model_hypothesis_card(conflated)


def test_validation_evidence_cannot_drop_recovery_or_counterexample() -> None:
    payload = _cards()["ridge-linear-baseline"].model_dump(mode="json")
    payload["validation_protocol"]["required_evidence"] = [
        payload["validation_protocol"]["required_evidence"][0],
        payload["validation_protocol"]["required_evidence"][0],
    ]

    with pytest.raises(
        ValidationError,
        match="requires exactly synthetic_recovery and counterexample",
    ):
        validate_model_hypothesis_card(payload)


def test_comparison_warns_when_research_candidates_have_no_baseline() -> None:
    cards = _cards()
    assessment = assess_hypothesis_comparison((
        cards["bayesian-additive-spline"],
        cards["exact-rbf-gaussian-process"],
    ))

    assert assessment.status == "warning"
    assert assessment.warning_codes == ("baseline_missing",)
    assert "baselineを含まない" in assessment.warnings[0]

    ready = assess_hypothesis_comparison((
        cards["ridge-linear-baseline"],
        cards["bayesian-additive-spline"],
    ))
    assert ready.status == "ready"
    assert ready.warning_codes == ()


def test_presentation_exposes_contract_gaps_and_honest_handoff() -> None:
    card = _cards()["exact-rbf-gaussian-process"]
    presentation = present_model_hypothesis(
        card,
        ModelHypothesisContext(
            data_grain="individual_observation",
            target_support="continuous",
            available_capabilities=("point", "quantiles", "support_warning"),
        ),
    )

    assert presentation.compatibility == "incompatible"
    assert presentation.missing_contracts == (
        "standard_deviation",
        "parametric_distribution",
        "response_curve",
    )
    assert presentation.handoff.current_surface == "model_library"
    assert presentation.handoff.current_action == "inspect_fixed_packages"
    assert presentation.handoff.future_surface == "model_playground"
    assert presentation.handoff.future_status == "not_implemented"
    assert (
        presentation.handoff.blocked_reason
        == "model_exploration_run_contract_unavailable"
    )
