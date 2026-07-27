"""Contracts for bounded, evidence-grounded AI candidate reviews."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.task_contracts import ContractModel


AiReviewState = Literal["running", "completed", "partial", "invalid", "failed"]
AiReviewResourceKind = Literal[
    "project",
    "candidate_revision",
    "predictive_snapshot",
    "decision_activity_run",
    "actual_measurement",
    "objective_definition",
    "design_space",
    "task_capability",
    "model_package",
]


class AiActorIdentity(ContractModel):
    actor_id: Annotated[str, Field(min_length=1)]
    actor_kind: Literal["ai_agent"] = "ai_agent"
    agent_definition_id: Annotated[str, Field(min_length=1)]
    model_provider: Annotated[str, Field(min_length=1)]
    model_id: Annotated[str, Field(min_length=1)]
    policy_version: Annotated[str, Field(min_length=1)]
    toolset_version: Annotated[str, Field(min_length=1)]
    workspace_id: Annotated[str, Field(min_length=1)]
    capabilities: tuple[Annotated[str, Field(min_length=1)], ...]


class AiReviewEvidenceReference(ContractModel):
    resource_kind: AiReviewResourceKind
    resource_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)] | None = None
    run_id: Annotated[str, Field(min_length=1)] | None = None
    field_path: Annotated[str, Field(min_length=1)]
    observed_value_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]


class AiReviewFinding(ContractModel):
    finding_id: Annotated[str, Field(min_length=1)]
    category: Literal[
        "objective_fit",
        "support",
        "uncertainty",
        "constraint",
        "missing_evidence",
        "next_action",
    ]
    severity: Literal["info", "caution", "important"]
    claim: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_refs: Annotated[
        tuple[AiReviewEvidenceReference, ...], Field(min_length=1)
    ]
    reasoning_summary: Annotated[str, Field(min_length=1, max_length=4000)]
    confidence_kind: Literal["none", "human_readable_heuristic"] = "none"
    confidence_level: Literal["low", "medium", "high"] | None = None
    uncertainty_note: Annotated[str, Field(min_length=1, max_length=2000)]
    suggested_action: Literal[
        "inspect_evidence",
        "run_decision_activity",
        "collect_measurement",
        "review_candidate",
    ]

    @model_validator(mode="after")
    def confidence_and_language_are_safe(self) -> "AiReviewFinding":
        if self.confidence_kind == "none" and self.confidence_level is not None:
            raise ValueError("confidence level requires a human-readable heuristic")
        if (
            self.confidence_kind == "human_readable_heuristic"
            and self.confidence_level is None
        ):
            raise ValueError("human-readable heuristic requires a level")
        normalized = f"{self.claim} {self.reasoning_summary}".lower()
        support_as_probability = (
            ("support" in normalized or "支持" in normalized)
            and (
                "success probability" in normalized
                or "probability of success" in normalized
                or "成功確率" in normalized
            )
        )
        causal_overclaim = re.search(
            r"\b(causes?|caused|causal effect|proves?)\b|原因である|因果効果|証明した",
            normalized,
        )
        if support_as_probability:
            raise ValueError("support must not be described as success probability")
        if causal_overclaim:
            raise ValueError("causal overclaim is not allowed in AI review findings")
        return self


class AiReviewSuggestedAction(ContractModel):
    action: Literal[
        "inspect_evidence",
        "run_decision_activity",
        "collect_measurement",
        "review_candidate",
    ]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]


class AiReviewProviderOutput(ContractModel):
    schema_version: Literal["candidate-decision-review-output/v1"] = (
        "candidate-decision-review-output/v1"
    )
    status: Literal["complete", "partial"] = "complete"
    findings: tuple[AiReviewFinding, ...]
    summary: Annotated[str, Field(min_length=1, max_length=4000)]
    suggested_actions: tuple[AiReviewSuggestedAction, ...]
    limitations: Annotated[tuple[str, ...], Field(min_length=1)]


class AiReviewProvenance(ContractModel):
    actor: AiActorIdentity
    review_task_id: Literal["candidate-decision-review-v1"] = (
        "candidate-decision-review-v1"
    )
    prompt_template_version: Literal["candidate-decision-review-prompt/v1"] = (
        "candidate-decision-review-prompt/v1"
    )
    sampling_settings: dict[str, str | int | float | bool | None]
    input_snapshot_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    reviewed_candidate_revision: Annotated[int, Field(ge=1)]


class AiReviewRun(ContractModel):
    review_run_id: Annotated[str, Field(min_length=1)]
    schema_version: Literal["ai-review-run/v1"] = "ai-review-run/v1"
    workspace_id: Annotated[str, Field(min_length=1)]
    project_id: Annotated[str, Field(min_length=1)]
    candidate_id: Annotated[str, Field(min_length=1)]
    state: AiReviewState
    started_at: datetime
    completed_at: datetime | None = None
    reviewed_resource_refs: tuple[AiReviewEvidenceReference, ...] = ()
    provenance: AiReviewProvenance
    findings: tuple[AiReviewFinding, ...] = ()
    summary: str = ""
    suggested_actions: tuple[AiReviewSuggestedAction, ...] = ()
    limitations: tuple[str, ...] = ()
    failure_reason: str | None = None

    @model_validator(mode="after")
    def terminal_state_is_coherent(self) -> "AiReviewRun":
        if self.state == "running":
            if self.completed_at is not None or self.failure_reason is not None:
                raise ValueError("running review cannot be completed")
            return self
        if self.completed_at is None:
            raise ValueError("terminal review requires completed_at")
        if self.state == "completed":
            if self.failure_reason is not None:
                raise ValueError("completed review cannot have failure_reason")
            if not self.findings:
                raise ValueError("completed review requires at least one finding")
            if not self.limitations:
                raise ValueError("completed review requires explicit limitations")
        elif not self.failure_reason:
            raise ValueError("non-completed terminal review requires failure_reason")
        return self


class AiReviewRunRequest(ContractModel):
    expected_revision: Annotated[int, Field(ge=1)]


class AiReviewAvailability(ContractModel):
    available: bool
    reason: str | None = None
    actor: AiActorIdentity | None = None
    allowed_read_tools: tuple[str, ...] = ()
    allowed_write_tools: tuple[str, ...] = ()

    @model_validator(mode="after")
    def availability_is_coherent(self) -> "AiReviewAvailability":
        if self.available and (self.reason is not None or self.actor is None):
            raise ValueError("available review requires actor and no unavailable reason")
        if not self.available and not self.reason:
            raise ValueError("unavailable review requires a reason")
        return self


class AiReviewDispositionInput(ContractModel):
    disposition: Literal[
        "accepted",
        "partially_accepted",
        "rejected",
        "deferred",
        "superseded",
    ]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]


class AiReviewDisposition(ContractModel):
    disposition_id: Annotated[str, Field(min_length=1)]
    review_run_id: Annotated[str, Field(min_length=1)]
    project_id: Annotated[str, Field(min_length=1)]
    disposition: Literal[
        "accepted",
        "partially_accepted",
        "rejected",
        "deferred",
        "superseded",
    ]
    reason: str
    actor_id: str
    recorded_at: datetime
