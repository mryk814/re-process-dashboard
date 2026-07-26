"""Contracts for credential-free source refresh, curation and approval."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from pydantic import Field, model_validator

from material_workbench.contracts.task_contracts import ContractModel
from material_workbench.execution.inference_work_graph import semantic_digest


JsonRecord = dict[str, Any]


class ObjectSelection(ContractModel):
    schema_version: Literal["object-selection/v1"] = "object-selection/v1"
    format: Literal["json_array", "jsonl"]
    primary_key: Annotated[str | None, Field(min_length=1)] = None
    included_fields: tuple[Annotated[str, Field(min_length=1)], ...] = ()
    source_adapter_id: str | None = None
    source_adapter_version: str | None = None

    @model_validator(mode="after")
    def adapter_identity_is_complete(self) -> "ObjectSelection":
        if bool(self.source_adapter_id) != bool(self.source_adapter_version):
            raise ValueError(
                "source adapter id and version must be declared together"
            )
        return self

    @property
    def digest(self) -> str:
        payload = self.model_dump(mode="json")
        if self.source_adapter_id is None:
            payload.pop("source_adapter_id")
            payload.pop("source_adapter_version")
        return semantic_digest(payload)


class ConnectorSchedule(ContractModel):
    schema_version: Literal["connector-schedule/v1"] = "connector-schedule/v1"
    schedule_id: Annotated[str, Field(min_length=1, max_length=120)]
    interval_minutes: Annotated[int, Field(ge=15, le=43_200)]
    enabled: bool = False


class SourceConnectorCreateInput(ContractModel):
    schema_version: Literal["source-connector/v1"] = "source-connector/v1"
    name: Annotated[str, Field(min_length=1, max_length=160)]
    connector_type: Literal["object_storage_json_v1"]
    source_locator: Annotated[str, Field(min_length=1, max_length=500)]
    selection: ObjectSelection
    trigger_policy: Literal["manual_only", "schedulable"] = "manual_only"
    schedule: ConnectorSchedule | None = None

    @model_validator(mode="after")
    def schedule_matches_policy(self) -> "SourceConnectorCreateInput":
        if self.trigger_policy == "manual_only" and self.schedule is not None:
            raise ValueError("manual_only Connectorにはscheduleを指定できません")
        if self.trigger_policy == "schedulable" and self.schedule is None:
            raise ValueError("schedulable Connectorにはscheduleが必要です")
        locator = urlsplit(self.source_locator)
        sensitive_keys = {
            "token",
            "secret",
            "password",
            "credential",
            "signature",
            "x-amz-signature",
            "sig",
            "sas",
        }
        query_keys = {key.lower() for key, _ in parse_qsl(locator.query)}
        if locator.username or locator.password or query_keys & sensitive_keys:
            raise ValueError(
                "source locatorへcredentialや署名付きqueryを含められません"
            )
        return self

    @property
    def calculated_configuration_digest(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"id", "configuration_digest", "created_at"},
        )
        selection = payload["selection"]
        if self.selection.source_adapter_id is None:
            selection.pop("source_adapter_id")
            selection.pop("source_adapter_version")
        return semantic_digest(payload)


class SourceConnector(SourceConnectorCreateInput):
    id: Annotated[str, Field(min_length=1)]
    configuration_digest: Annotated[str, Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def digest_matches_configuration(self) -> "SourceConnector":
        payload = SourceConnectorCreateInput.model_validate(
            self.model_dump(
                mode="json",
                exclude={"id", "configuration_digest", "created_at"},
            )
        )
        if payload.calculated_configuration_digest != self.configuration_digest:
            raise ValueError("Connector configuration digestが内容と一致しません")
        return self


class SourceFetchRequest(ContractModel):
    schema_version: Literal["source-fetch-request/v1"] = "source-fetch-request/v1"
    trigger_kind: Literal["manual", "scheduled"] = "manual"
    object_content: Annotated[str, Field(min_length=1, max_length=5_000_000)]
    object_version: Annotated[str, Field(min_length=1, max_length=200)]
    retry_of: str | None = None


class RawSnapshotDiff(ContractModel):
    comparable: bool
    reason: str = ""
    added_rows: Annotated[int, Field(ge=0)] = 0
    changed_rows: Annotated[int, Field(ge=0)] = 0
    removed_rows: Annotated[int, Field(ge=0)] = 0
    unchanged_rows: Annotated[int, Field(ge=0)] = 0


class RawSourceSnapshot(ContractModel):
    schema_version: Literal["raw-source-snapshot/v1"] = "raw-source-snapshot/v1"
    id: Annotated[str, Field(min_length=1)]
    connector_id: Annotated[str, Field(min_length=1)]
    connector_configuration_digest: Annotated[str, Field(min_length=1)]
    source_locator: Annotated[str, Field(min_length=1)]
    selection_digest: Annotated[str, Field(min_length=1)]
    object_version: str
    trigger_kind: Literal["manual", "scheduled"]
    captured_at: datetime
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    row_count: Annotated[int, Field(ge=0)]
    rows: tuple[JsonRecord, ...]
    previous_snapshot_id: str | None = None
    diff: RawSnapshotDiff
    snapshot_digest: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def snapshot_is_self_consistent(self) -> "RawSourceSnapshot":
        if self.row_count != len(self.rows):
            raise ValueError("Raw Snapshotのrow countがpayloadと一致しません")
        expected = semantic_digest(
            {
                "connector_id": self.connector_id,
                "connector_configuration_digest": self.connector_configuration_digest,
                "source_locator": self.source_locator,
                "selection_digest": self.selection_digest,
                "object_version": self.object_version,
                "trigger_kind": self.trigger_kind,
                "content_sha256": self.content_sha256,
                "rows": self.rows,
            }
        )
        if expected != self.snapshot_digest:
            raise ValueError("Raw Snapshot digestが内容と一致しません")
        return self


class FetchAttempt(ContractModel):
    schema_version: Literal["fetch-attempt/v1"] = "fetch-attempt/v1"
    id: Annotated[str, Field(min_length=1)]
    connector_id: Annotated[str, Field(min_length=1)]
    trigger_kind: Literal["manual", "scheduled"]
    object_version: str
    status: Literal["succeeded", "failed"]
    error_code: Literal[
        "invalid_object",
        "scheduled_trigger_not_allowed",
        "connector_not_found",
    ] | None = None
    error_message: str = ""
    retry_of: str | None = None
    snapshot_id: str | None = None
    reused_existing_snapshot: bool = False
    started_at: datetime
    finished_at: datetime


class SourceFetchResult(ContractModel):
    snapshot: RawSourceSnapshot
    attempt: FetchAttempt


class TrimStringsStep(ContractModel):
    kind: Literal["trim_strings_v1"]
    fields: Annotated[tuple[str, ...], Field(min_length=1)]


class CoerceNumberStep(ContractModel):
    kind: Literal["coerce_number_v1"]
    fields: Annotated[tuple[str, ...], Field(min_length=1)]


class RequiredFieldsStep(ContractModel):
    kind: Literal["required_fields_v1"]
    fields: Annotated[tuple[str, ...], Field(min_length=1)]


class TargetEligibilityStep(ContractModel):
    kind: Literal["target_eligibility_v1"]
    fields: Annotated[tuple[str, ...], Field(min_length=1)]


class FilterEqualStep(ContractModel):
    kind: Literal["filter_equal_v1"]
    field: Annotated[str, Field(min_length=1)]
    value: str | float | int | bool


class SumLimitStep(ContractModel):
    kind: Literal["sum_limit_v1"]
    fields: Annotated[tuple[str, ...], Field(min_length=1)]
    maximum: float
    tolerance: Annotated[float, Field(ge=0)] = 0
    on_violation: Literal["warning", "quarantined"] = "warning"


CurationStep = Annotated[
    TrimStringsStep
    | CoerceNumberStep
    | RequiredFieldsStep
    | TargetEligibilityStep
    | FilterEqualStep
    | SumLimitStep,
    Field(discriminator="kind"),
]


class CurationRecipeCreateInput(ContractModel):
    schema_version: Literal["curation-recipe/v1"] = "curation-recipe/v1"
    recipe_id: Annotated[str, Field(min_length=1, max_length=120)]
    version: Annotated[int, Field(ge=1)]
    name: Annotated[str, Field(min_length=1, max_length=160)]
    steps: Annotated[tuple[CurationStep, ...], Field(min_length=1)]

    @property
    def calculated_recipe_digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))


class CurationRecipe(CurationRecipeCreateInput):
    id: Annotated[str, Field(min_length=1)]
    recipe_digest: Annotated[str, Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def digest_matches_recipe(self) -> "CurationRecipe":
        payload = CurationRecipeCreateInput.model_validate(
            self.model_dump(
                mode="json",
                exclude={"id", "recipe_digest", "created_at"},
            )
        )
        if payload.calculated_recipe_digest != self.recipe_digest:
            raise ValueError("Curation Recipe digestが内容と一致しません")
        return self


class CuratedRow(ContractModel):
    row_key: Annotated[str, Field(min_length=1)]
    raw_row_index: Annotated[int, Field(ge=0)]
    canonical_record: JsonRecord
    status: Literal["accepted", "warning", "quarantined", "blocked"]
    reason_codes: tuple[
        Literal[
            "missing_required",
            "invalid_number",
            "filter_mismatch",
            "sum_limit_exceeded",
            "missing_target",
            "duplicate_row_key",
        ],
        ...,
    ] = ()
    target_eligible: bool


class QualitySummary(ContractModel):
    accepted: Annotated[int, Field(ge=0)] = 0
    warning: Annotated[int, Field(ge=0)] = 0
    quarantined: Annotated[int, Field(ge=0)] = 0
    blocked: Annotated[int, Field(ge=0)] = 0
    target_ineligible: Annotated[int, Field(ge=0)] = 0


class QualityDelta(ContractModel):
    comparable: bool
    accepted_delta: int = 0
    warning_delta: int = 0
    quarantined_delta: int = 0
    blocked_delta: int = 0
    target_ineligible_delta: int = 0


class CurationRun(ContractModel):
    schema_version: Literal["curation-run/v1"] = "curation-run/v1"
    id: Annotated[str, Field(min_length=1)]
    raw_snapshot_id: Annotated[str, Field(min_length=1)]
    raw_snapshot_digest: Annotated[str, Field(min_length=1)]
    recipe_id: Annotated[str, Field(min_length=1)]
    recipe_digest: Annotated[str, Field(min_length=1)]
    profile_revision_id: Annotated[str, Field(min_length=1)]
    profile_digest: Annotated[str, Field(min_length=1)]
    rows: tuple[CuratedRow, ...]
    quality: QualitySummary
    quality_delta: QualityDelta
    curation_digest: Annotated[str, Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def run_is_self_consistent(self) -> "CurationRun":
        expected = semantic_digest(
            {
                "raw_snapshot_digest": self.raw_snapshot_digest,
                "recipe_digest": self.recipe_digest,
                "profile_revision_id": self.profile_revision_id,
                "profile_digest": self.profile_digest,
                "rows": [row.model_dump(mode="json") for row in self.rows],
            }
        )
        if expected != self.curation_digest:
            raise ValueError("Curation Run digestが内容と一致しません")
        status_counts = {
            status: sum(row.status == status for row in self.rows)
            for status in ("accepted", "warning", "quarantined", "blocked")
        }
        if (
            self.quality.accepted != status_counts["accepted"]
            or self.quality.warning != status_counts["warning"]
            or self.quality.quarantined != status_counts["quarantined"]
            or self.quality.blocked != status_counts["blocked"]
            or self.quality.target_ineligible
            != sum(not row.target_eligible for row in self.rows)
        ):
            raise ValueError("Curation quality summaryがrowと一致しません")
        return self


class CurationRunCreateInput(ContractModel):
    recipe_resource_id: Annotated[str, Field(min_length=1)]
    profile_revision_id: Annotated[str, Field(min_length=1)]
    profile_digest: Annotated[str, Field(min_length=1)]


class ApprovalOverride(ContractModel):
    row_key: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class DatasetApprovalInput(ContractModel):
    actor: Annotated[str, Field(min_length=1, max_length=120)]
    reason: Annotated[str, Field(max_length=500)] = ""
    overrides: tuple[ApprovalOverride, ...] = ()

    @model_validator(mode="after")
    def override_requires_reason(self) -> "DatasetApprovalInput":
        if self.overrides and not self.reason.strip():
            raise ValueError("override時は全体の承認理由も必要です")
        return self


class CanonicalDatasetRevision(ContractModel):
    schema_version: Literal["approved-canonical-dataset/v1"] = (
        "approved-canonical-dataset/v1"
    )
    id: Annotated[str, Field(min_length=1)]
    curation_run_id: Annotated[str, Field(min_length=1)]
    curation_digest: Annotated[str, Field(min_length=1)]
    raw_snapshot_digest: Annotated[str, Field(min_length=1)]
    recipe_digest: Annotated[str, Field(min_length=1)]
    profile_revision_id: Annotated[str, Field(min_length=1)]
    profile_digest: Annotated[str, Field(min_length=1)]
    approved_row_keys: tuple[str, ...]
    excluded_row_keys: tuple[str, ...]
    overrides: tuple[ApprovalOverride, ...]
    actor: str
    reason: str
    dataset_digest: Annotated[str, Field(min_length=1)]
    approved_at: datetime

    @model_validator(mode="after")
    def approval_is_self_consistent(self) -> "CanonicalDatasetRevision":
        if set(self.approved_row_keys) & set(self.excluded_row_keys):
            raise ValueError("承認rowと除外rowは重複できません")
        expected = semantic_digest(
            {
                "curation_digest": self.curation_digest,
                "approved_row_keys": self.approved_row_keys,
                "excluded_row_keys": self.excluded_row_keys,
                "overrides": [
                    item.model_dump(mode="json") for item in self.overrides
                ],
                "actor": self.actor,
                "reason": self.reason,
            }
        )
        if expected != self.dataset_digest:
            raise ValueError("Canonical Dataset digestが内容と一致しません")
        return self


class TrainingRowExclusion(ContractModel):
    kind: Literal["field_equals_any_v1"]
    field: Annotated[str, Field(min_length=1)]
    values: Annotated[
        tuple[str | float | int | bool, ...],
        Field(min_length=1),
    ]


class TrainingSnapshotSelectionPolicy(ContractModel):
    schema_version: Literal["training-snapshot-selection/v1"] = (
        "training-snapshot-selection/v1"
    )
    policy_id: Annotated[str, Field(min_length=1, max_length=120)]
    revision: Annotated[int, Field(ge=1)] = 1
    exclusions: Annotated[
        tuple[TrainingRowExclusion, ...],
        Field(min_length=1),
    ]

    @property
    def digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))


class TrainingSnapshotCreateInput(ContractModel):
    actor: Annotated[str, Field(min_length=1, max_length=120)]
    purpose: Annotated[str, Field(min_length=1, max_length=300)]
    selection_policy: TrainingSnapshotSelectionPolicy | None = None


class ApprovedTrainingSnapshot(ContractModel):
    schema_version: Literal["approved-training-snapshot/v1"] = (
        "approved-training-snapshot/v1"
    )
    id: Annotated[str, Field(min_length=1)]
    canonical_dataset_revision_id: Annotated[str, Field(min_length=1)]
    dataset_digest: Annotated[str, Field(min_length=1)]
    included_row_keys: tuple[str, ...]
    row_count: Annotated[int, Field(ge=1)]
    actor: str
    purpose: str
    selection_policy: TrainingSnapshotSelectionPolicy | None = None
    selection_policy_digest: str | None = None
    snapshot_digest: Annotated[str, Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def training_snapshot_is_self_consistent(
        self,
    ) -> "ApprovedTrainingSnapshot":
        if self.row_count != len(self.included_row_keys):
            raise ValueError("Training Snapshotのrow countが一致しません")
        if bool(self.selection_policy) != bool(self.selection_policy_digest):
            raise ValueError(
                "Training Snapshot selection policy identity is incomplete"
            )
        if (
            self.selection_policy is not None
            and self.selection_policy.digest != self.selection_policy_digest
        ):
            raise ValueError(
                "Training Snapshot selection policy digest does not match"
            )
        payload = {
            "dataset_digest": self.dataset_digest,
            "included_row_keys": self.included_row_keys,
            "actor": self.actor,
            "purpose": self.purpose,
        }
        if self.selection_policy is not None:
            payload["selection_policy"] = self.selection_policy.model_dump(
                mode="json"
            )
            payload["selection_policy_digest"] = (
                self.selection_policy_digest
            )
        expected = semantic_digest(payload)
        if expected != self.snapshot_digest:
            raise ValueError("Training Snapshot digestが内容と一致しません")
        return self


class ConnectorLifecycleDetail(ContractModel):
    connector: SourceConnector
    attempts: tuple[FetchAttempt, ...] = ()
    raw_snapshots: tuple[RawSourceSnapshot, ...] = ()
    curation_runs: tuple[CurationRun, ...] = ()
    canonical_revisions: tuple[CanonicalDatasetRevision, ...] = ()
    training_snapshots: tuple[ApprovedTrainingSnapshot, ...] = ()


class DataLifecycleCatalog(ContractModel):
    connectors: tuple[SourceConnector, ...] = ()
    recipes: tuple[CurationRecipe, ...] = ()
