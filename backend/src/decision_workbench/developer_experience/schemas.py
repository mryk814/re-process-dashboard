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
    identity_kind: Literal["single_task", "chain", "prediction_graph"] = "single_task"
    chain_revision_id: str | None = None
    graph_revision_id: str | None = None
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


class DeveloperCapabilityAtlasTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    runtime_available: bool
    available_package_count: int
    graph_compatible: bool
    missingness_status: Literal[
        "runtime_contract_not_exposed",
        "reject_only",
        "declared_imputation",
        "evaluated_imputation",
    ]
    missingness_policy_digest: str


class DeveloperStochasticReproducibility(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["effective_sampling_identity_recorded"]
    identity_schema_version: Literal["sampling-identity/v1"]
    runtime_types: list[str]
    limitations: list[
        Literal[
            "response_curve_sampling_identity_unavailable",
            "legacy_evidence_sampling_conditions_unavailable",
        ]
    ]


class DeveloperModelHypothesisCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    label: str
    comparison_role: Literal["baseline", "candidate"]
    lifecycle_status: Literal["standard", "shared_specialized", "research"]
    data_grain: list[
        Literal[
            "source_row",
            "individual_observation",
            "parent_condition_mean",
            "replicate_context_mean",
            "grouped_observation_family",
        ]
    ]
    target_support: list[
        Literal[
            "continuous",
            "continuous_positive",
            "binary",
            "count",
            "ordinal",
        ]
    ]
    required_capabilities: list[
        Literal[
            "point",
            "quantiles",
            "standard_deviation",
            "parametric_distribution",
            "support_warning",
            "grouped_validation",
            "response_curve",
        ]
    ]
    recipe_id: Literal[
        "ridge.v1",
        "bayesian-additive-spline.v1",
        "exact-gp-rbf.v1",
        "stage-c-family-ridge-grouped-v1",
    ]
    execution_status: Literal["available", "specialized_only"]


class DeveloperModelHypothesisCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["model-hypothesis-catalog/v1"]
    authority: Literal["bundled_allow_list"]
    card_count: int
    lifecycle_counts: dict[
        Literal["standard", "shared_specialized", "research"],
        int,
    ]
    playground_handoff_status: Literal["not_implemented"]
    cards: list[DeveloperModelHypothesisCard]


class DeveloperCapabilityAtlas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["capability-atlas/v1"]
    authority: Literal["bundled"]
    stochastic_reproducibility: DeveloperStochasticReproducibility
    project_modes: list[
        Literal["single_task", "chain", "prediction_graph"]
    ]
    task_count: int
    available_package_count: int
    graph_count: int
    model_hypothesis_catalog: DeveloperModelHypothesisCatalog
    tasks: list[DeveloperCapabilityAtlasTask]


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
