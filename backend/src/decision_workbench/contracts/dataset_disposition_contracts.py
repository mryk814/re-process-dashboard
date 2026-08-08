"""Immutable, browser-safe Dataset availability semantics.

The disposition records what the canonical Dataset retained and which
operation inputs are available.  It deliberately contains counts and stable
reason identifiers only; source locators, source rows, and entity keys do not
cross this contract boundary.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from decision_workbench.execution.inference_work_graph import semantic_digest


DATASET_DISPOSITION_SCHEMA_VERSION = "dataset-disposition/v1"
DATASET_CANONICALIZATION_CONTRACT_DIGEST = semantic_digest(
    {"id": "workbook-canonicalizer/v1"}
)
DatasetDispositionStatus = Literal["recorded", "unknown_legacy"]
DispositionEligibility = Literal[
    "retained",
    "eligible",
    "excluded_without_required_series",
    "requires_user_supplied_series",
    "not_applicable",
    "unknown_legacy",
]


class DatasetOperationEligibility(BaseModel):
    """Allow-listed operation semantics for one Prediction Task."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    lineage: DispositionEligibility
    observation_browse: DispositionEligibility
    training: DispositionEligibility
    candidate_reference: DispositionEligibility
    similarity: DispositionEligibility
    prediction_input: DispositionEligibility


class DatasetTaskDisposition(BaseModel):
    """Counts and operation handling for one Task, without row identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_count: int = Field(ge=0)
    observation_count: int = Field(ge=0)
    usable_observation_count: int = Field(ge=0)
    heat_series_parent_count: int = Field(ge=0)
    unresolved_heat_series_parent_count: int = Field(ge=0)
    operation_eligibility: DatasetOperationEligibility
    reason_counts: dict[str, int] = Field(default_factory=dict)


class DatasetDisposition(BaseModel):
    """The immutable ``dataset-disposition/v1`` artifact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[DATASET_DISPOSITION_SCHEMA_VERSION] = (
        DATASET_DISPOSITION_SCHEMA_VERSION
    )
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_digest: str = Field(min_length=1)
    canonicalization_contract_digest: str = Field(min_length=1)
    canonical_dataset_digest: str = Field(min_length=1)
    task_dispositions: dict[str, DatasetTaskDisposition] = Field(default_factory=dict)


class DatasetDispositionTaskDiff(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    previous_entity_count: int = Field(ge=0)
    current_entity_count: int = Field(ge=0)
    previous_observation_count: int = Field(ge=0)
    current_observation_count: int = Field(ge=0)
    previous_unresolved_heat_series_parent_count: int = Field(ge=0)
    current_unresolved_heat_series_parent_count: int = Field(ge=0)
    previous_usable_observation_count: int = Field(ge=0)
    current_usable_observation_count: int = Field(ge=0)
    count_deltas: dict[str, int] = Field(default_factory=dict)


class DatasetDispositionDiff(BaseModel):
    """Bounded comparison between adjacent Dataset Revisions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    comparable: bool
    reason: Literal[
        "compared",
        "previous_disposition_unavailable",
        "previous_legacy_unknown",
        "profile_changed",
        "canonicalization_contract_changed",
    ]
    changed: bool = False
    previous_source_sha256: str | None = None
    current_source_sha256: str | None = None
    previous_profile_digest: str | None = None
    current_profile_digest: str | None = None
    source_changed: bool = False
    profile_changed: bool = False
    canonicalization_contract_changed: bool = False
    changed_task_ids: list[str] = Field(default_factory=list)
    added_task_ids: list[str] = Field(default_factory=list)
    removed_task_ids: list[str] = Field(default_factory=list)
    task_diffs: dict[str, DatasetDispositionTaskDiff] = Field(default_factory=dict)


class DatasetDispositionProjection(BaseModel):
    """Browser-safe projection used by preview, receipt, and Data Library."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: DatasetDispositionStatus
    digest: str | None = None
    schema_version: str | None = None
    task_dispositions: dict[str, DatasetTaskDisposition] = Field(default_factory=dict)
    previous_diff: DatasetDispositionDiff | None = None
    improvement_hints: list[str] = Field(default_factory=list)


def disposition_digest(disposition: DatasetDisposition) -> str:
    """Return the immutable digest for a disposition artifact."""

    return semantic_digest(disposition.model_dump(mode="json"))


def _task_operation_eligibility(
    *,
    requires_heat_series: bool,
    unresolved_count: int,
) -> DatasetOperationEligibility:
    if not requires_heat_series:
        operation = "eligible"
        prediction = "eligible"
    elif unresolved_count:
        operation = "excluded_without_required_series"
        prediction = "requires_user_supplied_series"
    else:
        operation = "eligible"
        prediction = "eligible"
    return DatasetOperationEligibility(
        lineage="retained",
        observation_browse="retained",
        training=operation,
        candidate_reference=operation,
        similarity=operation,
        prediction_input=prediction,
    )


def _canonical_dataset_digest(
    *,
    source_sha256: str,
    profile_digest: str,
    canonicalization_contract_digest: str,
    task_dispositions: Mapping[str, DatasetTaskDisposition],
) -> str:
    """Hash only stable, non-row identity needed to compare this artifact."""

    return semantic_digest(
        {
            "schema_version": DATASET_DISPOSITION_SCHEMA_VERSION,
            "source_sha256": source_sha256,
            "profile_digest": profile_digest,
            "canonicalization_contract_digest": canonicalization_contract_digest,
            "task_dispositions": {
                task_id: item.model_dump(mode="json")
                for task_id, item in sorted(task_dispositions.items())
            },
        }
    )


def build_dataset_disposition(
    canonical: Any,
    *,
    source_sha256: str,
    profile_digest: str,
    canonicalization_contract_digest: str,
) -> DatasetDisposition:
    """Build one disposition from the canonicalization result.

    ``canonical`` is intentionally consumed through its aggregate fields.  No
    source locator, source row, or entity key is copied into the artifact.
    """

    task_dispositions: dict[str, DatasetTaskDisposition] = {}
    for task_id, task in sorted(canonical.profile.tasks.items()):
        heat_mappings = [
            mapping
            for mapping in task.mappings
            if mapping.kind == "ordered_heat_series"
        ]
        parent_types = {
            mapping.parent_entity_type or "annealing"
            for mapping in heat_mappings
        }
        task_entities = (
            {
                identity
                for identity in canonical.entities
                if identity[0] in parent_types
            }
            if parent_types
            else set(canonical.entities)
        )
        task_series = {
            identity
            for identity in canonical.heat_series
            if not parent_types or identity[0] in parent_types
        }
        unresolved = task_entities - task_series if parent_types else set()
        observations = [
            item for item in canonical.observations if item.task_id == task_id
        ]
        usable_observations = (
            observations
            if not parent_types
            else [item for item in observations if item.parent_identity in task_series]
        )
        reasons: Counter[str] = Counter()
        for identity in unresolved:
            identity_reasons = getattr(canonical, "heat_series_reasons", {}).get(
                identity,
                (),
            )
            reasons.update(identity_reasons or ("missing_or_invalid_series",))
        task_dispositions[task_id] = DatasetTaskDisposition(
            entity_count=len(task_entities),
            observation_count=len(observations),
            usable_observation_count=len(usable_observations),
            heat_series_parent_count=len(task_series),
            unresolved_heat_series_parent_count=len(unresolved),
            operation_eligibility=_task_operation_eligibility(
                requires_heat_series=bool(parent_types),
                unresolved_count=len(unresolved),
            ),
            reason_counts=dict(sorted(reasons.items())),
        )
    return DatasetDisposition(
        source_sha256=source_sha256,
        profile_digest=profile_digest,
        canonicalization_contract_digest=canonicalization_contract_digest,
        canonical_dataset_digest=_canonical_dataset_digest(
            source_sha256=source_sha256,
            profile_digest=profile_digest,
            canonicalization_contract_digest=canonicalization_contract_digest,
            task_dispositions=task_dispositions,
        ),
        task_dispositions=task_dispositions,
    )


def build_count_disposition(
    *,
    source_sha256: str,
    profile_digest: str,
    canonicalization_contract_digest: str,
    task_ids: list[str] | tuple[str, ...],
    entities: int,
    observations_by_task: Mapping[str, int],
    usable_observations_by_task: Mapping[str, int] | None = None,
    heat_series_parents: int = 0,
    rejected_by_policy: Mapping[str, int] | None = None,
) -> DatasetDisposition:
    """Build a safe disposition for non-workbook Profile families.

    This helper does not infer heat-series availability.  Non-workbook loaders
    must provide the aggregate observation counts they actually know; a
    canonical workbook is required when parent/series retention needs to be
    represented.
    """

    policy_reasons = rejected_by_policy or {}
    usable_by_task = (
        observations_by_task
        if usable_observations_by_task is None
        else usable_observations_by_task
    )
    task_dispositions: dict[str, DatasetTaskDisposition] = {}
    for task_id in sorted(task_ids):
        observation_count = int(observations_by_task.get(task_id, 0))
        usable_observation_count = int(usable_by_task.get(task_id, 0))
        if not 0 <= usable_observation_count <= observation_count:
            raise ValueError(
                "usable_observations_by_task must be within observations_by_task"
            )
        task_dispositions[task_id] = DatasetTaskDisposition(
            entity_count=max(0, int(entities)),
            observation_count=max(0, observation_count),
            usable_observation_count=usable_observation_count,
            heat_series_parent_count=max(0, int(heat_series_parents)),
            unresolved_heat_series_parent_count=0,
            operation_eligibility=_task_operation_eligibility(
                requires_heat_series=False,
                unresolved_count=0,
            ),
            reason_counts={
                str(key): int(value)
                for key, value in sorted(policy_reasons.items())
                if int(value) > 0
            },
        )
    return DatasetDisposition(
        source_sha256=source_sha256,
        profile_digest=profile_digest,
        canonicalization_contract_digest=canonicalization_contract_digest,
        canonical_dataset_digest=_canonical_dataset_digest(
            source_sha256=source_sha256,
            profile_digest=profile_digest,
            canonicalization_contract_digest=canonicalization_contract_digest,
            task_dispositions=task_dispositions,
        ),
        task_dispositions=task_dispositions,
    )


def compare_dispositions(
    previous: DatasetDisposition | None,
    current: DatasetDisposition,
    *,
    previous_status: DatasetDispositionStatus = "recorded",
) -> DatasetDispositionDiff:
    if previous is None:
        return DatasetDispositionDiff(
            comparable=False,
            reason=(
                "previous_legacy_unknown"
                if previous_status == "unknown_legacy"
                else "previous_disposition_unavailable"
            ),
            current_source_sha256=current.source_sha256,
            current_profile_digest=current.profile_digest,
        )
    source_changed = previous.source_sha256 != current.source_sha256
    profile_changed = previous.profile_digest != current.profile_digest
    contract_changed = (
        previous.canonicalization_contract_digest
        != current.canonicalization_contract_digest
    )
    comparable = not profile_changed and not contract_changed
    reason: Literal[
        "compared",
        "previous_disposition_unavailable",
        "previous_legacy_unknown",
        "profile_changed",
        "canonicalization_contract_changed",
    ] = (
        "profile_changed"
        if profile_changed
        else "canonicalization_contract_changed"
        if contract_changed
        else "compared"
    )
    task_diffs: dict[str, DatasetDispositionTaskDiff] = {}
    previous_task_ids = set(previous.task_dispositions)
    current_task_ids = set(current.task_dispositions)
    common_task_ids = sorted(previous_task_ids & current_task_ids)
    added_task_ids = sorted(current_task_ids - previous_task_ids)
    removed_task_ids = sorted(previous_task_ids - current_task_ids)
    changed_task_ids: set[str] = set(added_task_ids) | set(removed_task_ids)
    count_fields = (
        "entity_count",
        "observation_count",
        "usable_observation_count",
        "heat_series_parent_count",
        "unresolved_heat_series_parent_count",
    )
    for task_id in common_task_ids:
        before = previous.task_dispositions.get(task_id)
        after = current.task_dispositions.get(task_id)
        assert before is not None and after is not None
        count_deltas = {
            field: getattr(after, field) - getattr(before, field)
            for field in count_fields
        }
        diff = DatasetDispositionTaskDiff(
            previous_entity_count=before.entity_count,
            current_entity_count=after.entity_count,
            previous_observation_count=before.observation_count,
            current_observation_count=after.observation_count,
            previous_unresolved_heat_series_parent_count=(
                before.unresolved_heat_series_parent_count
            ),
            current_unresolved_heat_series_parent_count=(
                after.unresolved_heat_series_parent_count
            ),
            previous_usable_observation_count=before.usable_observation_count,
            current_usable_observation_count=after.usable_observation_count,
            count_deltas=count_deltas,
        )
        if any(count_deltas.values()):
            task_diffs[task_id] = diff
            changed_task_ids.add(task_id)
        else:
            # Keep the common-task zero delta available when the identities
            # changed, while a stable revision remains compact.
            if source_changed or profile_changed or contract_changed:
                task_diffs[task_id] = diff
    return DatasetDispositionDiff(
        comparable=comparable,
        reason=reason,
        changed=bool(
            source_changed
            or profile_changed
            or contract_changed
            or changed_task_ids
        ),
        previous_source_sha256=previous.source_sha256,
        current_source_sha256=current.source_sha256,
        previous_profile_digest=previous.profile_digest,
        current_profile_digest=current.profile_digest,
        source_changed=source_changed,
        profile_changed=profile_changed,
        canonicalization_contract_changed=contract_changed,
        changed_task_ids=sorted(changed_task_ids),
        added_task_ids=added_task_ids,
        removed_task_ids=removed_task_ids,
        task_diffs=task_diffs,
    )


def disposition_projection(
    *,
    status: DatasetDispositionStatus,
    digest: str | None,
    disposition: DatasetDisposition | None,
    previous_diff: DatasetDispositionDiff | None = None,
) -> DatasetDispositionProjection:
    if disposition is None or status == "unknown_legacy":
        return DatasetDispositionProjection(
            status="unknown_legacy",
            previous_diff=previous_diff,
        )
    unresolved = sum(
        item.unresolved_heat_series_parent_count
        for item in disposition.task_dispositions.values()
    )
    hints = (
        [
            "ヒート系列の列対応付けを見直す",
            "測定点フォールバックを設定する",
            "元Sourceを修正する",
        ]
        if unresolved
        else []
    )
    return DatasetDispositionProjection(
        status="recorded",
        digest=digest or disposition_digest(disposition),
        schema_version=disposition.schema_version,
        task_dispositions=disposition.task_dispositions,
        previous_diff=previous_diff,
        improvement_hints=hints,
    )
