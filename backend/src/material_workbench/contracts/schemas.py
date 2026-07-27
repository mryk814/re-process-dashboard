from __future__ import annotations

import math
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from material_workbench.contracts.subsystem_availability import (
    SubsystemAvailability,
)

from material_workbench.contracts.blend_contracts import (
    BlendEditorState,
    BlendValidationState,
    SparseBlend,
)
from material_workbench.contracts.batch_proposal_contracts import (
    BatchProposalDefinition,
    BatchProposalRun,
)
from material_workbench.contracts.chain_contracts import (
    ChainProjectIdentity,
    ProjectScientificIdentity,
)
from material_workbench.contracts.chain_execution_contracts import (
    ActualConditionedVariant,
    ChainSnapshot,
)
from material_workbench.contracts.chain_uncertainty_contracts import (
    ChainDistributionRun,
)
from material_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from material_workbench.contracts.objective_contracts import ObjectiveDefinition
from material_workbench.contracts.proposal_contracts import (
    ProposalCandidateEvaluation,
    ProposalIncumbentResolution,
    ProposalObjectiveExecution,
    ProposalRejectedCandidate,
    ProposalStrategyRequest,
)
from material_workbench.contracts.task_contracts import CandidateProvenance, DirectSourceRef, ResolvedTaskDefinition


COMPOSITION_ELEMENTS = {
    "C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N"
}


class DataAssetCreateInput(BaseModel):
    original_filename: Annotated[str, Field(min_length=1, max_length=255)]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    media_type: Annotated[str, Field(min_length=1, max_length=120)]
    locator_kind: Literal["managed", "bundled"]
    locator: Annotated[str, Field(min_length=1)]


class DataAssetUpdateInput(BaseModel):
    archived: bool


class DataAsset(DataAssetCreateInput):
    id: str
    created_at: datetime
    archived_at: datetime | None = None


class ProfileRevisionCreateInput(BaseModel):
    profile_id: Annotated[str, Field(min_length=1, max_length=120)]
    revision: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    profile_digest: Annotated[str, Field(min_length=1)]
    canonical_contract_digest: Annotated[str, Field(min_length=1)]
    effective_profile_json: dict[str, Any]


class ProfileRevisionUpdateInput(BaseModel):
    archived: bool


class ProfileRevision(ProfileRevisionCreateInput):
    id: str
    created_at: datetime
    archived_at: datetime | None = None


class DatasetRevisionCreateInput(BaseModel):
    data_asset_id: Annotated[str, Field(min_length=1)]
    profile_revision_id: Annotated[str, Field(min_length=1)]
    canonicalization_contract_digest: Annotated[str, Field(min_length=1)]


class DatasetRevisionUpdateInput(BaseModel):
    archived: bool


class DatasetRevision(DatasetRevisionCreateInput):
    id: str
    dataset_digest: Annotated[str, Field(min_length=1)]
    created_at: datetime
    archived_at: datetime | None = None


class DatasetViewMemberInput(BaseModel):
    dataset_revision_id: Annotated[str, Field(min_length=1)]
    ordinal: Annotated[int, Field(ge=0)]
    cohort_key: Annotated[str, Field(max_length=120)] = ""
    cohort_label: Annotated[str, Field(max_length=160)] = ""
    provenance_json: dict[str, Any] = Field(default_factory=dict)


class DatasetViewMember(DatasetViewMemberInput):
    dataset_view_revision_id: str


class DatasetViewRevisionCreateInput(BaseModel):
    view_id: Annotated[str, Field(min_length=1, max_length=120)]
    revision: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    kind: Literal["single", "cohort_comparison"]
    members: Annotated[list[DatasetViewMemberInput], Field(min_length=1)]

    @model_validator(mode="after")
    def members_match_view_kind(self) -> "DatasetViewRevisionCreateInput":
        if self.kind == "single" and len(self.members) != 1:
            raise ValueError("single Dataset ViewにはDataset Revisionを1件だけ指定してください")
        if self.kind == "cohort_comparison" and len(self.members) < 2:
            raise ValueError("cohort comparisonにはDataset Revisionを2件以上指定してください")
        revision_ids = [member.dataset_revision_id for member in self.members]
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("Dataset View内で同じDataset Revisionを重複指定できません")
        ordinals = [member.ordinal for member in self.members]
        if len(ordinals) != len(set(ordinals)):
            raise ValueError("Dataset View内のordinalは重複できません")
        cohort_keys = [member.cohort_key for member in self.members]
        if len(cohort_keys) != len(set(cohort_keys)):
            raise ValueError("Dataset View内のcohort_keyは重複できません")
        return self


class DatasetViewRevisionUpdateInput(BaseModel):
    archived: bool


class DatasetViewRevision(BaseModel):
    id: str
    view_id: str
    revision: Annotated[int, Field(ge=1)]
    name: str
    kind: Literal["single", "cohort_comparison"]
    view_digest: Annotated[str, Field(min_length=1)]
    members: list[DatasetViewMember]
    created_at: datetime
    archived_at: datetime | None = None


class ModelPackageRefCreateInput(BaseModel):
    package_id: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Annotated[str, Field(min_length=1)]
    manifest_digest: Annotated[str, Field(min_length=1)]
    locator: Annotated[str, Field(min_length=1)]
    manifest_json: dict[str, Any]


class ModelPackageRefUpdateInput(BaseModel):
    archived: bool


class ModelPackageRef(ModelPackageRefCreateInput):
    id: str
    created_at: datetime
    archived_at: datetime | None = None


class ProjectSeriesCreateInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    description: str = ""


class ProjectSeriesUpdateInput(ProjectSeriesCreateInput):
    archived: bool = False


class ProjectSeries(ProjectSeriesCreateInput):
    id: str
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DataLibraryDataset(BaseModel):
    dataset_revision: DatasetRevision
    data_asset: DataAsset
    profile_revision: ProfileRevision
    supported_task_ids: list[str]
    dataset_views: list[DatasetViewRevision] = Field(default_factory=list)


class ProfileWorkbenchProfileOption(BaseModel):
    profile_id: str
    source_name: str
    profile_digest: str
    task_ids: list[str]


class ProfileWorkbenchSheetInventory(BaseModel):
    name: str
    headers: list[str]
    rows: Annotated[int, Field(ge=0)]


class ProfileWorkbenchValidation(BaseModel):
    registration_ready: bool
    profile_id: str
    profile_digest: str
    task_ids: list[str]
    entities: Annotated[int, Field(ge=0)]
    relations: Annotated[int, Field(ge=0)]
    observations: Annotated[int, Field(ge=0)]
    observations_by_task: dict[str, int]
    heat_series_parents: Annotated[int, Field(ge=0)]
    unresolved_heat_series_by_task: dict[str, Annotated[int, Field(ge=0)]] = Field(default_factory=dict)
    rejected_by_policy: dict[str, int]
    entity_preview: list[dict[str, Any]]


class ProfileWorkbenchInspection(BaseModel):
    source_filename: str
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    sheets: list[ProfileWorkbenchSheetInventory]
    selected_profile_digest: str | None = None
    auto_detected: bool = False
    profile_error: str | None = None
    validation: ProfileWorkbenchValidation | None = None


class ProfileWorkbenchRegistration(BaseModel):
    reused_existing: bool
    data_asset_id: str
    profile_revision_id: str
    dataset_revision_id: str
    dataset_view_revision_id: str
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    profile_id: str
    task_ids: list[str]


class ProjectCreationOptions(BaseModel):
    datasets: list[DataLibraryDataset]
    dataset_views: list[DatasetViewRevision]
    model_packages: list[ModelPackageRef]
    project_series: list[ProjectSeries]
    task_contract_digests: dict[str, str]


class HeatPoint(BaseModel):
    time_s: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    temperature_c: Annotated[float, Field(ge=-273.15, le=1800)]
    segment_start: bool = False
    set_temperature_c: Annotated[float | None, Field(ge=-273.15, le=1800)] = None
    stage_category: str | None = None
    stage_name: str | None = None
    mapping_status: str | None = None


class CandidateInputs(BaseModel):
    composition: dict[str, float]
    process: dict[str, float]
    categorical: dict[str, str] = Field(default_factory=dict)
    heat_pattern: list[HeatPoint] | None = Field(default=None, max_length=30)
    heat_time_basis: Literal["line_speed", "elapsed_time"] = "line_speed"

    @field_validator("composition", "process")
    @classmethod
    def numeric_values_are_finite(cls, value: dict[str, float]) -> dict[str, float]:
        invalid = sorted(name for name, amount in value.items() if not math.isfinite(amount))
        if invalid:
            raise ValueError(f"入力値は有限値にしてください: {', '.join(invalid)}")
        return value

    @model_validator(mode="after")
    def heat_pattern_is_a_real_route(self) -> "CandidateInputs":
        if self.heat_pattern is not None:
            if len(self.heat_pattern) < 2:
                raise ValueError("ヒートパターンは2点以上にしてください")
            times = [point.time_s for point in self.heat_pattern]
            if any(later <= earlier for earlier, later in zip(times, times[1:])):
                raise ValueError("ヒートパターンの時刻は厳密な昇順にしてください")
        return self


class CandidateInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80)] = "候補"
    inputs: CandidateInputs
    blend: SparseBlend | None = None
    editor_state: BlendEditorState = Field(default_factory=BlendEditorState)
    blend_validation: BlendValidationState = Field(default_factory=BlendValidationState)
    provenance: CandidateProvenance = Field(default_factory=lambda: DirectSourceRef(source_kind="direct"))

    @model_validator(mode="after")
    def blend_metadata_matches_candidate_shape(self) -> "CandidateInput":
        if self.blend is None:
            if self.editor_state.locked_material_ids:
                raise ValueError("配合のない候補へ配合行lockを保存できません")
            if self.blend_validation.status != "not_applicable":
                raise ValueError("配合のない候補へ配合検証状態を保存できません")
        elif set(self.editor_state.locked_material_ids) - {
            item.material_id for item in self.blend.items
        }:
            raise ValueError("配合にない原料をlockできません")
        return self


class CandidateUpdate(CandidateInput):
    expected_revision: Annotated[int, Field(ge=1)]


class Candidate(CandidateInput):
    id: str
    project_id: str
    revision: Annotated[int, Field(ge=1)]
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class CandidateImportError(BaseModel):
    row: int
    message: str


class CandidateImportResponse(BaseModel):
    created: int
    errors: list[CandidateImportError]
    candidates: list[Candidate]


class CandidateCapacity(BaseModel):
    schema_version: Literal["candidate-capacity/v1"] = "candidate-capacity/v1"
    limit: Annotated[int, Field(ge=1)]
    used: Annotated[int, Field(ge=0)]
    remaining: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def counts_are_consistent(self) -> "CandidateCapacity":
        if self.used > self.limit or self.remaining != self.limit - self.used:
            raise ValueError("candidate capacity counts are inconsistent")
        return self


class InputRange(BaseModel):
    min: float
    max: float

    @model_validator(mode="after")
    def range_is_valid(self) -> "InputRange":
        if not math.isfinite(self.min) or not math.isfinite(self.max):
            raise ValueError("入力許容範囲は有限の数値にしてください")
        if self.min >= self.max:
            raise ValueError("入力許容範囲の最小値は最大値より小さくしてください")
        return self


class TargetRange(BaseModel):
    lower: Annotated[float, Field(allow_inf_nan=False)]
    upper: Annotated[float, Field(allow_inf_nan=False)]

    @model_validator(mode="after")
    def range_is_valid(self) -> "TargetRange":
        if self.lower >= self.upper:
            raise ValueError("目標範囲の下限は上限より小さくしてください")
        return self


TargetValue = Annotated[float, Field(allow_inf_nan=False)] | TargetRange


class ProjectInput(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] = "焼鈍条件の候補検討"
    description: str = ""
    purpose: str = ""
    # Empty only for Chain Projects. `scientific_identity` is the canonical
    # discriminator; this column remains for legacy single-Task readers.
    task_id: str = "annealed-properties-v1"
    target_values: dict[str, TargetValue] = Field(default_factory=dict)
    input_ranges: dict[str, InputRange] = Field(default_factory=dict)
    response_curve_ranges: dict[str, dict[str, InputRange]] = Field(default_factory=dict)
    response_curve_points: Annotated[int, Field(ge=9, le=51)] = 17
    heat_stage_positions_m: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=dict)
    display_decimals: dict[str, Annotated[int, Field(ge=0, le=8)]] = Field(default_factory=dict)
    notes: str = ""
    decision_candidate_id: Annotated[str, Field(max_length=80)] = ""
    decision_snapshot_id: Annotated[str, Field(max_length=80)] = ""
    decision_note: Annotated[str, Field(max_length=500)] = ""

    @model_validator(mode="after")
    def decision_note_requires_candidate(self) -> "ProjectInput":
        if (self.decision_note or self.decision_snapshot_id) and not self.decision_candidate_id:
            raise ValueError("判断理由またはスナップショットを保存する場合は採用候補を指定してください")
        if self.decision_candidate_id and not self.decision_snapshot_id:
            raise ValueError("採用候補には判断時点の予測スナップショットが必要です")
        if self.decision_candidate_id and not self.decision_note:
            raise ValueError("採用候補には判断理由が必要です")
        return self


class ProjectCreateInput(ProjectInput):
    scientific_identity: ProjectScientificIdentity | None = None
    initial_candidate: CandidateInput | None = None
    dataset_view_revision_id: str | None = None
    task_contract_digest: str = ""
    model_package_ref_id: str | None = None
    model_package_manifest_digest: str = ""
    project_series_id: str | None = None
    new_project_series: ProjectSeriesCreateInput | None = None
    predecessor_project_id: str | None = None
    continuation_reason: str = ""
    design_space: DesignSpaceDefinition | None = None
    design_space_binding_provenance: Literal[
        "explicit", "generated_default", "inherited_predecessor"
    ] | None = None
    objective_definition: ObjectiveDefinition | None = None
    objective_binding_provenance: Literal[
        "explicit", "generated_default", "inherited_predecessor", "updated_revision", "none_configured"
    ] | None = None

    @model_validator(mode="after")
    def explicit_identity_does_not_conflict_with_legacy_fields(
        self,
    ) -> "ProjectCreateInput":
        if self.project_series_id is not None and self.new_project_series is not None:
            raise ValueError(
                "既存の検討グループと新しい検討グループを同時指定できません"
            )
        identity = self.scientific_identity
        if identity is None:
            return self
        if identity.identity_kind == "chain":
            if "task_id" in self.model_fields_set and self.task_id:
                raise ValueError("Chain Projectへtask_idを同時指定できません")
            legacy_bindings = (
                self.dataset_view_revision_id,
                self.task_contract_digest,
                self.model_package_ref_id,
                self.model_package_manifest_digest,
            )
            if any(legacy_bindings):
                raise ValueError(
                    "Chain Projectへ単一TaskのDataset/Package参照を同時指定できません"
                )
        elif "task_id" in self.model_fields_set and self.task_id != identity.task_id:
            raise ValueError("single-Task identityとtask_idが一致しません")
        return self


class ProjectUpdateInput(BaseModel):
    """Fields that may change without changing a Project's scientific identity."""

    name: Annotated[str, Field(min_length=1, max_length=120)] = "焼鈍条件の候補検討"
    description: str = ""
    purpose: str = ""
    target_values: dict[str, TargetValue] = Field(default_factory=dict)
    input_ranges: dict[str, InputRange] = Field(default_factory=dict)
    response_curve_ranges: dict[str, dict[str, InputRange]] = Field(default_factory=dict)
    response_curve_points: Annotated[int, Field(ge=9, le=51)] = 17
    heat_stage_positions_m: dict[str, Annotated[float, Field(ge=0, allow_inf_nan=False)]] = Field(default_factory=dict)
    display_decimals: dict[str, Annotated[int, Field(ge=0, le=8)]] = Field(default_factory=dict)
    notes: str = ""
    decision_candidate_id: Annotated[str, Field(max_length=80)] = ""
    decision_snapshot_id: Annotated[str, Field(max_length=80)] = ""
    decision_note: Annotated[str, Field(max_length=500)] = ""
    # Accepted only to detect stale/full-object clients. The service verifies
    # these values and never persists them through the update path.
    task_id: str | None = None
    dataset_view_revision_id: str | None = None
    task_contract_digest: str | None = None
    model_package_ref_id: str | None = None
    model_package_manifest_digest: str | None = None
    project_series_id: str | None = None
    predecessor_project_id: str | None = None
    scientific_identity: ProjectScientificIdentity | None = None
    design_space_digest: str | None = None
    objective_definition_digest: str | None = None

    @model_validator(mode="after")
    def decision_note_requires_candidate(self) -> "ProjectUpdateInput":
        if (self.decision_note or self.decision_snapshot_id) and not self.decision_candidate_id:
            raise ValueError("判断理由またはスナップショットを保存する場合は採用候補を指定してください")
        if self.decision_candidate_id and not self.decision_snapshot_id:
            raise ValueError("採用候補には判断時点の予測スナップショットが必要です")
        if self.decision_candidate_id and not self.decision_note:
            raise ValueError("採用候補には判断理由が必要です")
        return self


class ProjectDecisionInput(BaseModel):
    candidate_id: Annotated[str, Field(max_length=80)] = ""
    snapshot_id: Annotated[str, Field(max_length=80)] = ""
    note: Annotated[str, Field(max_length=500)] = ""

    @model_validator(mode="after")
    def complete_or_empty(self) -> "ProjectDecisionInput":
        populated = (self.candidate_id, self.snapshot_id, self.note)
        if any(populated) and not all(populated):
            raise ValueError("採用候補・予測スナップショット・判断理由をすべて指定してください")
        return self


class ProjectGroupMoveInput(BaseModel):
    project_series_id: Annotated[str, Field(min_length=1)]
    expected_project_series_id: str | None


class Project(ProjectInput):
    id: str
    scientific_identity: ProjectScientificIdentity
    dataset_view_revision_id: str | None = None
    task_contract_digest: str = ""
    model_package_ref_id: str | None = None
    model_package_manifest_digest: str = ""
    project_series_id: str | None = None
    predecessor_project_id: str | None = None
    continuation_reason: str = ""
    design_space: DesignSpaceDefinition | None = None
    design_space_digest: str | None = None
    design_space_binding_provenance: Literal[
        "explicit", "generated_default", "inherited_predecessor", "unbound_legacy"
    ] = "unbound_legacy"
    objective_definition: ObjectiveDefinition | None = None
    objective_definition_digest: str | None = None
    objective_binding_provenance: Literal[
        "explicit",
        "generated_default",
        "inherited_predecessor",
        "updated_revision",
        "none_configured",
        "unbound_legacy",
    ] = "unbound_legacy"
    binding_provenance: Literal["explicit", "assumed_current_at_upgrade", "unbound_legacy"] = "unbound_legacy"
    binding_migrated_at: datetime | None = None
    archived_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def legacy_task_column_matches_scientific_identity(self) -> "Project":
        if self.scientific_identity.identity_kind == "single_task":
            if self.task_id != self.scientific_identity.task_id:
                raise ValueError("Project task_id disagrees with single-Task identity")
        elif self.task_id:
            raise ValueError("Chain Project must not masquerade as a Prediction Task")
        return self


class ScreeningVariable(BaseModel):
    mode: Literal["fixed", "range", "list"]
    value: float | str | None = None
    min: float | None = None
    max: float | None = None
    values: list[float | str] | None = None

    @model_validator(mode="after")
    def complete_specification(self) -> "ScreeningVariable":
        if self.mode == "fixed" and self.value is None:
            raise ValueError("fixedにはvalueが必要です")
        if self.mode == "range" and (self.min is None or self.max is None or self.min >= self.max):
            raise ValueError("rangeにはmin < maxが必要です")
        if self.mode == "list" and not self.values:
            raise ValueError("listにはvaluesが必要です")
        return self


DEFAULT_SCREENING_SEED = 20260719
SCREENING_POOL_MULTIPLIER = 4


class ScreeningGoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["at_least", "at_most", "between"]
    lower: float | None = None
    upper: float | None = None

    @model_validator(mode="after")
    def complete_goal(self) -> "ScreeningGoal":
        values = [value for value in (self.lower, self.upper) if value is not None]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("選別基準は有限の数値にしてください")
        if self.direction == "at_least" and (self.lower is None or self.upper is not None):
            raise ValueError("at_leastにはlowerだけを指定してください")
        if self.direction == "at_most" and (self.upper is None or self.lower is not None):
            raise ValueError("at_mostにはupperだけを指定してください")
        if self.direction == "between" and (
            self.lower is None or self.upper is None or self.lower >= self.upper
        ):
            raise ValueError("betweenにはlower < upperを指定してください")
        return self


class ScreeningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: Literal[
        "design_space_map", "goal_search", "experiment_batch"
    ]
    source_run_id: Annotated[str, Field(min_length=1)] | None = None
    base_candidate_id: Annotated[str, Field(min_length=1)]
    base_inputs: CandidateInputs
    variables: Annotated[dict[str, ScreeningVariable], Field(min_length=1)]
    samples: Annotated[int, Field(ge=48, le=128)] = 64
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)] = DEFAULT_SCREENING_SEED
    target: Annotated[str, Field(min_length=1)] = "TS"
    target_goal: ScreeningGoal | None = None
    secondary_goals: dict[str, ScreeningGoal] = Field(default_factory=dict)
    objective_definition: ObjectiveDefinition | None = None
    proposal: ProposalStrategyRequest = ProposalStrategyRequest()
    batch_definition: BatchProposalDefinition | None = None

    @model_validator(mode="after")
    def target_is_not_a_secondary_goal(self) -> "ScreeningRequest":
        if self.target in self.secondary_goals:
            raise ValueError("主目標を副条件にも指定することはできません")
        if (
            self.batch_definition is not None
            and self.batch_definition.batch_size > self.samples
        ):
            raise ValueError("batch sizeは評価点数以下にしてください")
        if (
            self.batch_definition is not None
            and "candidate_pool_size" in self.batch_definition.model_fields_set
            and self.batch_definition.candidate_pool_size > self.samples
        ):
            raise ValueError("batch candidate poolは評価点数以下にしてください")
        if self.batch_definition is not None and any(
            item.candidate_revision is None
            for item in self.batch_definition.controls
        ):
            raise ValueError("exact Controlには選択時点のcandidate revisionが必要です")
        if self.purpose == "design_space_map":
            if self.target_goal is not None or self.secondary_goals:
                raise ValueError("領域を見るRunには目標を指定しません")
            if self.objective_definition is not None:
                raise ValueError("領域を見るRunにはObjectiveを指定しません")
            if self.batch_definition is not None or self.source_run_id is not None:
                raise ValueError("領域を見るRunにはbatch元を指定しません")
            if self.proposal.support_policy != "allow_with_warning":
                raise ValueError("領域を見るRunは学習範囲外も地図へ含めてください")
        elif self.purpose == "goal_search":
            if self.batch_definition is not None or self.source_run_id is not None:
                raise ValueError("有望候補Runにはbatch元を指定しません")
        elif self.batch_definition is None or self.source_run_id is None:
            raise ValueError("実験バッチには元の有望候補Runとbatch定義が必要です")
        return self


class ActualMeasurementInput(BaseModel):
    property: Annotated[str, Field(min_length=1)]
    mean: Annotated[float, Field(allow_inf_nan=False)]
    std: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0
    replicates: Annotated[int, Field(ge=1, le=999)] = 1
    unit: Annotated[str, Field(min_length=1)]
    experiment_no: str = ""
    measured_at: date | None = None
    note: str = ""


class ActualMeasurement(ActualMeasurementInput):
    id: str
    candidate_id: str
    snapshot_id: str
    created_at: datetime


class Prediction(BaseModel):
    value: float
    lower: float
    upper: float
    unit: str
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    point_statistic: Literal["mean", "median", "probability", "rate", "expected_category"]
    predictive_family: str
    quantiles: dict[str, float]
    categories: list[str] = Field(default_factory=list)
    goal_value: float | None = None
    goal_lower: float | None = None
    goal_upper: float | None = None
    goal_probability: Annotated[float | None, Field(ge=0, le=1)] = None
    goal_direction: Literal["at_least", "at_most", "between"] | None = None
    uncertainty_components: dict[str, float] | None = None

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_snapshot_semantics(cls, value: Any) -> Any:
        """Read immutable v1 snapshots without pretending they were re-predicted."""
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        migrated.setdefault("target_kind", "continuous")
        migrated.setdefault("point_statistic", "mean")
        migrated.setdefault("predictive_family", "empirical_quantiles")
        if "quantiles" not in migrated:
            quantiles: dict[str, float] = {}
            if "lower" in migrated:
                quantiles["0.05"] = migrated["lower"]
            if "upper" in migrated:
                quantiles["0.95"] = migrated["upper"]
            migrated["quantiles"] = quantiles
        return migrated


class RepeatSummary(BaseModel):
    mean: float
    std: float
    n: int


class SimilarObservation(BaseModel):
    observation_id: str = ""
    observation_ids: list[str] = Field(default_factory=list)
    parent_key: str
    source: str = ""
    layer: Literal["training", "historical"] | None = None
    source_scope: Literal["model_training_data", "project_reference_data"] | None = None
    distance: float
    components: dict[str, float] = Field(default_factory=dict)
    outputs: dict[str, float] = Field(default_factory=dict)
    repeat_summary: dict[str, RepeatSummary] = Field(default_factory=dict)
    melt_key: str | None = None
    process_key: str | None = None
    process_label: str = "工程履歴"
    relation_context_ids: list[str] = Field(default_factory=list)


class ModelIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = ""
    version: str = ""
    method: str = ""


class PackageIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = ""
    version: str = ""
    manifest_sha256: str = ""
    runtime_types: list[str] = Field(default_factory=list)


class FeaturePipelineIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = ""
    version: str = ""
    digest: str = ""
    input_schema_version: str = ""
    features: list[str] = Field(default_factory=list)


class TrainingDataIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    source_path: str = ""
    source_sha256: str = ""
    training_data_id: str = ""
    feature_dataset_id: str = ""
    training_code_revision: str = ""
    records: dict[str, int] = Field(default_factory=dict)


class SourceLifecycleIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector_id: str
    connector_configuration_digest: str
    source_adapter_id: str
    source_adapter_version: str
    raw_snapshot_id: str
    raw_snapshot_digest: str
    recipe_id: str
    recipe_digest: str
    curation_run_id: str
    curation_digest: str
    profile_revision_id: str
    profile_digest: str
    canonical_dataset_revision_id: str
    canonical_dataset_digest: str
    training_snapshot_id: str
    training_snapshot_digest: str
    training_selection_policy_digest: str
    materialization_adapter_id: str
    materialization_adapter_version: str
    materialized_training_sha256: str
    row_count: int


class PredictionIntervalIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    method: str = ""
    coverage: str | float | None = None
    grouping: str = ""
    folds: int | dict[str, int] | None = None
    note: str = ""


class SimilarityIdentity(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: str = ""
    method: str = ""


class ModelMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    package: PackageIdentity | None = None
    model: ModelIdentity | None = None
    feature_pipeline: FeaturePipelineIdentity | None = None
    training_data: TrainingDataIdentity | None = None
    source_lifecycle: SourceLifecycleIdentity | None = None
    prediction_interval: PredictionIntervalIdentity | None = None
    similarity: SimilarityIdentity | None = None


class Support(BaseModel):
    status: Literal["supported", "caution", "extrapolated"]
    distance: float
    percentile: float
    message: str
    components: dict[str, float]
    reference_count: int
    supported_threshold: float
    caution_threshold: float


class PredictionResponse(BaseModel):
    task_id: str
    candidate_id: str
    mode: Literal["preview", "detailed"]
    predictions: dict[str, Prediction]
    support: Support
    model_support: dict[str, Support] = Field(default_factory=dict)
    warnings: list[str]
    model_meta: ModelMetadata
    canonical_input: dict[str, object]
    similar: list[SimilarObservation]
    heat_pattern: list[HeatPoint]
    response_curve: list[dict[str, float]] | None = None


class RuntimeAvailability(BaseModel):
    runtime_type: str
    available: bool


class ModelPackagePredictorStatus(BaseModel):
    target: str
    runtime_type: str
    predictive_family: str


class ModelQualityTarget(BaseModel):
    model_config = ConfigDict(extra="allow")
    target: str
    parent_conditions: int
    mae: float
    rmse: float
    interval_coverage_90: float
    interval_coverage_method: str | None = None
    interval_coverage_observations: int | None = None


class ModelQualityReport(BaseModel):
    model_config = ConfigDict(extra="allow")
    split: str
    targets: list[ModelQualityTarget]


class ModelPackageStatus(BaseModel):
    id: str
    version: str
    task_id: str
    manifest_sha256: str
    active_runtimes: list[str]
    supported_runtimes: list[RuntimeAvailability]
    predictors: list[ModelPackagePredictorStatus]
    quality_report: ModelQualityReport


class TrainingDataColumn(BaseModel):
    key: str
    label: str
    unit: str | None = None
    group: Literal["識別", "原値", "正規化", "判定", "入力", "特徴量", "実測"]


class TrainingDataRow(BaseModel):
    observation_id: str
    parent_key: str
    values: dict[str, str | float | int | bool | None]


class ModelTrainingCurationTargetSummary(BaseModel):
    target: str
    usable_rows: int
    source_groups: int
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)


class ModelTrainingCurationSummary(BaseModel):
    source_rows: int
    input_usable_rows: int
    accepted_rows: int
    warning_rows: int
    quarantined_rows: int
    blocked_rows: int
    exclusion_reasons: dict[str, int] = Field(default_factory=dict)
    targets: list[ModelTrainingCurationTargetSummary]


class ModelTrainingDataPage(BaseModel):
    stage: Literal["curation", "selected", "features"]
    target: str
    target_label: str
    source_data_digest: str
    feature_dataset_digest: str
    feature_pipeline_id: str
    feature_pipeline_version: str
    training_unit: Literal["individual_observation", "parent_condition_mean"]
    total: int
    parent_conditions: int
    offset: int
    limit: int
    columns: list[TrainingDataColumn]
    rows: list[TrainingDataRow]
    curation_summary: ModelTrainingCurationSummary


class SnapshotPayload(BaseModel):
    model_config = ConfigDict(extra="allow")
    prediction: PredictionResponse | None = None
    provenance: ModelMetadata | None = None
    project_design_space_digest: str | None = None
    project_design_space_binding_provenance: Literal[
        "explicit", "generated_default", "inherited_predecessor", "unbound_legacy"
    ] = "unbound_legacy"


class SnapshotResponse(BaseModel):
    id: str
    candidate_id: str
    created_at: datetime
    payload: SnapshotPayload


class ProjectDecisionHistory(BaseModel):
    candidate_id: str
    snapshot_id: str
    note: str


class CandidateCurrentHistory(BaseModel):
    revision: int
    updated_at: datetime


class TaskCatalogItem(BaseModel):
    definition: ResolvedTaskDefinition
    starter_candidate: CandidateInput


class SnapshotHistoryItem(BaseModel):
    id: str
    candidate_id: str
    created_at: datetime
    candidate_revision: int | None = None
    prediction_summary: dict[str, Prediction]
    model_ref: ModelMetadata | None = None


class CandidateHistoryItem(BaseModel):
    candidate: Candidate
    current: CandidateCurrentHistory
    snapshots: list[SnapshotHistoryItem]
    chain_snapshots: list[ChainSnapshot] = Field(default_factory=list)
    chain_analysis_variants: list[ActualConditionedVariant] = Field(
        default_factory=list
    )
    chain_distribution_runs: list[ChainDistributionRun] = Field(
        default_factory=list
    )
    actuals: list[ActualMeasurement]
    decision: ProjectDecisionHistory | None = None


class ProjectHistoryResponse(BaseModel):
    project: Project
    candidates: list[CandidateHistoryItem]


class CurvePoint(BaseModel):
    x: float
    value: float
    lower: float
    upper: float
    target_kind: Literal["continuous", "continuous_positive", "binary", "count", "ordinal"]
    point_statistic: Literal["mean", "median", "probability", "rate", "expected_category"]
    predictive_family: str
    quantiles: dict[str, float]
    categories: list[str] = Field(default_factory=list)


class CurveVariable(BaseModel):
    id: str
    label: str
    unit: str
    min: float
    max: float
    current: float
    training_range: InputRange | None = None


class ResponseCurveResponse(BaseModel):
    target: str
    variable: CurveVariable
    points: list[CurvePoint]
    output_range: InputRange | None = None
    point_count: int
    policy_id: str


class CurveFamilySeries(BaseModel):
    level: float | str | None = None
    label: str
    points: list[CurvePoint]


class CurveVariableCategorical(BaseModel):
    id: str
    label: str
    choices: list[str]
    current: str


class CurveFamilyResponse(BaseModel):
    target: str
    axis: CurveVariable
    vary: CurveVariable | None = None
    vary_categorical: CurveVariableCategorical | None = None
    series: list[CurveFamilySeries]
    output_range: InputRange | None = None
    point_count: int
    policy_id: str


class DurationDiagnostic(BaseModel):
    total: float
    last: float
    max: float
    average: float


class OperationDiagnostic(BaseModel):
    runtime_types: list[str]
    hits: int
    misses: int
    coalesced: int
    computations: int
    computation_duration_ms: DurationDiagnostic
    total_duration_ms: DurationDiagnostic


class InferenceDiagnosticsResponse(BaseModel):
    max_entries: int
    cached_entries: int
    operations: dict[str, OperationDiagnostic]


class LineageIndexItem(BaseModel):
    key: str
    entity_type: str
    has_issue: bool
    family: str | None = None
    melt_keys: list[str] = Field(default_factory=list)
    project: str | None = None
    route: str | None = None
    peak_temperature_c: float | None = None
    learning_status: str | None = None
    has_observation: bool | None = None
    observation_summary: dict[str, RepeatSummary] | None = None
    review_status: Literal["noted", "later", "accepted", "needs_fix", "hidden"] | None = None
    review_note: str | None = None


class LineageIndexResponse(BaseModel):
    items: list[LineageIndexItem]
    matched_entities: int
    total_entities: int
    relation_rows: int
    detected_issues: int
    counts_by_type: dict[str, int]
    review_count: int


class LineageNodeReviewInput(BaseModel):
    entity_type: Annotated[str, Field(min_length=1, max_length=120)]
    status: Literal["noted", "later", "accepted", "needs_fix", "hidden"]
    note: Annotated[str, Field(max_length=1000)] = ""


class LineageNodeReview(LineageNodeReviewInput):
    project_id: str
    entity_key: str
    created_at: datetime
    updated_at: datetime


class LineageNodeReviewList(BaseModel):
    items: list[LineageNodeReview]
    counts_by_status: dict[str, int]


class ScreeningPoint(BaseModel):
    index: int
    inputs: dict[str, float | str]
    candidate: CandidateInput
    prediction: Prediction
    predictions: dict[str, Prediction] = Field(default_factory=dict)
    color_value: float
    support: Support
    warnings: list[str] = Field(default_factory=list)
    similar: list[SimilarObservation] = Field(default_factory=list)
    score: float | None
    goal_evaluation: "ScreeningGoalEvaluation"
    secondary_goal_evaluations: dict[str, "ScreeningGoalEvaluation"] = Field(default_factory=dict)


class ScreeningGoalEvaluation(BaseModel):
    score: float | None
    method: Literal["achievement_probability", "directional_shortfall", "range_shortfall", "absolute_distance", "support_distance"]
    achieved: bool | None
    achievement_probability: float | None


class ScreeningScoreContract(BaseModel):
    version: Literal["screening-score/v1", "screening-score/v2", "screening-score/v3"]
    preference: Literal["lower_is_better"]
    direction: Literal["at_least", "at_most", "between", "target"] | None
    target_value: float | None
    lower: float | None = None
    upper: float | None = None
    probability_available: bool
    probability_semantics: Literal["probability_of_achieving_goal"] | None = None
    ranking_policy: Literal["support_tier_then_secondary_goals_then_score"] | None = None
    fallback: Literal["directional_shortfall", "range_shortfall", "absolute_distance", "support_distance"]
    display_label: str


class ScreeningProposalStrategy(BaseModel):
    id: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    seed: Annotated[int, Field(ge=0, le=2_147_483_647)]
    requested_count: Annotated[int, Field(ge=1)]
    pool_multiplier: Annotated[int, Field(ge=1)] = SCREENING_POOL_MULTIPLIER
    generator_id: str = "latin_hypercube"
    generator_version: str = "1.0.0"
    generator_parameters: dict[str, float | str | bool] = Field(default_factory=dict)
    distance_id: str = "scalar_axis_rms"
    distance_version: str = "1.0.0"
    distance_parameters: dict[str, float | str | bool] = Field(default_factory=dict)
    distance_usage: Literal["batch_selector_only"] = "batch_selector_only"
    acquisition_id: str = "goal_achievement"
    acquisition_version: str = "1.0.0"
    selector_id: str = "ranked_top_k"
    selector_version: str = "1.0.0"
    exploration_parameter: float | None = None
    parameter_role: Literal["confidence_multiplier", "improvement_margin"] | None = None
    acquisition_representation: Literal["normal_mean_std"] | None = None
    standard_deviation_methods: tuple[str, ...] = ()
    support_policy: Literal[
        "supported_first", "exclude_extrapolated", "allow_with_warning"
    ] = "supported_first"
    fallback_from: str | None = None
    fallback_policy: Literal["reject", "deterministic_goal"] = "reject"
    incumbent_value: float | None = None
    incumbent_resolution: ProposalIncumbentResolution | None = None
    constraint_treatment: Literal[
        "feasibility_first_then_rank"
    ] = "feasibility_first_then_rank"
    uncertainty_treatment: Literal["predictive_standard_deviation"] | None = None


class ProposalCoverageEvidence(BaseModel):
    observed_min: float = Field(allow_inf_nan=False)
    observed_max: float = Field(allow_inf_nan=False)
    observed_mean: float = Field(allow_inf_nan=False)
    normalized_span: float = Field(ge=0, le=1, allow_inf_nan=False)


class ScreeningProposalDiagnostics(BaseModel):
    generated_count: Annotated[int, Field(ge=1)]
    valid_count: Annotated[int, Field(ge=0)]
    evaluated_count: Annotated[int, Field(ge=0)]
    rejected_count: Annotated[int, Field(ge=0)]
    rejection_rate: Annotated[float, Field(ge=0, le=1)]
    rejected_by_reason: dict[str, Annotated[int, Field(ge=1)]] = Field(default_factory=dict)
    selected_count: Annotated[int, Field(ge=0)] = 0
    coverage_by_path: dict[str, ProposalCoverageEvidence] = Field(default_factory=dict)

    @model_validator(mode="after")
    def counts_share_one_denominator(self) -> "ScreeningProposalDiagnostics":
        if self.valid_count + self.rejected_count != self.generated_count:
            raise ValueError("valid_count + rejected_count must equal generated_count")
        if self.evaluated_count > self.valid_count:
            raise ValueError("evaluated_count must not exceed valid_count")
        if self.selected_count > self.evaluated_count:
            raise ValueError("selected_count must not exceed evaluated_count")
        if sum(self.rejected_by_reason.values()) != self.rejected_count:
            raise ValueError("rejected_by_reason must sum to rejected_count")
        expected_rate = self.rejected_count / self.generated_count
        if not math.isclose(self.rejection_rate, expected_rate, rel_tol=0, abs_tol=1e-12):
            raise ValueError("rejection_rate must use generated_count as its denominator")
        return self


class ScreeningRunResponse(BaseModel):
    schema_version: Literal["screening-run/v1", "screening-run/v2", "screening-run/v3", "screening-run/v4", "screening-run/v5", "screening-run/v6", "screening-run/v7"] = "screening-run/v1"
    id: str
    project_id: str
    created_at: datetime
    purpose: Literal[
        "design_space_map", "goal_search", "experiment_batch"
    ] | None = None
    source_run_id: str | None = None
    seed: int
    base_candidate_id: str
    base_inputs: CandidateInputs | None = None
    base_canonical_input: dict[str, object]
    model_provenance: ModelMetadata
    target: str
    target_goal: ScreeningGoal | None = None
    secondary_goals: dict[str, ScreeningGoal] = Field(default_factory=dict)
    target_value: float | None = Field(
        default=None,
        deprecated="screening-run/v1-v3 compatibility; use target_goal",
    )
    secondary_targets: dict[str, float] = Field(
        default_factory=dict,
        deprecated="screening-run/v1-v3 compatibility; use secondary_goals",
    )
    score_contract: ScreeningScoreContract
    samples: int
    variables: dict[str, ScreeningVariable]
    design_space: dict[str, Any] | None = None
    design_space_digest: str | None = None
    project_design_space_digest: str | None = None
    project_design_space_binding_provenance: Literal[
        "explicit", "generated_default", "inherited_predecessor", "unbound_legacy"
    ] = "unbound_legacy"
    objective_definition: ObjectiveDefinition | None = None
    objective_definition_digest: str | None = None
    objective_binding_provenance: Literal[
        "explicit", "project_revision", "legacy_screening"
    ] = "legacy_screening"
    objective_execution: ProposalObjectiveExecution | None = None
    proposal_strategy: ScreeningProposalStrategy | None = None
    proposal_diagnostics: ScreeningProposalDiagnostics | None = None
    proposal_pool: list[ProposalCandidateEvaluation] = Field(default_factory=list)
    proposal_rejections: list[ProposalRejectedCandidate] = Field(default_factory=list)
    batch_proposal: BatchProposalRun | None = None
    rejection_summary: dict[str, int] | None = Field(
        default=None,
        deprecated="screening-run/v1-v2 compatibility; use proposal_diagnostics",
    )
    points: list[ScreeningPoint]
    representative_points: list[ScreeningPoint]

    @model_validator(mode="after")
    def proposal_identity_is_internally_consistent(self) -> "ScreeningRunResponse":
        if self.schema_version not in {"screening-run/v3", "screening-run/v4", "screening-run/v5", "screening-run/v6", "screening-run/v7"}:
            return self
        if (
            self.design_space is None
            or self.design_space_digest is None
            or self.proposal_strategy is None
            or self.proposal_diagnostics is None
        ):
            raise ValueError("screening-run/v3 requires design-space, strategy, and diagnostics")
        if self.proposal_strategy.seed != self.seed:
            raise ValueError("proposal strategy seed must match screening run seed")
        if self.proposal_strategy.requested_count != self.samples:
            raise ValueError("proposal strategy requested_count must match samples")
        expected_generated = self.samples * self.proposal_strategy.pool_multiplier
        if self.proposal_diagnostics.generated_count != expected_generated:
            raise ValueError("proposal diagnostics must cover the complete generated pool")
        if self.schema_version in {"screening-run/v6", "screening-run/v7"}:
            if self.proposal_diagnostics.evaluated_count != self.proposal_diagnostics.valid_count:
                raise ValueError("screening-run/v6 must evaluate the complete valid pool")
            if self.proposal_diagnostics.selected_count != self.samples:
                raise ValueError("screening-run/v6 selected_count must match samples")
            if len(self.proposal_pool) != self.proposal_diagnostics.valid_count:
                raise ValueError("screening-run/v6 proposal_pool must preserve every evaluated point")
            if len(self.proposal_rejections) != self.proposal_diagnostics.rejected_count:
                raise ValueError("screening-run/v6 must preserve every rejected generated point")
            pool_indices = {
                item.pool_index
                for item in (*self.proposal_pool, *self.proposal_rejections)
            }
            if len(pool_indices) != self.proposal_diagnostics.generated_count:
                raise ValueError("screening-run/v6 generated pool indices must be complete")
            if sum(item.selected_rank is not None for item in self.proposal_pool) != self.samples:
                raise ValueError("screening-run/v6 proposal_pool must identify every selected point")
            if self.batch_proposal is not None:
                if (
                    self.batch_proposal.distance_id
                    != self.proposal_strategy.distance_id
                    or self.batch_proposal.distance_version
                    != self.proposal_strategy.distance_version
                    or self.batch_proposal.distance_parameters
                    != self.proposal_strategy.distance_parameters
                ):
                    raise ValueError(
                        "batch proposal distance must match proposal strategy evidence"
                    )
                point_by_index = {item.index: item for item in self.points}
                pool_by_index = {
                    item.pool_index: item for item in self.proposal_pool
                }
                if any(
                    (
                        item.source == "acquisition_ranked"
                        and (
                            item.point_index not in point_by_index
                            or item.pool_index not in pool_by_index
                        )
                    )
                    or (
                        item.source == "exact_control"
                        and (
                            item.point_index is not None
                            or item.candidate_id is None
                            or item.candidate_revision is None
                        )
                    )
                    for item in self.batch_proposal.selected
                ):
                    raise ValueError(
                        "batch proposal must reference the saved screening shortlist"
                    )
        elif self.proposal_diagnostics.evaluated_count != self.samples:
            raise ValueError("proposal diagnostics evaluated_count must match samples")
        if self.schema_version in {"screening-run/v4", "screening-run/v5", "screening-run/v6", "screening-run/v7"}:
            if self.__dict__["target_value"] is not None or self.__dict__["secondary_targets"]:
                raise ValueError("screening-run/v4 must use target_goal and secondary_goals")
            if self.target in self.secondary_goals:
                raise ValueError("target must not also appear in secondary_goals")
            expected_direction = self.target_goal.direction if self.target_goal else None
            if self.score_contract.direction != expected_direction:
                raise ValueError("score contract direction must match target_goal")
        if self.schema_version in {"screening-run/v5", "screening-run/v6", "screening-run/v7"}:
            if self.objective_definition is None or self.objective_definition_digest is None:
                raise ValueError("screening-run/v5 requires an Objective Definition")
            if self.objective_definition.digest != self.objective_definition_digest:
                raise ValueError("Objective Definition digest does not match its payload")
            expected_target = (
                self.target_goal.lower
                if self.target_goal and self.target_goal.direction == "at_least"
                else self.target_goal.upper
                if self.target_goal and self.target_goal.direction == "at_most"
                else None
            )
            expected_lower = self.target_goal.lower if self.target_goal else None
            expected_upper = self.target_goal.upper if self.target_goal else None
            if (
                self.score_contract.target_value != expected_target
                or self.score_contract.lower != expected_lower
                or self.score_contract.upper != expected_upper
            ):
                raise ValueError("score contract bounds must match target_goal")
        if self.schema_version == "screening-run/v7":
            if self.purpose is None:
                raise ValueError("screening-run/v7 requires purpose")
            if self.purpose == "design_space_map":
                if (
                    self.source_run_id is not None
                    or self.target_goal is not None
                    or self.secondary_goals
                    or self.objective_execution is not None
                    or self.batch_proposal is not None
                ):
                    raise ValueError("design-space map must not contain goal or batch execution")
                if self.score_contract.fallback != "support_distance":
                    raise ValueError("design-space map must use support-distance evidence")
            elif self.purpose == "goal_search":
                if self.source_run_id is not None or self.batch_proposal is not None:
                    raise ValueError("goal search must not contain batch evidence")
                if self.objective_execution is None:
                    raise ValueError("goal search requires objective execution")
            elif (
                self.source_run_id is None
                or self.batch_proposal is None
                or self.objective_execution is None
            ):
                raise ValueError("experiment batch requires its source and objective evidence")
        return self


class ScreeningCandidateBatchRequest(BaseModel):
    point_indices: Annotated[list[int], Field(min_length=1, max_length=10)]


class ScreeningCandidateBatchResponse(BaseModel):
    candidates: list[Candidate]
    skipped_point_indices: list[int] = Field(default_factory=list)


class PredictionComparison(BaseModel):
    actual: ActualMeasurement
    snapshot_id: str
    snapshot_created_at: datetime
    candidate_revision: int | None = None
    prediction: PredictionResponse
    provenance: ModelMetadata


class PredictionVsActualResponse(BaseModel):
    candidate_id: str
    actuals: list[ActualMeasurement]
    comparisons: list[PredictionComparison]


class DetailedPredictionResponse(BaseModel):
    prediction: PredictionResponse
    snapshot: SnapshotResponse


class FieldError(BaseModel):
    path: str
    message: str


class ApiError(BaseModel):
    code: Literal[
        "not_found",
        "revision_conflict",
        "candidate_limit",
        "adopted_candidate",
        "candidate_archived",
        "candidate_provenance_immutable",
        "chain_project_requires_chain_candidate_api",
        "project_task_locked",
        "project_group_conflict",
        "protected_project",
        "project_has_successors",
        "project_has_derived_candidates",
        "project_archived",
        "active_project_purge",
        "project_purge_confirmation_mismatch",
        "data_integrity_error",
        "validation_error",
        "batch_feasibility_infeasible",
        "batch_greedy_search_exhausted",
        "runtime_unavailable",
        "subsystem_unavailable",
    ]
    message: str
    field_errors: list[FieldError] = Field(default_factory=list)
    current_candidate: Candidate | None = None
    availability: SubsystemAvailability | None = None


class DataQualityIssue(BaseModel):
    issue_id: str
    issue_type: Literal[
        "missing_key",
        "orphan_entity",
        "duplicate_key",
        "invalid_reference",
        "out_of_range",
        "suspicious_distribution",
        "curation_quarantine",
        "missing_target",
    ]
    source_sheet: str
    entity_key: str
    detail: str
    focus_entity_key: str | None
    related_entity_keys: list[str]
    missing_reference_key: str | None
    suggested_view: Literal["lineage", "source_sheet"]


class QualityScenario(BaseModel):
    model_config = ConfigDict(extra="allow")
    scenario_id: str
    category: str = Field(alias="分類")
    target_key: str = Field(alias="対象キー")
    target_sheet: str = Field(alias="対象シート")
    expected_insight: str = Field(alias="期待する気づき")


class DatasetIdentity(BaseModel):
    task_id: str
    source_path: str
    source_sha256: str
    profile_id: str
    profile_path: str


class QualityResponse(BaseModel):
    # Legacy scenario fields remain until the UI is switched to detected issues.
    total: int
    by_category: dict[str, int]
    issues: list[QualityScenario]
    reference_scenarios: list[QualityScenario]
    detected_total: int
    detected_by_type: dict[str, int]
    detected_issues: list[DataQualityIssue]
    dataset: DatasetIdentity


class PropertySummary(BaseModel):
    count: int
    min: float
    mean: float
    std: float
    median: float
    max: float


class ConnectedObservation(BaseModel):
    id: str
    source: str
    parent_key: str
    outputs: dict[str, float]


class ObservationGroup(BaseModel):
    stage: str
    test_type: str
    property: str
    count: int
    min: float
    mean: float
    std: float
    median: float
    max: float
    observations: list[ConnectedObservation]


class LineageGraphNode(BaseModel):
    key: str
    entity_type: str
    source_sheet: str
    exists: bool
    selected: bool
    issue_types: list[str]


class LineageGraphEdge(BaseModel):
    source: str
    target: str
    route_rows: list[int]


class LineageGraph(BaseModel):
    nodes: list[LineageGraphNode]
    edges: list[LineageGraphEdge]
    relation_row_count: int
    visible_node_count: int
    total_node_count: int
    node_limit: int
    all_reachable: bool
    has_more: bool
    omitted_node_count: int


class EvidenceImageRef(BaseModel):
    """A micrograph the source row points at, and whether the file is there.

    `available=False` keeps a declared-but-missing image visible as missing
    instead of silently dropping the observation's evidence.
    """

    declared_path: str
    available: bool
    reason: str | None = None


class LineageNodeDetail(BaseModel):
    key: str
    entity_type: str
    source_sheet: str
    evidence_image: EvidenceImageRef | None = None
    source_row: dict[str, str | float | int | bool | None]
    primary_conditions: dict[str, str | float | int | bool | None]
    composition: dict[str, float]
    heat_pattern: list[HeatPoint]
    connected_observation_count: int
    connected_observations: list[ConnectedObservation]
    observation_groups: list[ObservationGroup]
    property_summary: dict[str, PropertySummary]
    related_entities: dict[str, list[str]]
    missing_source: bool = False


class LineageCandidateOption(BaseModel):
    process_key: str
    process_role: Literal["annealing", "hot_rolling"]
    process_label: str
    melt_key: str


class LineageResponse(BaseModel):
    # key/relations/quality_issues are the existing renderer contract.
    key: str
    relations: dict[str, list[str]]
    quality_issues: list[DataQualityIssue]
    node: LineageNodeDetail
    graph: LineageGraph
    candidate_eligible: bool
    candidate_reason: str
    candidate_options: list[LineageCandidateOption] = Field(default_factory=list)
    review: LineageNodeReview | None = None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
