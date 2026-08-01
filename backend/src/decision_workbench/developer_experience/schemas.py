from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from decision_workbench.contracts.feature_recipe_contracts import (
    FeatureRecipe,
    FeatureRecipeState,
)

Severity = Literal["ok", "warning", "error"]
Decision = Literal["no", "yes", "review_required"]
Confidence = Literal["low", "medium", "high"]
CommandPlatform = Literal["cross-platform", "windows", "powershell"]


class DeveloperCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executable: str
    arguments: list[str] = []
    display_text: str
    platform: CommandPlatform = "cross-platform"


class InspectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Decision
    reason: str
    confidence: Confidence
    evidence: list[str] = []


class DataPurposeGuidance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Literal["application", "training", "candidate_input"]
    label: str
    description: str
    package_rebuild: Decision


class DeveloperCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    section: str
    title: str
    severity: Severity
    summary: str
    cause: str | None = None
    impact: str | None = None
    commands: list[DeveloperCommand] = []
    details: dict[str, Any] = {}


class ProfileCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str
    profile_path: str
    score: int
    task_ids: list[str]
    missing_sheets: list[str] = []
    extra_sheets: list[str] = []
    missing_columns: dict[str, list[str]] = {}
    extra_columns: dict[str, list[str]] = {}
    possible_unit_differences: list[str] = []
    validation_error: str | None = None


class SourceInspection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    source_sha256: str
    selected_profile: str | None = None
    ambiguous: bool = False
    candidates: list[ProfileCandidate]
    canonical_counts: dict[str, Any] = {}
    learning_counts: dict[str, int] = {}
    output_counts: dict[str, int] = {}
    structural_differences: dict[str, list[str]] = {}
    decisions: dict[str, InspectionDecision] = {}
    data_purposes: list[DataPurposeGuidance] = []
    recommendations: list[str] = []
    commands: list[DeveloperCommand] = []


class DeveloperDoctorReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["developer-doctor/v1"] = "developer-doctor/v1"
    generated_at: str
    status: Severity
    code: Literal[0, 1, 2, 3]
    checks: list[DeveloperCheck]
    task_ids: list[str]
    recommendations: list[str]
    source_inspection: SourceInspection | None = None


class ChangeGuideStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    paths: list[str]
    outcome: str


class ChangeGuideEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    risk: Literal["safe", "review", "specialist"]
    changes: list[str]
    unchanged: list[str]
    artifacts: list[str]
    steps: list[ChangeGuideStep] = []
    warnings: list[str] = []
    commands: list[DeveloperCommand]
    documents: list[str]
    human_review: str | None = None


class DeveloperOverviewItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    project_name: str
    identity_kind: Literal["single_task", "chain"] = "single_task"
    chain_revision_id: str | None = None
    dataset_view_revision_id: str | None = None
    dataset_revision_ids: list[str] = []
    source_filename: str | None = None
    source_sha256: str | None = None
    profile_id: str | None = None
    profile_digest: str | None = None
    task_id: str
    task_contract_digest: str | None = None
    package_id: str | None = None
    package_manifest_digest: str | None = None
    feature_pipeline_id: str | None = None
    feature_pipeline_version: str | None = None
    runtime_type: str | None = None
    active_package: bool = False
    archived_references: list[str] = []
    validation_status: Severity


class DeveloperOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeveloperOverviewItem]


class RuntimeDiagnosticsReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["runtime-diagnostics/v1"] = "runtime-diagnostics/v1"
    generated_at: str
    status: Severity
    checks: list[DeveloperCheck]
    project_count: int
    task_ids: list[str]


class FeatureRecipeInspectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe: FeatureRecipe
    state: FeatureRecipeState
    canonical_input: dict[str, Any]


class FeatureRecipeInspectResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipe_id: str
    recipe_digest: str
    state_digest: str
    canonical_input: dict[str, Any]
    steps: list[dict[str, Any]]
    features: dict[str, float]
