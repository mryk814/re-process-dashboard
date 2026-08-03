from __future__ import annotations

import pytest
from decision_workbench.contracts.inference_policy_contracts import (
    InferenceDiagnostics,
    InferenceIdentity,
    InferenceRequirements,
)
from decision_workbench.contracts.sampling_identity_contracts import (
    SamplingIdentity,
    SamplingRequest,
)
from decision_workbench.modeling.inference_policy import (
    inference_policy,
    inference_policy_by_identity,
    resolve_inference_policy,
)
from decision_workbench.modeling.packages.contracts import PredictorSpec
from pydantic import ValidationError


def _requirements(**overrides) -> InferenceRequirements:
    values = {
        "model_hypothesis_id": "continuous-regression/v1",
        "latent_structure": "continuous",
        "posterior_geometry": "generic",
        "differentiability": "differentiable",
        "posterior_dimension": 12,
        "posterior_requirement": "sampling",
        "compute_budget": "standard",
        "requires_predictive_samples": True,
        "requires_joint_samples": True,
    }
    values.update(overrides)
    return InferenceRequirements(**values)


def test_resolver_keeps_model_and_inference_policy_explicit() -> None:
    missing = resolve_inference_policy(_requirements())
    assert missing.status == "unavailable"
    assert missing.selected_policy is None
    assert any("numpyro" in reason for reason in missing.reasons)

    resolved = resolve_inference_policy(
        _requirements(),
        available_dependencies={"numpyro"},
    )
    assert resolved.status == "ready"
    assert resolved.selected_policy is not None
    assert resolved.selected_policy.algorithm_id == "nuts"
    assert resolved.requirements.model_hypothesis_id == "continuous-regression/v1"
    assert resolved.selected_policy.policy_digest.startswith("sha256:")


def test_resolver_covers_analytic_discrete_state_and_approximation_without_fallback() -> None:
    analytic = resolve_inference_policy(
        _requirements(
            model_hypothesis_id="bayesian-additive/v1",
            posterior_geometry="conjugate_gaussian",
            posterior_requirement="exact",
            requires_predictive_samples=False,
        )
    )
    assert analytic.status == "ready"
    assert analytic.selected_policy.algorithm_id == "analytic-gaussian"

    discrete = resolve_inference_policy(
        _requirements(
            model_hypothesis_id="finite-regime/v1",
            latent_structure="finite_discrete",
            differentiability="not_applicable",
            posterior_dimension=8,
            finite_state_count=8,
            posterior_requirement="exact",
            requires_joint_samples=False,
        )
    )
    assert discrete.status == "ready"
    assert (
        discrete.selected_policy.algorithm_id
        == "finite-discrete-enumeration"
    )
    discrete_sampling = resolve_inference_policy(
        _requirements(
            model_hypothesis_id="finite-regime-sampling/v1",
            latent_structure="finite_discrete",
            differentiability="not_applicable",
            posterior_dimension=8,
            finite_state_count=8,
            posterior_requirement="sampling",
        ),
        available_dependencies={"numpyro"},
    )
    assert discrete_sampling.status == "experimental"
    assert discrete_sampling.selected_policy.algorithm_id == "gibbs"

    generic_exact = resolve_inference_policy(
        _requirements(posterior_requirement="exact"),
        available_dependencies={"numpyro"},
    )
    assert generic_exact.status == "unavailable"
    assert any("does not satisfy exact" in item for item in generic_exact.reasons)

    state = resolve_inference_policy(
        _requirements(
            model_hypothesis_id="degradation-state/v1",
            latent_structure="state_space",
            differentiability="differentiable",
            posterior_dimension=400,
            time_steps=100,
            expected_multimodality=True,
            posterior_requirement="sampling",
        ),
        available_dependencies={"numpyro"},
    )
    assert state.status == "experimental"
    assert state.selected_policy.algorithm_id == "smc-particle"

    approximation = resolve_inference_policy(
        _requirements(
            posterior_requirement="approximation_allowed",
            compute_budget="quick",
            requires_joint_samples=False,
        )
    )
    assert approximation.status == "experimental"
    assert approximation.selected_policy.algorithm_id == "laplace"


def test_inference_identity_requires_effective_settings_and_failed_diagnostics() -> None:
    diagnostics = InferenceDiagnostics(
        status="passed",
        max_r_hat=1.005,
        min_effective_sample_size=400,
        divergence_count=0,
    )
    identity = InferenceIdentity.create(
        policy=inference_policy("nuts"),
        parameterization="non-centered/v1",
        diagnostics=diagnostics,
        seed=20260804,
        chains=4,
        warmup=500,
        draws=1000,
        resource_limits={"wall_time_seconds": 600},
        convergence_criteria={
            "max_r_hat": 1.01,
            "min_ess": 200,
            "max_divergences": 0,
        },
    )
    assert identity.identity_digest.startswith("sha256:")
    assert identity.fallback_policy == "forbid_implicit_switch"

    with pytest.raises(ValidationError, match="requires seed"):
        InferenceIdentity.create(
            policy=inference_policy("nuts"),
            parameterization="centered/v1",
            diagnostics=diagnostics,
        )
    relaxed = InferenceIdentity.create(
        policy=inference_policy("nuts"),
        parameterization="non-centered/v1",
        diagnostics=InferenceDiagnostics(
            status="passed",
            max_r_hat=1.04,
            min_effective_sample_size=100,
            divergence_count=1,
        ),
        seed=1,
        chains=2,
        warmup=100,
        draws=200,
        convergence_criteria={
            "max_r_hat": 1.05,
            "min_ess": 50,
            "max_divergences": 5,
        },
    )
    assert relaxed.diagnostics.status == "passed"
    with pytest.raises(ValidationError, match="below the saved criterion"):
        InferenceIdentity.create(
            policy=inference_policy("nuts"),
            parameterization="non-centered/v1",
            diagnostics=InferenceDiagnostics(
                status="passed",
                max_r_hat=1.005,
                min_effective_sample_size=10,
                divergence_count=0,
            ),
            seed=1,
            chains=2,
            warmup=100,
            draws=200,
            convergence_criteria={
                "max_r_hat": 1.01,
                "min_ess": 100,
                "max_divergences": 0,
            },
        )


def test_sampling_identity_links_model_inference_identity_without_breaking_legacy() -> None:
    model_identity = InferenceIdentity.create(
        policy=inference_policy("nuts"),
        parameterization="non-centered/v1",
        diagnostics=InferenceDiagnostics(
            status="passed",
            max_r_hat=1.0,
            min_effective_sample_size=100,
            divergence_count=0,
        ),
        seed=7,
        chains=2,
        warmup=100,
        draws=200,
        convergence_criteria={
            "max_r_hat": 1.01,
            "min_ess": 50,
            "max_divergences": 0,
        },
    )
    request = SamplingRequest.for_package_verification(
        seed=11,
        posterior_draw_count=12,
    )
    linked = SamplingIdentity.create(
        request=request,
        requested_sample_count=12,
        effective_sample_count=12,
        posterior_draw_count=12,
        draw_selection_policy="all_posterior_draws",
        family="normal",
        model_inference_policy_id=model_identity.algorithm_id,
        model_inference_identity_digest=model_identity.identity_digest,
    )
    assert linked.model_inference_policy_id == "nuts"
    assert (
        linked.model_inference_identity_digest
        == model_identity.identity_digest
    )

    legacy = SamplingIdentity.create(
        request=request,
        requested_sample_count=12,
        effective_sample_count=12,
        posterior_draw_count=12,
        draw_selection_policy="all_posterior_draws",
        family="normal",
    )
    assert legacy.model_inference_policy_id is None
    assert SamplingIdentity.model_validate_json(
        legacy.model_dump_json(exclude_none=True)
    ) == legacy


def test_predictor_rejects_inference_identity_outside_the_allow_list() -> None:
    identity = InferenceIdentity.create(
        policy=inference_policy("analytic-gaussian"),
        parameterization="closed-form/v1",
        diagnostics=InferenceDiagnostics(status="not_applicable"),
    )
    forged = identity.model_dump(mode="json")
    forged["policy_digest"] = f"sha256:{'0' * 64}"
    forged["identity_digest"] = identity.identity_digest
    with pytest.raises(
        ValidationError,
        match="inference identity digest does not match",
    ):
        PredictorSpec(
            id="y",
            target="y",
            unit="1",
            target_kind="continuous",
            runtime_type="builtin.linear.v1",
            architecture_id=None,
            artifact="model-artifacts/y.npz",
            predictive_family="normal",
            feature_names=("x",),
            inference_identity=forged,
        )

    with pytest.raises(
        ValidationError,
        match="runtime and architecture do not support",
    ):
        PredictorSpec(
            id="y",
            target="y",
            unit="1",
            target_kind="continuous",
            runtime_type="numpyro.dense_posterior.v1",
            architecture_id="dense_mlp_v1",
            artifact="model-artifacts/y.npz",
            predictive_family="normal",
            feature_names=("x",),
            inference_identity=identity,
        )


def test_policy_identity_lookup_is_historical_and_approximation_keeps_limitations() -> None:
    current = inference_policy("laplace")
    assert (
        inference_policy_by_identity(
            current.algorithm_id,
            current.algorithm_version,
            current.policy_digest,
        )
        == current
    )
    with pytest.raises(KeyError, match="historical"):
        inference_policy_by_identity(
            current.algorithm_id,
            current.algorithm_version,
            f"sha256:{'0' * 64}",
        )

    approximation = InferenceIdentity.create(
        policy=current,
        parameterization="mode-hessian/v1",
        diagnostics=InferenceDiagnostics(
            status="passed",
            approximation_failure=False,
        ),
    )
    assert approximation.approximation_kind == "laplace"
    assert set(current.limitations).issubset(
        approximation.approximation_limitations
    )

    for status, failed in (
        ("not_applicable", False),
        ("failed", False),
        ("passed", True),
    ):
        diagnostics = InferenceDiagnostics(
            status=status,
            approximation_failure=failed,
            findings=("approximation failed",) if status == "failed" else (),
        )
        with pytest.raises(
            ValidationError,
            match="status must match its failure flag",
        ):
            InferenceIdentity.create(
                policy=current,
                parameterization="mode-hessian/v1",
                diagnostics=diagnostics,
            )
