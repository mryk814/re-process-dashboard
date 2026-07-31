from __future__ import annotations

from datetime import datetime
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class DataLibraryDataAsset(BaseModel):
    """Browser-safe Data Asset summary without its local filesystem locator."""

    id: str
    original_filename: str
    sha256: str
    media_type: str
    locator_kind: Literal["managed", "bundled"]
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
    storage_scope: Literal["bundled", "personal"] = "bundled"


class DataLibraryModelPackage(BaseModel):
    """Browser-safe Model Package reference without its local filesystem locator."""

    id: str
    package_id: str
    task_id: str
    task_contract_digest: str
    manifest_digest: str
    manifest_json: dict[str, Any]
    created_at: datetime
    archived_at: datetime | None = None
    storage_scope: Literal["bundled", "personal"] = "bundled"


class ModelPackageRegistrationWarning(BaseModel):
    source: str
    reference: str | None = None
    message: str


class DataLibraryResourceWarning(BaseModel):
    """Browser-safe resource warning without personal filesystem locators."""

    source: str
    reference: str | None = None
    message: str


_ABSOLUTE_LOCATOR = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|file://|/(?:Users|home|tmp|var|private)/)")


def present_resource_warning(
    warning: ModelPackageRegistrationWarning,
) -> DataLibraryResourceWarning:
    """Keep actionable warning text while omitting local absolute paths."""

    def label(value: str) -> str:
        return value.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] or "Model Package"

    message = warning.message
    if _ABSOLUTE_LOCATOR.search(message):
        message = "Model Packageを読み込めません。Packageの内容とPrediction Taskの対応を確認してください。"
    return DataLibraryResourceWarning(
        source=label(warning.source),
        reference=(
            label(warning.reference)
            if warning.reference and _ABSOLUTE_LOCATOR.search(warning.reference)
            else warning.reference
        ),
        message=message,
    )


class ModelPackageRefreshResult(BaseModel):
    model_packages: list[DataLibraryModelPackage]
    warnings: list[DataLibraryResourceWarning] = Field(default_factory=list)


class TaskResourceRefreshResult(BaseModel):
    task_ids: list[str]
    added_task_ids: list[str] = Field(default_factory=list)
    model_package_ids: list[str] = Field(default_factory=list)
    added_model_package_ids: list[str] = Field(default_factory=list)
    warnings: list[DataLibraryResourceWarning] = Field(default_factory=list)


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
    data_asset: DataLibraryDataAsset
    profile_revision: ProfileRevision
    profile_available: bool = False
    supported_task_ids: list[str]
    dataset_views: list[DatasetViewRevision] = Field(default_factory=list)


class SampleGalleryItem(BaseModel):
    project_id: str
    task_id: str
    name: str
    installed: bool
    available: bool
    unavailable_reason: str = ""
    removable: bool = False
    remove_blocked_reason: str = ""


class SampleGalleryInstallInput(BaseModel):
    project_ids: list[str] = Field(default_factory=list)


class ProfileWorkbenchProfileOption(BaseModel):
    profile_id: str
    source_name: str
    profile_digest: str
    task_ids: list[str]
    personal: bool = False


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


class ProfileWorkbenchBindingCandidate(BaseModel):
    source_name: str
    score: Annotated[float, Field(ge=0, le=1)]


class ProfileWorkbenchBindingSlot(BaseModel):
    slot_id: str
    binding_type: Literal["sheet", "column"]
    role: str
    semantic_kind: Literal[
        "entity_key",
        "relation_join",
        "input",
        "output",
        "technical",
        "policy",
        "series",
    ]
    canonical_name: str
    expected_source_name: str
    source_unit: str | None = None
    canonical_unit: str | None = None
    source_unit_candidates: list[str] = Field(default_factory=list)
    required: bool
    state: Literal["unresolved", "suggested", "confirmed"]
    selected_source_name: str | None = None
    candidates: list[ProfileWorkbenchBindingCandidate] = Field(default_factory=list)


class ProfileWorkbenchBindingDraft(BaseModel):
    base_profile_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    complete: bool
    slots: list[ProfileWorkbenchBindingSlot]


class ProfileWorkbenchConfirmedBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot_id: str
    state: Literal["confirmed"]
    source_name: Annotated[str, Field(min_length=1)]
    source_unit: str | None = None


class ProfileWorkbenchInspection(BaseModel):
    source_filename: str
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    sheets: list[ProfileWorkbenchSheetInventory]
    selected_profile_digest: str | None = None
    auto_detected: bool = False
    profile_error: str | None = None
    validation: ProfileWorkbenchValidation | None = None
    binding_draft: ProfileWorkbenchBindingDraft | None = None


class ProfileWorkbenchDraftSave(BaseModel):
    profile_id: str
    profile_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    profile_locator: str
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    base_profile_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    validation: ProfileWorkbenchValidation


class ProfileWorkbenchRegistration(BaseModel):
    reused_existing: bool
    data_asset_id: str
    profile_revision_id: str
    dataset_revision_id: str
    dataset_view_revision_id: str
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    profile_id: str
    task_ids: list[str]
    previous_dataset_revision_id: str | None = None
    previous_source_sha256: str | None = None
