"""Typed model-inference policy, selection, identity, and diagnostics contracts."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
InferenceAlgorithmId = Literal[
    "analytic-gaussian",
    "nuts",
    "finite-discrete-enumeration",
    "gibbs",
    "smc-particle",
    "laplace",
    "bounded-variational",
]
InferenceRole = Literal["exact", "sampling", "approximation", "initializer"]
LatentStructure = Literal[
    "none",
    "continuous",
    "finite_discrete",
    "mixed",
    "state_space",
]


def _digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class InferenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InferenceRequirements(InferenceContract):
    """Model structure and decision needs used to resolve an inference policy."""

    schema_version: Literal["inference-requirements/v1"] = (
        "inference-requirements/v1"
    )
    model_hypothesis_id: Annotated[str, Field(min_length=1)]
    latent_structure: LatentStructure
    posterior_geometry: Literal[
        "generic",
        "conjugate_gaussian",
        "latent_gaussian",
    ] = "generic"
    differentiability: Literal[
        "not_applicable",
        "differentiable",
        "non_differentiable",
    ]
    posterior_dimension: Annotated[int, Field(ge=0, le=1_000_000)]
    finite_state_count: Annotated[int | None, Field(ge=2, le=1_000_000)] = None
    time_steps: Annotated[int | None, Field(ge=1, le=10_000_000)] = None
    expected_multimodality: bool = False
    posterior_requirement: Literal[
        "exact",
        "sampling",
        "approximation_allowed",
    ]
    compute_budget: Literal["quick", "standard", "research"]
    requires_predictive_samples: bool = False
    requires_joint_samples: bool = False

    @model_validator(mode="after")
    def structure_specific_fields_match(self) -> InferenceRequirements:
        if self.latent_structure == "finite_discrete":
            if self.finite_state_count is None:
                raise ValueError(
                    "finite discrete inference requires finite_state_count"
                )
        elif self.finite_state_count is not None:
            raise ValueError(
                "finite_state_count is only valid for finite discrete inference"
            )
        if self.latent_structure == "state_space":
            if self.time_steps is None:
                raise ValueError("state-space inference requires time_steps")
        elif self.time_steps is not None:
            raise ValueError("time_steps is only valid for state-space inference")
        if (
            self.latent_structure in {"none", "finite_discrete"}
            and self.differentiability != "not_applicable"
        ):
            raise ValueError(
                "non-continuous latent structures use not_applicable differentiability"
            )
        return self


class InferencePolicyDefinition(InferenceContract):
    """Allow-listed reviewed algorithm policy, independent from a model recipe."""

    schema_version: Literal["inference-policy-definition/v1"] = (
        "inference-policy-definition/v1"
    )
    algorithm_id: InferenceAlgorithmId
    algorithm_version: Annotated[str, Field(min_length=1)]
    role: InferenceRole
    lifecycle_status: Literal[
        "production",
        "experimental",
        "unavailable",
        "no_adopt",
    ]
    supported_latent_structures: Annotated[
        tuple[LatentStructure, ...],
        Field(min_length=1),
    ]
    requires_differentiable: bool = False
    supports_multimodality: bool
    supports_joint_samples: bool
    max_posterior_dimension: Annotated[int, Field(ge=0, le=1_000_000)]
    max_finite_states: Annotated[int | None, Field(ge=2)] = None
    optional_dependency: str | None = None
    limitations: Annotated[tuple[str, ...], Field(min_length=1)]
    policy_digest: Digest

    @classmethod
    def create(cls, **values: Any) -> InferencePolicyDefinition:
        payload = {
            "schema_version": "inference-policy-definition/v1",
            **values,
        }
        return cls(**payload, policy_digest=_digest(payload))

    @model_validator(mode="after")
    def policy_digest_matches(self) -> InferencePolicyDefinition:
        payload = self.model_dump(mode="json", exclude={"policy_digest"})
        if self.policy_digest != _digest(payload):
            raise ValueError("inference policy digest does not match")
        if (
            self.max_finite_states is not None
            and "finite_discrete" not in self.supported_latent_structures
        ):
            raise ValueError(
                "max_finite_states requires finite_discrete support"
            )
        return self


class InferencePolicyResolution(InferenceContract):
    schema_version: Literal["inference-policy-resolution/v1"] = (
        "inference-policy-resolution/v1"
    )
    status: Literal["ready", "experimental", "unavailable"]
    requirements: InferenceRequirements
    selected_policy: InferencePolicyDefinition | None = None
    reasons: Annotated[tuple[str, ...], Field(min_length=1)]
    alternative_policy_ids: tuple[InferenceAlgorithmId, ...] = ()

    @model_validator(mode="after")
    def selection_matches_status(self) -> InferencePolicyResolution:
        if self.status == "unavailable" and self.selected_policy is not None:
            raise ValueError("unavailable inference resolution cannot select a policy")
        if self.status != "unavailable" and self.selected_policy is None:
            raise ValueError("ready inference resolution requires a selected policy")
        if (
            self.selected_policy is not None
            and self.status == "ready"
            and self.selected_policy.lifecycle_status != "production"
        ):
            raise ValueError("ready resolution requires a production policy")
        if (
            self.selected_policy is not None
            and self.status == "experimental"
            and self.selected_policy.lifecycle_status != "experimental"
        ):
            raise ValueError(
                "experimental resolution requires an experimental policy"
            )
        return self


class InferenceDiagnostics(InferenceContract):
    """Common effective diagnostics without pretending every algorithm has R-hat."""

    schema_version: Literal["inference-diagnostics/v1"] = (
        "inference-diagnostics/v1"
    )
    status: Literal["passed", "failed", "not_applicable"]
    max_r_hat: Annotated[float | None, Field(ge=1)] = None
    min_effective_sample_size: Annotated[float | None, Field(ge=0)] = None
    divergence_count: Annotated[int | None, Field(ge=0)] = None
    particle_effective_sample_size: Annotated[
        float | None,
        Field(ge=0),
    ] = None
    approximation_failure: bool | None = None
    findings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def failures_are_explicit(self) -> InferenceDiagnostics:
        if self.status == "failed" and not self.findings:
            raise ValueError("failed inference diagnostics require a finding")
        return self


class InferenceIdentity(InferenceContract):
    """Effective algorithm identity saved separately from the model recipe."""

    schema_version: Literal["inference-identity/v1"] = "inference-identity/v1"
    algorithm_id: InferenceAlgorithmId
    algorithm_version: Annotated[str, Field(min_length=1)]
    role: InferenceRole
    policy_digest: Digest
    parameterization: Annotated[str, Field(min_length=1)]
    seed: Annotated[int | None, Field(ge=0, le=2_147_483_647)] = None
    chains: Annotated[int | None, Field(ge=1, le=64)] = None
    warmup: Annotated[int | None, Field(ge=0, le=1_000_000)] = None
    draws: Annotated[int | None, Field(ge=1, le=1_000_000)] = None
    particles: Annotated[int | None, Field(ge=2, le=10_000_000)] = None
    resource_limits: dict[str, int | float | str] = Field(default_factory=dict)
    convergence_criteria: dict[str, int | float | str] = Field(
        default_factory=dict
    )
    approximation_kind: str | None = None
    approximation_limitations: tuple[str, ...] = ()
    fallback_policy: Literal["forbid_implicit_switch"] = "forbid_implicit_switch"
    diagnostics: InferenceDiagnostics
    identity_digest: Digest

    @classmethod
    def create(
        cls,
        *,
        policy: InferencePolicyDefinition,
        parameterization: str,
        diagnostics: InferenceDiagnostics,
        seed: int | None = None,
        chains: int | None = None,
        warmup: int | None = None,
        draws: int | None = None,
        particles: int | None = None,
        resource_limits: dict[str, int | float | str] | None = None,
        convergence_criteria: dict[str, int | float | str] | None = None,
        approximation_kind: str | None = None,
        approximation_limitations: tuple[str, ...] = (),
    ) -> InferenceIdentity:
        effective_approximation_kind = (
            approximation_kind or policy.algorithm_id
            if policy.role == "approximation"
            else None
        )
        effective_approximation_limitations = (
            tuple(dict.fromkeys((*policy.limitations, *approximation_limitations)))
            if policy.role == "approximation"
            else ()
        )
        payload = {
            "schema_version": "inference-identity/v1",
            "algorithm_id": policy.algorithm_id,
            "algorithm_version": policy.algorithm_version,
            "role": policy.role,
            "policy_digest": policy.policy_digest,
            "parameterization": parameterization,
            "seed": seed,
            "chains": chains,
            "warmup": warmup,
            "draws": draws,
            "particles": particles,
            "resource_limits": resource_limits or {},
            "convergence_criteria": convergence_criteria or {},
            "approximation_kind": effective_approximation_kind,
            "approximation_limitations": effective_approximation_limitations,
            "fallback_policy": "forbid_implicit_switch",
            "diagnostics": diagnostics.model_dump(mode="json"),
        }
        return cls(**payload, identity_digest=_digest(payload))

    @model_validator(mode="after")
    def effective_identity_is_consistent(self) -> InferenceIdentity:
        payload = self.model_dump(mode="json", exclude={"identity_digest"})
        if self.identity_digest != _digest(payload):
            raise ValueError("inference identity digest does not match")
        if self.role == "approximation":
            if not self.approximation_kind or not self.approximation_limitations:
                raise ValueError(
                    "approximate inference identity requires kind and limitations"
                )
        elif self.approximation_kind is not None:
            raise ValueError(
                "non-approximate inference identity cannot claim approximation kind"
            )
        if self.algorithm_id == "nuts" and None in {
            self.seed,
            self.chains,
            self.warmup,
            self.draws,
        }:
            raise ValueError("NUTS identity requires seed, chains, warmup, draws")
        if self.algorithm_id == "smc-particle" and (
            self.seed is None or self.particles is None
        ):
            raise ValueError("SMC identity requires seed and particle count")
        if self.algorithm_id in {
            "analytic-gaussian",
            "finite-discrete-enumeration",
        }:
            if any(
                value is not None
                for value in (
                    self.seed,
                    self.chains,
                    self.warmup,
                    self.draws,
                    self.particles,
                )
            ):
                raise ValueError(
                    "analytic inference identity cannot invent sampling settings"
                )
            if self.diagnostics.status != "not_applicable":
                raise ValueError(
                    "analytic inference diagnostics must be not_applicable"
                )
        if self.algorithm_id in {"nuts", "gibbs"}:
            self._validate_chain_diagnostics()
        if self.algorithm_id == "smc-particle":
            self._validate_particle_diagnostics()
        if self.role == "approximation":
            self._validate_approximation_diagnostics()
        return self

    def _criterion(self, name: str) -> float:
        value = self.convergence_criteria.get(name)
        if not isinstance(value, (int, float)):
            raise ValueError(  # noqa: TRY004 - Pydantic validation failure
                f"inference identity requires numeric {name} criterion"
            )
        return float(value)

    def _validate_chain_diagnostics(self) -> None:
        if self.diagnostics.status == "failed":
            return
        if self.diagnostics.status != "passed":
            raise ValueError("sampling inference diagnostics must be passed or failed")
        if (
            self.diagnostics.max_r_hat is None
            or self.diagnostics.min_effective_sample_size is None
        ):
            raise ValueError("chain diagnostics require R-hat and effective sample size")
        max_r_hat = self._criterion("max_r_hat")
        min_ess = self._criterion("min_ess")
        if self.diagnostics.max_r_hat > max_r_hat:
            raise ValueError("effective R-hat exceeds the saved convergence criterion")
        if self.diagnostics.min_effective_sample_size < min_ess:
            raise ValueError("effective sample size is below the saved criterion")
        if self.algorithm_id == "nuts":
            if self.diagnostics.divergence_count is None:
                raise ValueError("NUTS diagnostics require divergence count")
            max_divergences = self._criterion("max_divergences")
            if self.diagnostics.divergence_count > max_divergences:
                raise ValueError(
                    "divergence count exceeds the saved convergence criterion"
                )

    def _validate_particle_diagnostics(self) -> None:
        if self.diagnostics.status == "failed":
            return
        if (
            self.diagnostics.status != "passed"
            or self.diagnostics.particle_effective_sample_size is None
        ):
            raise ValueError("SMC diagnostics require particle effective sample size")
        min_particle_ess = self._criterion("min_particle_ess")
        if self.diagnostics.particle_effective_sample_size < min_particle_ess:
            raise ValueError("particle ESS is below the saved convergence criterion")

    def _validate_approximation_diagnostics(self) -> None:
        if self.diagnostics.approximation_failure is None:
            raise ValueError(
                "approximation diagnostics require an explicit failure flag"
            )
        expected_status = (
            "failed" if self.diagnostics.approximation_failure else "passed"
        )
        if self.diagnostics.status != expected_status:
            raise ValueError(
                "approximation diagnostics status must match its failure flag"
            )
