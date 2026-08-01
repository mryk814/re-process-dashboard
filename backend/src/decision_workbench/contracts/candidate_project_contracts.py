from __future__ import annotations

import math
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from decision_workbench.contracts.blend_contracts import (
    BlendEditorState,
    BlendValidationState,
    SparseBlend,
)
from decision_workbench.contracts.chain_contracts import (
    ChainProjectIdentity,
    ProjectScientificIdentity,
)
from decision_workbench.contracts.data_library_contracts import (
    DataLibraryDataset,
    DataLibraryModelPackage,
    DatasetViewRevision,
    ProjectSeries,
    ProjectSeriesCreateInput,
)
from decision_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from decision_workbench.contracts.missingness_contracts import MissingKind
from decision_workbench.contracts.objective_contracts import ObjectiveDefinition
from decision_workbench.contracts.task_contracts import CandidateProvenance, DirectSourceRef

class ProjectCreationOptions(BaseModel):
    datasets: list[DataLibraryDataset]
    dataset_views: list[DatasetViewRevision]
    model_packages: list[DataLibraryModelPackage]
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
    input_missing_kinds: dict[str, MissingKind] = Field(default_factory=dict)

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
        if any(
            path.count(".") != 1 or path.split(".", 1)[0] not in {
                "composition",
                "process",
                "categorical",
            }
            for path in self.input_missing_kinds
        ):
            raise ValueError("input missing kindにはcanonical input pathを指定してください")
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
    # null は「グループなしへ移動」を表す明示的な操作値。空文字は未選択との
    # 取り違えを防ぐために受け付けない。
    project_series_id: Annotated[str, Field(min_length=1)] | None
    expected_project_series_id: str | None


class Project(ProjectInput):
    id: str
    starter: bool = False
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
