from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


MissingKind = Literal[
    "structural_not_applicable",
    "not_measured",
    "unknown_category",
    "redacted",
]
MissingnessSupport = Literal["supported", "sparse", "unseen", "incompatible"]
InputCompleteness = Literal["complete", "imputed", "native_missing", "blocked"]
PredictionStatus = Literal["final", "provisional", "blocked"]
MissingnessOperation = Literal[
    "preview",
    "detailed_prediction",
    "snapshot",
    "proposal",
    "export",
    "completion_lab",
]


class MissingFieldEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: Annotated[str, Field(min_length=1)]
    kind: MissingKind
    applied_policy: Annotated[str, Field(min_length=1)]
    imputed_value: float | str | None = None
    sampling_method: str | None = None
    policy_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    training_missing_rate: Annotated[float, Field(ge=0, le=1)] | None = None
    evaluation_count: Annotated[int, Field(ge=0)] | None = None


class InputMissingnessEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["input-missingness-evidence/v1"] = (
        "input-missingness-evidence/v1"
    )
    input_completeness: InputCompleteness
    prediction_status: PredictionStatus
    operation: MissingnessOperation
    missingness_support: MissingnessSupport
    pattern_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    support_policy_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    fields: tuple[MissingFieldEvidence, ...] = ()
    uncertainty_propagated: bool = False
    uncertainty_method: str | None = None
    pattern_training_count: Annotated[int, Field(ge=0)] | None = None
    pattern_evaluation_count: Annotated[int, Field(ge=0)] | None = None
    pattern_metrics: dict[str, dict[str, float]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def status_and_uncertainty_are_consistent(self) -> "InputMissingnessEvidence":
        if self.input_completeness == "complete":
            if self.prediction_status != "final" or self.fields:
                raise ValueError(
                    "complete input requires final status and no missing fields"
                )
        elif self.input_completeness == "blocked":
            if self.prediction_status != "blocked" or not self.fields:
                raise ValueError(
                    "blocked input requires blocked status and missing fields"
                )
        elif self.prediction_status != "provisional" or not self.fields:
            raise ValueError(
                "imputed or native-missing input requires provisional status "
                "and missing fields"
            )
        if self.uncertainty_propagated != bool(self.uncertainty_method):
            raise ValueError(
                "uncertainty propagation requires an explicit method, and "
                "a method cannot be recorded without propagation"
            )
        return self


class MissingnessOperationCapability(BaseModel):
    """Package-owned permission envelope for incomplete candidate operations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    preview: Literal["allow", "warn", "block"]
    comparison: Literal["allow", "warn", "block"]
    snapshot: Literal["allow", "explicit_override", "block"]
    proposal: Literal["allow_with_quota", "block"]
    export: Literal["require_complete", "allow_provisional"]
    completion_uncertainty: Literal["available", "unavailable"]


class CompletionUncertainty(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: Annotated[float, Field(ge=0)]
    input_missingness: Annotated[float, Field(ge=0)]
    combined: Annotated[float, Field(ge=0)]


class MissingCompletionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: Annotated[str, Field(min_length=1)]
    mean: float
    uncertainty: CompletionUncertainty


class MissingCompletionLabReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["missing-completion-lab/v1"] = "missing-completion-lab/v1"
    candidate_id: Annotated[str, Field(min_length=1)]
    generator_id: Literal["empirical_rows", "knn_local"]
    sample_count: Annotated[int, Field(ge=2, le=256)]
    seed: Annotated[int, Field(ge=0)]
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    canonical_input_schema_version: Annotated[str, Field(min_length=1)]
    predictive_package_id: Annotated[str, Field(min_length=1)]
    predictive_manifest_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    design_prior_package_id: Annotated[str, Field(min_length=1)]
    design_prior_manifest_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    candidate_revision: Annotated[int, Field(ge=1)]
    candidate_input_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ]
    missing_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    summaries: tuple[MissingCompletionSummary, ...]
    completion_evidence: tuple[dict[str, object], ...]
