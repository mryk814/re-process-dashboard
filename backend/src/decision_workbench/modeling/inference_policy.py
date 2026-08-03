"""Allow-listed model-inference policies and explicit resolver.

The resolver selects an algorithm identity; it never executes an algorithm and
never silently changes to another policy after a failure.
"""

from __future__ import annotations

from collections.abc import Iterable

from decision_workbench.contracts.inference_policy_contracts import (
    InferenceAlgorithmId,
    InferencePolicyDefinition,
    InferencePolicyResolution,
    InferenceRequirements,
)

INFERENCE_POLICY_CATALOG = (
    InferencePolicyDefinition.create(
        algorithm_id="analytic-gaussian",
        algorithm_version="1.0.0",
        role="exact",
        lifecycle_status="production",
        supported_latent_structures=("none", "continuous"),
        requires_differentiable=False,
        supports_multimodality=False,
        supports_joint_samples=True,
        max_posterior_dimension=100_000,
        max_finite_states=None,
        optional_dependency=None,
        limitations=(
            "requires an explicitly conjugate Gaussian posterior",
            "conditions on fixed model structure and hyperparameters",
        ),
    ),
    InferencePolicyDefinition.create(
        algorithm_id="nuts",
        algorithm_version="1.0.0",
        role="sampling",
        lifecycle_status="production",
        supported_latent_structures=("continuous",),
        requires_differentiable=True,
        supports_multimodality=False,
        supports_joint_samples=True,
        max_posterior_dimension=20_000,
        max_finite_states=None,
        optional_dependency="numpyro",
        limitations=(
            "requires a differentiable continuous posterior",
            "does not guarantee exploration of separated modes",
        ),
    ),
    InferencePolicyDefinition.create(
        algorithm_id="finite-discrete-enumeration",
        algorithm_version="1.0.0",
        role="exact",
        lifecycle_status="production",
        supported_latent_structures=("finite_discrete",),
        requires_differentiable=False,
        supports_multimodality=True,
        supports_joint_samples=False,
        max_posterior_dimension=64,
        max_finite_states=64,
        optional_dependency=None,
        limitations=(
            "only finite allow-listed state spaces are enumerated",
        ),
    ),
    InferencePolicyDefinition.create(
        algorithm_id="gibbs",
        algorithm_version="1.0.0",
        role="sampling",
        lifecycle_status="experimental",
        supported_latent_structures=("finite_discrete", "mixed"),
        requires_differentiable=False,
        supports_multimodality=False,
        supports_joint_samples=True,
        max_posterior_dimension=50_000,
        max_finite_states=100_000,
        optional_dependency="numpyro",
        limitations=(
            "mixing must be demonstrated for each conditional structure",
        ),
    ),
    InferencePolicyDefinition.create(
        algorithm_id="smc-particle",
        algorithm_version="1.0.0",
        role="sampling",
        lifecycle_status="experimental",
        supported_latent_structures=("state_space",),
        requires_differentiable=False,
        supports_multimodality=True,
        supports_joint_samples=True,
        max_posterior_dimension=1_000_000,
        max_finite_states=None,
        optional_dependency="numpyro",
        limitations=(
            "particle degeneracy and path ancestry require explicit diagnostics",
        ),
    ),
    InferencePolicyDefinition.create(
        algorithm_id="laplace",
        algorithm_version="1.0.0",
        role="approximation",
        lifecycle_status="experimental",
        supported_latent_structures=("continuous",),
        requires_differentiable=True,
        supports_multimodality=False,
        supports_joint_samples=False,
        max_posterior_dimension=100_000,
        max_finite_states=None,
        optional_dependency=None,
        limitations=(
            "local Gaussian approximation can miss skew and separated modes",
        ),
    ),
    InferencePolicyDefinition.create(
        algorithm_id="bounded-variational",
        algorithm_version="1.0.0",
        role="approximation",
        lifecycle_status="experimental",
        supported_latent_structures=("continuous", "mixed"),
        requires_differentiable=True,
        supports_multimodality=False,
        supports_joint_samples=True,
        max_posterior_dimension=1_000_000,
        max_finite_states=None,
        optional_dependency="numpyro",
        limitations=(
            "variational family and optimization budget must be versioned",
            "posterior variance can be underestimated",
        ),
    ),
)

_POLICY_BY_IDENTITY = {
    (
        policy.algorithm_id,
        policy.algorithm_version,
        policy.policy_digest,
    ): policy
    for policy in INFERENCE_POLICY_CATALOG
}
_CURRENT_POLICY_BY_ID = {
    policy.algorithm_id: policy for policy in INFERENCE_POLICY_CATALOG
}


def inference_policy(
    algorithm_id: InferenceAlgorithmId,
) -> InferencePolicyDefinition:
    return _CURRENT_POLICY_BY_ID[algorithm_id]


def inference_policy_by_identity(
    algorithm_id: InferenceAlgorithmId,
    algorithm_version: str,
    policy_digest: str,
) -> InferencePolicyDefinition:
    """Resolve an immutable historical policy identity, not merely the latest ID."""

    try:
        return _POLICY_BY_IDENTITY[
            (algorithm_id, algorithm_version, policy_digest)
        ]
    except KeyError as exc:
        raise KeyError(
            "historical inference policy identity is not present in the reviewed catalog"
        ) from exc


def _ordered_candidate_ids(
    requirements: InferenceRequirements,
) -> tuple[InferenceAlgorithmId, ...]:
    if requirements.posterior_geometry == "conjugate_gaussian":
        return ("analytic-gaussian",)
    if requirements.latent_structure == "none":
        return ("analytic-gaussian",)
    if requirements.latent_structure == "finite_discrete":
        if requirements.posterior_requirement == "sampling":
            return ("gibbs",)
        if requirements.posterior_requirement == "exact":
            return ("finite-discrete-enumeration",)
        return ("finite-discrete-enumeration", "gibbs")
    if requirements.latent_structure == "state_space":
        return ("smc-particle",)
    if requirements.latent_structure == "mixed":
        if requirements.posterior_requirement == "approximation_allowed":
            return ("gibbs", "bounded-variational")
        return ("gibbs",)
    if (
        requirements.posterior_requirement == "approximation_allowed"
        and requirements.compute_budget == "quick"
    ):
        return ("laplace", "bounded-variational", "nuts")
    if requirements.posterior_requirement == "approximation_allowed":
        return ("nuts", "laplace", "bounded-variational")
    return ("nuts",)


def _incompatibility_reasons(
    policy: InferencePolicyDefinition,
    requirements: InferenceRequirements,
    dependencies: frozenset[str],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if (
        requirements.posterior_requirement == "exact"
        and policy.role != "exact"
    ):
        reasons.append(
            f"{policy.algorithm_id} role {policy.role} does not satisfy exact inference"
        )
    if (
        requirements.posterior_requirement == "sampling"
        and policy.role != "sampling"
    ):
        reasons.append(
            f"{policy.algorithm_id} role {policy.role} does not satisfy sampling inference"
        )
    if requirements.latent_structure not in policy.supported_latent_structures:
        reasons.append(
            f"{policy.algorithm_id} does not support "
            f"{requirements.latent_structure} latent structure"
        )
    if (
        policy.requires_differentiable
        and requirements.differentiability != "differentiable"
    ):
        reasons.append(f"{policy.algorithm_id} requires differentiability")
    if requirements.posterior_dimension > policy.max_posterior_dimension:
        reasons.append(
            f"posterior dimension {requirements.posterior_dimension} exceeds "
            f"{policy.algorithm_id} policy limit {policy.max_posterior_dimension}"
        )
    if (
        requirements.finite_state_count is not None
        and policy.max_finite_states is not None
        and requirements.finite_state_count > policy.max_finite_states
    ):
        reasons.append(
            f"finite state count {requirements.finite_state_count} exceeds "
            f"{policy.algorithm_id} limit {policy.max_finite_states}"
        )
    if requirements.expected_multimodality and not policy.supports_multimodality:
        reasons.append(f"{policy.algorithm_id} does not cover expected multimodality")
    if requirements.requires_joint_samples and not policy.supports_joint_samples:
        reasons.append(f"{policy.algorithm_id} does not provide joint samples")
    if (
        requirements.posterior_requirement != "approximation_allowed"
        and policy.role == "approximation"
    ):
        reasons.append("approximation is not permitted by the model requirements")
    if (
        policy.optional_dependency is not None
        and policy.optional_dependency not in dependencies
    ):
        reasons.append(
            f"optional dependency {policy.optional_dependency} is unavailable"
        )
    return tuple(reasons)


def resolve_inference_policy(
    requirements: InferenceRequirements,
    *,
    available_dependencies: Iterable[str] = (),
    allow_experimental: bool = True,
) -> InferencePolicyResolution:
    """Resolve one reviewed policy or return an explicit unavailable result."""

    dependencies = frozenset(available_dependencies)
    candidate_ids = _ordered_candidate_ids(requirements)
    rejected: list[str] = []
    compatible_not_selected: list[InferenceAlgorithmId] = []
    for algorithm_id in candidate_ids:
        policy = inference_policy(algorithm_id)
        reasons = _incompatibility_reasons(policy, requirements, dependencies)
        if reasons:
            rejected.extend(reasons)
            continue
        if policy.lifecycle_status == "experimental" and not allow_experimental:
            rejected.append(f"{algorithm_id} is experimental and not enabled")
            compatible_not_selected.append(algorithm_id)
            continue
        status = (
            "ready"
            if policy.lifecycle_status == "production"
            else "experimental"
        )
        alternatives = tuple(
            item
            for item in candidate_ids
            if item != algorithm_id
            and not _incompatibility_reasons(
                inference_policy(item),
                requirements,
                dependencies,
            )
        )
        return InferencePolicyResolution(
            status=status,
            requirements=requirements,
            selected_policy=policy,
            reasons=(
                f"selected {algorithm_id} from the reviewed allow-list",
            ),
            alternative_policy_ids=alternatives,
        )
    return InferencePolicyResolution(
        status="unavailable",
        requirements=requirements,
        selected_policy=None,
        reasons=tuple(dict.fromkeys(rejected))
        or ("no reviewed inference policy matches the requirements",),
        alternative_policy_ids=tuple(dict.fromkeys(compatible_not_selected)),
    )
