"""Typed, data-only contracts for reviewed model hypotheses.

The contract deliberately describes scientific assumptions and evidence.  It
does not contain source code, import paths, callbacks, or an executable model
graph.  Execution remains owned by an allow-listed estimator or specialized
builder elsewhere in the application.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import ContractModel


HypothesisTargetSupport = Literal[
    "continuous",
    "continuous_positive",
    "binary",
    "count",
    "ordinal",
]
HypothesisDataGrain = Literal[
    "source_row",
    "individual_observation",
    "parent_condition_mean",
    "replicate_context_mean",
    "grouped_observation_family",
]
HypothesisCapability = Literal[
    "point",
    "quantiles",
    "standard_deviation",
    "parametric_distribution",
    "support_warning",
    "grouped_validation",
    "response_curve",
]
HypothesisSplitStrategy = Literal[
    "grouped_kfold",
    "leave_one_group_out",
    "fixed_holdout",
]


class ObservationProtocol(ContractModel):
    entity_role: Annotated[str, Field(min_length=1)]
    observation_role: Annotated[str, Field(min_length=1)]
    independent_unit: Annotated[str, Field(min_length=1)]
    measurement_protocol: Annotated[str, Field(min_length=1)]
    replicate_role: str | None = None
    group_role: str | None = None
    time_role: str | None = None


class LatentProcess(ContractModel):
    latent_quantity: Annotated[str, Field(min_length=1)]
    observation_link: Annotated[str, Field(min_length=1)]
    observation_noise: Annotated[str, Field(min_length=1)]
    description: Annotated[str, Field(min_length=12)]


class SharingStructure(ContractModel):
    kind: Literal["none", "global", "partial_pooling", "shared_curve"]
    description: Annotated[str, Field(min_length=12)]
    group_role: str | None = None
    shared_parameters: tuple[str, ...] = ()
    group_specific_parameters: tuple[str, ...] = ()

    @model_validator(mode="after")
    def sharing_matches_group_declaration(self) -> "SharingStructure":
        if self.kind == "none":
            if self.group_role or self.group_specific_parameters:
                raise ValueError(
                    "sharing kind=none cannot declare group-specific structure"
                )
        elif not self.group_role:
            raise ValueError("shared structure requires group_role")
        return self


class PriorPolicy(ContractModel):
    policy_id: Literal[
        "regularization_only",
        "gaussian_smoothness",
        "bounded_gp_hyperparameters",
    ]
    description: Annotated[str, Field(min_length=12)]


class InferencePolicy(ContractModel):
    policy_id: Literal[
        "closed_form_ridge",
        "analytic_gaussian_posterior",
        "bounded_marginal_likelihood",
        "grouped_ridge",
        "reviewed_specialized_builder",
    ]
    description: Annotated[str, Field(min_length=12)]


class RequiredEvidence(ContractModel):
    kind: Literal["synthetic_recovery", "counterexample"]
    status: Literal["required"] = "required"
    purpose: Annotated[str, Field(min_length=12)]


class HypothesisValidationProtocol(ContractModel):
    split_strategy: HypothesisSplitStrategy
    comparison_cohort: Annotated[str, Field(min_length=1)]
    metrics: Annotated[tuple[str, ...], Field(min_length=1)]
    required_evidence: Annotated[
        tuple[RequiredEvidence, ...],
        Field(min_length=2),
    ]

    @model_validator(mode="after")
    def requires_recovery_and_counterexample(
        self,
    ) -> "HypothesisValidationProtocol":
        kinds = [item.kind for item in self.required_evidence]
        if sorted(kinds) != ["counterexample", "synthetic_recovery"]:
            raise ValueError(
                "validation requires exactly synthetic_recovery and counterexample evidence"
            )
        return self


class HypothesisRecipeIdentity(ContractModel):
    kind: Literal["standard_estimator", "specialized_builder"]
    recipe_id: Literal[
        "ridge.v1",
        "bayesian-additive-spline.v1",
        "exact-gp-rbf.v1",
        "stage-c-family-ridge-grouped-v1",
    ]
    version: Annotated[str, Field(min_length=1)]
    execution_status: Literal["available", "specialized_only"]

    @model_validator(mode="after")
    def recipe_matches_allow_list_role(self) -> "HypothesisRecipeIdentity":
        specialized = self.recipe_id == "stage-c-family-ridge-grouped-v1"
        if specialized != (self.kind == "specialized_builder"):
            raise ValueError("recipe kind does not match the allow-list entry")
        if specialized != (self.execution_status == "specialized_only"):
            raise ValueError(
                "recipe execution status does not match the allow-list entry"
            )
        return self


class ModelHypothesisCard(ContractModel):
    schema_version: Literal["model-hypothesis-card/v1"] = (
        "model-hypothesis-card/v1"
    )
    id: Annotated[
        str,
        Field(pattern=r"^[a-z][a-z0-9-]*$", min_length=3),
    ]
    version: Annotated[str, Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")]
    label: Annotated[str, Field(min_length=1)]
    comparison_role: Literal["baseline", "candidate"]
    data_grain: Annotated[tuple[HypothesisDataGrain, ...], Field(min_length=1)]
    target_support: Annotated[
        tuple[HypothesisTargetSupport, ...],
        Field(min_length=1),
    ]
    observation_protocol: ObservationProtocol
    latent_process: LatentProcess
    sharing_structure: SharingStructure
    constraints: Annotated[tuple[str, ...], Field(min_length=1)]
    prior_policy: PriorPolicy
    inference_policy: InferencePolicy
    identifiability_risks: Annotated[tuple[str, ...], Field(min_length=1)]
    required_diagnostics: Annotated[tuple[str, ...], Field(min_length=1)]
    validation_protocol: HypothesisValidationProtocol
    decision_outputs: Annotated[tuple[str, ...], Field(min_length=1)]
    known_failure_modes: Annotated[tuple[str, ...], Field(min_length=1)]
    required_capabilities: Annotated[
        tuple[HypothesisCapability, ...],
        Field(min_length=1),
    ]
    lifecycle_status: Literal["standard", "shared_specialized", "research"]
    recipe_identity: HypothesisRecipeIdentity | None = None

    @model_validator(mode="after")
    def separates_observation_and_latent_meaning(
        self,
    ) -> "ModelHypothesisCard":
        observed = self.observation_protocol.observation_role.strip().casefold()
        latent = self.latent_process.latent_quantity.strip().casefold()
        if observed == latent:
            raise ValueError(
                "observation role and latent quantity must be described separately"
            )
        return self


class ModelHypothesisCatalog(ContractModel):
    schema_version: Literal["model-hypothesis-catalog/v1"] = (
        "model-hypothesis-catalog/v1"
    )
    authority: Literal["bundled_allow_list"] = "bundled_allow_list"
    cards: Annotated[tuple[ModelHypothesisCard, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_card_identities(self) -> "ModelHypothesisCatalog":
        identities = [(card.id, card.version) for card in self.cards]
        if len(identities) != len(set(identities)):
            raise ValueError("model hypothesis card identities must be unique")
        if any(card.recipe_identity is None for card in self.cards):
            raise ValueError(
                "bundled allow-list cards require a reviewed recipe identity"
            )
        return self


class HypothesisComparisonAssessment(ContractModel):
    status: Literal["ready", "warning"]
    card_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    warning_codes: tuple[
        Literal["baseline_missing", "single_hypothesis_only"],
        ...,
    ] = ()
    warnings: tuple[str, ...] = ()


class ModelHypothesisContext(ContractModel):
    data_grain: HypothesisDataGrain
    target_support: HypothesisTargetSupport
    available_capabilities: tuple[HypothesisCapability, ...]


class ModelPlaygroundHandoff(ContractModel):
    current_surface: Literal["model_library"] = "model_library"
    current_action: Literal["inspect_fixed_packages"] = "inspect_fixed_packages"
    future_surface: Literal["model_playground"] = "model_playground"
    future_status: Literal["not_implemented"] = "not_implemented"
    blocked_reason: Literal[
        "model_exploration_run_contract_unavailable",
        "hypothesis_not_promoted_to_recipe",
    ]
    recipe_identity: HypothesisRecipeIdentity | None = None


class ModelHypothesisPresentation(ContractModel):
    card_id: str
    label: str
    lifecycle_status: Literal["standard", "shared_specialized", "research"]
    compatibility: Literal["compatible", "incompatible"]
    required_data: tuple[str, ...]
    missing_contracts: tuple[str, ...]
    incompatibility_reasons: tuple[str, ...]
    handoff: ModelPlaygroundHandoff
