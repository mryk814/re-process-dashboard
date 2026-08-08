"""Immutable contracts for historical Decision Cases and retrospective replay."""
from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.prediction_catalog_contracts import (
    ActualMeasurement,
    Prediction,
)
from decision_workbench.contracts.objective_contracts import ObjectiveDefinition
from decision_workbench.contracts.task_contracts import ContractModel


class DecisionCandidateReference(ContractModel):
    candidate_id: Annotated[str, Field(min_length=1)]
    candidate_revision: Annotated[int, Field(ge=1)]


class DecisionSelection(ContractModel):
    status: Literal["selected", "no_decision"]
    candidate: DecisionCandidateReference | None = None

    @model_validator(mode="after")
    def selected_candidate_matches_status(self) -> "DecisionSelection":
        if self.status == "selected" and self.candidate is None:
            raise ValueError("selected decision requires a candidate revision")
        if self.status == "no_decision" and self.candidate is not None:
            raise ValueError("no-decision case cannot name a selected candidate")
        return self


class DecisionRationaleInput(ContractModel):
    disposition: Literal["selected", "deferred", "rejected_all", "no_decision"]
    rationale: Annotated[str, Field(min_length=1, max_length=4000)]


class DecisionRationale(DecisionRationaleInput):
    actor_id: Annotated[str, Field(min_length=1, max_length=128)]


class DecisionOutcomePolicy(ContractModel):
    schema_version: Literal["decision-outcome-policy/v1"] = "decision-outcome-policy/v1"
    target_keys: Annotated[tuple[str, ...], Field(min_length=1)]
    missing_actual_policy: Literal["retain_partial"] = "retain_partial"

    @model_validator(mode="after")
    def target_keys_are_unique(self) -> "DecisionOutcomePolicy":
        if len(self.target_keys) != len(set(self.target_keys)):
            raise ValueError("outcome target keys must be unique")
        return self


class DecisionCaseCreateRequest(ContractModel):
    schema_version: Literal["decision-case-create/v1"] = "decision-case-create/v1"
    decision_timestamp: datetime
    candidates: Annotated[tuple[DecisionCandidateReference, ...], Field(min_length=1)]
    snapshot_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    selection: DecisionSelection
    rationale: DecisionRationaleInput | None = None
    actual_measurement_ids: tuple[str, ...] = ()
    outcome_policy: DecisionOutcomePolicy

    @model_validator(mode="after")
    def references_are_unique_and_selected_is_in_set(self) -> "DecisionCaseCreateRequest":
        candidate_keys = [
            (item.candidate_id, item.candidate_revision) for item in self.candidates
        ]
        if len(candidate_keys) != len(set(candidate_keys)):
            raise ValueError("candidate revisions must be unique")
        if len(self.snapshot_ids) != len(set(self.snapshot_ids)):
            raise ValueError("snapshot identities must be unique")
        if len(self.actual_measurement_ids) != len(set(self.actual_measurement_ids)):
            raise ValueError("actual measurement identities must be unique")
        if self.selection.candidate is not None and (
            self.selection.candidate.candidate_id,
            self.selection.candidate.candidate_revision,
        ) not in set(candidate_keys):
            raise ValueError("selected candidate revision must belong to the fixed candidate set")
        return self


class DecisionCaseDraftSnapshot(ContractModel):
    snapshot_id: str
    candidate: DecisionCandidateReference
    candidate_name: str
    created_at: datetime


class DecisionCaseDraftContext(ContractModel):
    schema_version: Literal["decision-case-draft-context/v1"] = (
        "decision-case-draft-context/v1"
    )
    snapshots: tuple[DecisionCaseDraftSnapshot, ...]
    actuals: tuple[ActualMeasurement, ...]
    target_keys: tuple[str, ...]
    current_selection: DecisionSelection


class HistoricalCandidateEvidence(ContractModel):
    candidate: DecisionCandidateReference
    candidate_name: str
    candidate_updated_at: datetime
    snapshot_id: str
    snapshot_created_at: datetime
    predictions: dict[str, Prediction]
    model_package_manifest_digest: str
    warnings: tuple[str, ...] = ()


class RetrospectiveActualEvidence(ContractModel):
    actual: ActualMeasurement
    candidate: DecisionCandidateReference
    prediction_snapshot_created_at: datetime


class DecisionCase(ContractModel):
    schema_version: Literal["decision-case/v1"] = "decision-case/v1"
    id: str
    semantic_identity: str
    project_id: str
    task_id: str
    task_contract_digest: str
    objective_definition: ObjectiveDefinition | None = None
    objective_definition_digest: str | None = None
    decision_timestamp: datetime
    candidates: tuple[DecisionCandidateReference, ...]
    historical_evidence: tuple[HistoricalCandidateEvidence, ...]
    selection: DecisionSelection
    rationale: DecisionRationale | None = None
    retrospective_actuals: tuple[RetrospectiveActualEvidence, ...]
    outcome_policy: DecisionOutcomePolicy
    created_at: datetime


class DecisionReplayRequest(ContractModel):
    schema_version: Literal["decision-replay-request/v1"] = "decision-replay-request/v1"
    alternative_policy: Literal["primary-objective-point-estimate/v1"] = (
        "primary-objective-point-estimate/v1"
    )


class HistoricalCandidateEvaluation(ContractModel):
    candidate: DecisionCandidateReference
    candidate_name: str
    predictions: dict[str, Prediction]
    originally_selected: bool


class RealizedOutcome(ContractModel):
    candidate_id: str
    target: str
    actual_id: str
    observed_value: float
    observed_label: str | None = None
    predicted_value: float
    absolute_error: Annotated[float, Field(ge=0)]
    measured_at: date | None = None


class CurrentPackageReevaluation(ContractModel):
    candidate: DecisionCandidateReference
    model_package_manifest_digest: str
    predictions: dict[str, Prediction]
    evidence_layer: Literal["hindsight"] = "hindsight"


class SimilarDecisionCase(ContractModel):
    case_id: str
    project_id: str
    decision_timestamp: datetime
    selection_status: Literal["selected", "no_decision"]
    snapshot_ids: tuple[str, ...]
    actual_references: tuple[RetrospectiveActualEvidence, ...]
    compatibility: Literal["same_task_objective_targets"] = (
        "same_task_objective_targets"
    )


class DecisionReplayResult(ContractModel):
    schema_version: Literal["decision-replay-result/v1"] = "decision-replay-result/v1"
    historical: tuple[HistoricalCandidateEvaluation, ...]
    realized_outcomes: tuple[RealizedOutcome, ...]
    unobserved_targets: tuple[str, ...]
    alternative_policy: Literal["primary-objective-point-estimate/v1"]
    alternative_selection: DecisionCandidateReference | None = None
    alternative_selection_reason: str
    current_package_reevaluation: tuple[CurrentPackageReevaluation, ...]
    similar_cases: tuple[SimilarDecisionCase, ...]
    warnings: tuple[str, ...] = ()


class DecisionReplayRun(ContractModel):
    schema_version: Literal["decision-replay-run/v1"] = "decision-replay-run/v1"
    id: str
    semantic_identity: str
    project_id: str
    case_id: str
    created_at: datetime
    request: DecisionReplayRequest
    result: DecisionReplayResult
