from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SamplingRequest(BaseModel):
    """Versioned operation policy requested from a sample-based runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["sampling-request/v1"] = "sampling-request/v1"
    operation: Literal[
        "preview",
        "detailed_prediction",
        "response_surface",
        "prediction_graph_stage",
        "decision_activity",
        "screening_proposal",
        "missing_completion",
        "candidate_export",
        "package_verification",
    ]
    policy_id: Annotated[str, Field(min_length=1)]
    method_id: Literal["numpyro-posterior-predictive"] = (
        "numpyro-posterior-predictive"
    )
    method_version: Literal["1.0.0"] = "1.0.0"
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)] = 0
    requested_sample_count: Annotated[int | None, Field(ge=2, le=4096)] = None
    policy_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @classmethod
    def for_operation(
        cls,
        operation: Literal[
            "preview",
            "detailed_prediction",
            "response_surface",
            "prediction_graph_stage",
            "decision_activity",
            "screening_proposal",
            "missing_completion",
            "candidate_export",
        ],
        *,
        seed: int | None = None,
    ) -> "SamplingRequest":
        policies = {
            "preview": ("prediction-preview/v1", 128, 20260803),
            "detailed_prediction": ("detailed-prediction/v1", 512, 20260803),
            "response_surface": ("response-surface/v1", 256, 20260803),
            "prediction_graph_stage": ("prediction-graph-stage/v1", 512, 20260803),
            "decision_activity": ("decision-activity/v1", 256, 20260803),
            "screening_proposal": ("screening-proposal/v1", 256, 20260803),
            "missing_completion": ("missing-completion-prediction/v1", 128, 20260803),
            "candidate_export": ("candidate-export/v1", 256, 20260803),
        }
        policy_id, count, default_seed = policies[operation]
        return cls.create(
            operation=operation,
            policy_id=policy_id,
            seed=default_seed if seed is None else seed,
            requested_sample_count=count,
        )

    @classmethod
    def for_package_verification(
        cls, *, seed: int, posterior_draw_count: int
    ) -> "SamplingRequest":
        return cls.create(
            operation="package_verification",
            policy_id="package-verification-all-posterior-draws/v1",
            seed=seed,
            requested_sample_count=posterior_draw_count,
        )

    @classmethod
    def create(
        cls,
        *,
        operation: str,
        policy_id: str,
        seed: int,
        requested_sample_count: int,
    ) -> "SamplingRequest":
        policy = {
            "schema_version": "sampling-request/v1",
            "operation": operation,
            "policy_id": policy_id,
            "method_id": "numpyro-posterior-predictive",
            "method_version": "1.0.0",
            "seed": seed,
            "requested_sample_count": requested_sample_count,
        }
        digest = hashlib.sha256(
            json.dumps(
                policy,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return cls(**policy, policy_digest=f"sha256:{digest}")

    @model_validator(mode="after")
    def policy_digest_matches(self) -> "SamplingRequest":
        policy = self.model_dump(mode="json", exclude={"policy_digest"})
        expected = "sha256:" + hashlib.sha256(
            json.dumps(
                policy,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if self.policy_digest != expected:
            raise ValueError("sampling request policy digest does not match")
        if self.requested_sample_count is None:
            raise ValueError("operation sampling request requires an explicit sample count")
        return self


class SamplingIdentity(BaseModel):
    """Effective sampling conditions returned by the runtime that used them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["sampling-identity/v1"] = "sampling-identity/v1"
    runtime_type: Literal["numpyro.dense_posterior.v1"]
    method_id: Literal["numpyro-posterior-predictive"]
    method_version: Literal["1.0.0"]
    operation: Annotated[str, Field(min_length=1)]
    request_policy_id: Annotated[str, Field(min_length=1)]
    request_policy_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)]
    requested_sample_count: Annotated[int, Field(ge=2, le=4096)]
    effective_sample_count: Annotated[int, Field(ge=2, le=4096)]
    posterior_draw_count: Annotated[int, Field(ge=2, le=4096)]
    draw_selection_policy: Literal[
        "all_posterior_draws",
        "seeded_without_replacement",
        "seeded_with_replacement",
    ]
    predictive_resampling_policy: Literal[
        "numpy-default-rng-likelihood/v1",
        "none-posterior-probability-summary/v1",
    ]
    aggregation_policy: Literal[
        "central-90-linear-quantiles/v1",
        "central-90-inverted-cdf-quantiles/v1",
    ]
    approximation: str | None = None
    fallback: str | None = None
    parameter_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

    @classmethod
    def create(
        cls,
        *,
        request: SamplingRequest,
        requested_sample_count: int,
        effective_sample_count: int,
        posterior_draw_count: int,
        draw_selection_policy: Literal[
            "all_posterior_draws",
            "seeded_without_replacement",
            "seeded_with_replacement",
        ],
        family: str,
    ) -> "SamplingIdentity":
        if requested_sample_count != request.requested_sample_count:
            raise ValueError(
                "sampling identity requested count must match the Sampling Request"
            )
        parameters = {
            "schema_version": "sampling-identity/v1",
            "runtime_type": "numpyro.dense_posterior.v1",
            "method_id": "numpyro-posterior-predictive",
            "method_version": "1.0.0",
            "operation": request.operation,
            "request_policy_id": request.policy_id,
            "request_policy_digest": request.policy_digest,
            "seed": request.seed,
            "requested_sample_count": requested_sample_count,
            "effective_sample_count": effective_sample_count,
            "posterior_draw_count": posterior_draw_count,
            "draw_selection_policy": draw_selection_policy,
            "predictive_resampling_policy": (
                "none-posterior-probability-summary/v1"
                if family == "bernoulli_logit"
                else "numpy-default-rng-likelihood/v1"
            ),
            "aggregation_policy": (
                "central-90-inverted-cdf-quantiles/v1"
                if family
                in {
                    "poisson_log",
                    "negative_binomial_log",
                    "zero_inflated_poisson_log",
                    "ordinal_logit",
                }
                else "central-90-linear-quantiles/v1"
            ),
            "approximation": None,
            "fallback": None,
        }
        digest = hashlib.sha256(
            json.dumps(
                parameters,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return cls(**parameters, parameter_digest=f"sha256:{digest}")

    @model_validator(mode="after")
    def effective_count_matches_selection(self) -> "SamplingIdentity":
        if self.effective_sample_count != self.requested_sample_count:
            raise ValueError("effective sample count must match the fulfilled request")
        if (
            self.draw_selection_policy == "all_posterior_draws"
            and self.effective_sample_count != self.posterior_draw_count
        ):
            raise ValueError("all-posterior-draw selection requires matching counts")
        parameters = self.model_dump(mode="json", exclude={"parameter_digest"})
        expected = "sha256:" + hashlib.sha256(
            json.dumps(
                parameters,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if self.parameter_digest != expected:
            raise ValueError("sampling identity parameter digest does not match")
        return self


class LegacySamplingIdentityUnavailable(BaseModel):
    """Read projection for immutable sample-based evidence written before v1."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["sampling-identity/unavailable-legacy"] = (
        "sampling-identity/unavailable-legacy"
    )
    reason: Literal["not_recorded"] = "not_recorded"


SamplingEvidence = SamplingIdentity | LegacySamplingIdentityUnavailable
