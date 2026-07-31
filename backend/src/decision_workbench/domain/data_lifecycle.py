"""Pure data-lifecycle operations; no credential or activation side effects."""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from decision_workbench.contracts.data_lifecycle_contracts import (
    ApprovedTrainingSnapshot,
    CanonicalDatasetRevision,
    CurationRecipe,
    CurationRun,
    CuratedRow,
    DatasetApprovalInput,
    ObjectSelection,
    QualityDelta,
    QualitySummary,
    RawSnapshotDiff,
    RawSourceSnapshot,
    SourceConnector,
    TrainingSplitAssignment,
    TrainingSnapshotCreateInput,
    TrainingTargetCohort,
)
from decision_workbench.execution.inference_work_graph import semantic_digest


class SourceObjectError(ValueError):
    pass


class LifecycleConflictError(ValueError):
    pass


def parse_source_object(content: str, selection: ObjectSelection) -> tuple[dict[str, Any], ...]:
    try:
        if selection.format == "json_array":
            parsed = json.loads(content)
            if not isinstance(parsed, list):
                raise SourceObjectError("JSON objectはrecord配列である必要があります")
            source_rows = parsed
        else:
            source_rows = [
                json.loads(line)
                for line in content.splitlines()
                if line.strip()
            ]
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise SourceObjectError("objectをJSON recordとして解釈できません") from exc
    if not all(isinstance(row, dict) for row in source_rows):
        raise SourceObjectError("各recordはJSON objectである必要があります")
    fields = set(selection.included_fields)
    return tuple(
        {
            key: value
            for key, value in row.items()
            if not fields or key in fields
        }
        for row in source_rows
    )


def _index_by_key(
    rows: tuple[dict[str, Any], ...],
    primary_key: str,
) -> dict[str, dict[str, Any]] | None:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(primary_key)
        if value is None or str(value) in result:
            return None
        result[str(value)] = row
    return result


def compare_snapshots(
    previous: RawSourceSnapshot | None,
    rows: tuple[dict[str, Any], ...],
    selection: ObjectSelection,
) -> RawSnapshotDiff:
    if previous is None:
        return RawSnapshotDiff(
            comparable=False,
            reason="比較する前回Snapshotがありません",
            added_rows=len(rows),
        )
    if selection.primary_key is None:
        return RawSnapshotDiff(
            comparable=False,
            reason="upstream row keyが定義されていません",
        )
    before = _index_by_key(previous.rows, selection.primary_key)
    after = _index_by_key(rows, selection.primary_key)
    if before is None or after is None:
        return RawSnapshotDiff(
            comparable=False,
            reason="row keyが欠損または重複しているため比較できません",
        )
    before_keys = set(before)
    after_keys = set(after)
    shared = before_keys & after_keys
    changed = sum(
        semantic_digest(before[key]) != semantic_digest(after[key])
        for key in shared
    )
    return RawSnapshotDiff(
        comparable=True,
        added_rows=len(after_keys - before_keys),
        changed_rows=changed,
        removed_rows=len(before_keys - after_keys),
        unchanged_rows=len(shared) - changed,
    )


def build_raw_snapshot(
    connector: SourceConnector,
    content: str,
    *,
    object_version: str,
    source_byte_count: int | None = None,
    trigger_kind: str,
    previous: RawSourceSnapshot | None,
    captured_at: datetime | None = None,
) -> RawSourceSnapshot:
    rows = parse_source_object(content, connector.selection)
    content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    captured = captured_at or datetime.now(UTC)
    payload = {
        "connector_id": connector.id,
        "connector_configuration_digest": connector.configuration_digest,
        "source_locator": connector.source_locator,
        "selection_digest": connector.selection.digest,
        "object_version": object_version,
        "trigger_kind": trigger_kind,
        "content_sha256": content_sha256,
        "source_byte_count": source_byte_count or len(content.encode("utf-8")),
        "rows": rows,
    }
    digest = semantic_digest(payload)
    return RawSourceSnapshot(
        id=f"raw-snapshot-{digest.removeprefix('sha256:')[:24]}",
        connector_id=connector.id,
        connector_configuration_digest=connector.configuration_digest,
        source_locator=connector.source_locator,
        selection_digest=connector.selection.digest,
        object_version=object_version,
        trigger_kind=trigger_kind,  # type: ignore[arg-type]
        captured_at=captured,
        content_sha256=content_sha256,
        source_byte_count=source_byte_count or len(content.encode("utf-8")),
        row_count=len(rows),
        rows=rows,
        previous_snapshot_id=previous.id if previous else None,
        diff=compare_snapshots(previous, rows, connector.selection),
        snapshot_digest=digest,
    )


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _status(reasons: list[tuple[str, str]]) -> str:
    severities = {severity for severity, _ in reasons}
    if "blocked" in severities:
        return "blocked"
    if "quarantined" in severities:
        return "quarantined"
    if "warning" in severities:
        return "warning"
    return "accepted"


def _quality(rows: tuple[CuratedRow, ...]) -> QualitySummary:
    counts = Counter(row.status for row in rows)
    return QualitySummary(
        accepted=counts["accepted"],
        warning=counts["warning"],
        quarantined=counts["quarantined"],
        blocked=counts["blocked"],
        target_ineligible=sum(not row.target_eligible for row in rows),
    )


def _quality_delta(
    current: QualitySummary,
    previous: CurationRun | None,
) -> QualityDelta:
    if previous is None:
        return QualityDelta(comparable=False)
    return QualityDelta(
        comparable=True,
        accepted_delta=current.accepted - previous.quality.accepted,
        warning_delta=current.warning - previous.quality.warning,
        quarantined_delta=current.quarantined - previous.quality.quarantined,
        blocked_delta=current.blocked - previous.quality.blocked,
        target_ineligible_delta=(
            current.target_ineligible - previous.quality.target_ineligible
        ),
    )


def curate_snapshot(
    snapshot: RawSourceSnapshot,
    recipe: CurationRecipe,
    *,
    profile_revision_id: str,
    profile_digest: str,
    primary_key: str | None,
    previous: CurationRun | None = None,
    created_at: datetime | None = None,
) -> CurationRun:
    key_counts = Counter(
        str(row.get(primary_key))
        for row in snapshot.rows
        if primary_key is not None and not _missing(row.get(primary_key))
    )
    curated: list[CuratedRow] = []
    for index, source in enumerate(snapshot.rows):
        record = dict(source)
        reasons: list[tuple[str, str]] = []
        target_eligible = True
        for step in recipe.steps:
            if step.kind == "trim_strings_v1":
                for field in step.fields:
                    if isinstance(record.get(field), str):
                        record[field] = record[field].strip()
            elif step.kind == "coerce_number_v1":
                for field in step.fields:
                    value = record.get(field)
                    if _missing(value):
                        continue
                    try:
                        numeric = float(value)
                        if not math.isfinite(numeric):
                            raise ValueError
                        record[field] = numeric
                    except (TypeError, ValueError):
                        reasons.append(("quarantined", "invalid_number"))
            elif step.kind == "required_fields_v1":
                if any(_missing(record.get(field)) for field in step.fields):
                    reasons.append(("quarantined", "missing_required"))
            elif step.kind == "target_eligibility_v1":
                if any(_missing(record.get(field)) for field in step.fields):
                    target_eligible = False
                    reasons.append(("warning", "missing_target"))
            elif step.kind == "filter_equal_v1":
                if record.get(step.field) != step.value:
                    reasons.append(("quarantined", "filter_mismatch"))
            elif step.kind == "sum_limit_v1":
                values = [record.get(field) for field in step.fields]
                if all(isinstance(value, (int, float)) for value in values):
                    if sum(float(value) for value in values) > (
                        step.maximum + step.tolerance
                    ):
                        reasons.append((step.on_violation, "sum_limit_exceeded"))
        key = (
            str(record.get(primary_key))
            if primary_key and not _missing(record.get(primary_key))
            else f"row-{index + 1}"
        )
        if primary_key and key_counts[key] > 1:
            reasons.append(("blocked", "duplicate_row_key"))
        status = _status(reasons)
        curated.append(
            CuratedRow(
                row_key=key,
                raw_row_index=index,
                canonical_record={
                    field: record[field] for field in sorted(record)
                },
                status=status,  # type: ignore[arg-type]
                reason_codes=tuple(dict.fromkeys(reason for _, reason in reasons)),
                target_eligible=target_eligible and status != "blocked",
            )
        )
    rows = tuple(curated)
    quality = _quality(rows)
    digest_payload = {
        "raw_snapshot_digest": snapshot.snapshot_digest,
        "recipe_digest": recipe.recipe_digest,
        "profile_revision_id": profile_revision_id,
        "profile_digest": profile_digest,
        "rows": [row.model_dump(mode="json") for row in rows],
    }
    digest = semantic_digest(digest_payload)
    return CurationRun(
        id=f"curation-run-{digest.removeprefix('sha256:')[:24]}",
        raw_snapshot_id=snapshot.id,
        raw_snapshot_digest=snapshot.snapshot_digest,
        recipe_id=recipe.id,
        recipe_digest=recipe.recipe_digest,
        profile_revision_id=profile_revision_id,
        profile_digest=profile_digest,
        rows=rows,
        quality=quality,
        quality_delta=_quality_delta(quality, previous),
        curation_digest=digest,
        created_at=created_at or datetime.now(UTC),
    )


def approve_curation_run(
    run: CurationRun,
    approval: DatasetApprovalInput,
    *,
    approved_at: datetime | None = None,
) -> CanonicalDatasetRevision:
    rows_by_key = {row.row_key: row for row in run.rows}
    if len(rows_by_key) != len(run.rows):
        raise LifecycleConflictError("重複row keyを含むCuration Runは承認できません")
    override_keys = {item.row_key for item in approval.overrides}
    missing = sorted(override_keys - set(rows_by_key))
    if missing:
        raise LifecycleConflictError(
            "override対象がCuration Runにありません: " + ", ".join(missing)
        )
    blocked = sorted(
        key
        for key in override_keys
        if rows_by_key[key].status == "blocked"
    )
    if blocked:
        raise LifecycleConflictError(
            "blocked rowはoverrideできません: " + ", ".join(blocked)
        )
    approved = tuple(
        row.row_key
        for row in run.rows
        if row.status in {"accepted", "warning"} or row.row_key in override_keys
    )
    excluded = tuple(row.row_key for row in run.rows if row.row_key not in approved)
    payload = {
        "curation_digest": run.curation_digest,
        "approved_row_keys": approved,
        "excluded_row_keys": excluded,
        "overrides": [item.model_dump(mode="json") for item in approval.overrides],
        "actor": approval.actor,
        "reason": approval.reason,
    }
    digest = semantic_digest(payload)
    return CanonicalDatasetRevision(
        id=f"canonical-dataset-{digest.removeprefix('sha256:')[:24]}",
        curation_run_id=run.id,
        curation_digest=run.curation_digest,
        raw_snapshot_digest=run.raw_snapshot_digest,
        recipe_digest=run.recipe_digest,
        profile_revision_id=run.profile_revision_id,
        profile_digest=run.profile_digest,
        approved_row_keys=approved,
        excluded_row_keys=excluded,
        overrides=approval.overrides,
        actor=approval.actor,
        reason=approval.reason,
        dataset_digest=digest,
        approved_at=approved_at or datetime.now(UTC),
    )


def build_training_snapshot(
    dataset: CanonicalDatasetRevision,
    run: CurationRun,
    request: TrainingSnapshotCreateInput,
    *,
    created_at: datetime | None = None,
) -> ApprovedTrainingSnapshot:
    approved = set(dataset.approved_row_keys)
    policy = request.selection_policy

    def selected(row: CuratedRow) -> bool:
        if policy is None:
            return True
        for exclusion in policy.exclusions:
            if (
                exclusion.kind == "field_equals_any_v1"
                and row.canonical_record.get(exclusion.field)
                in exclusion.values
            ):
                return False
        return True

    selected_rows = tuple(
        row
        for row in run.rows
        if row.row_key in approved and selected(row)
    )
    target_cohorts: list[TrainingTargetCohort] = []
    for target in request.targets:
        cohort_rows = tuple(
            row
            for row in selected_rows
            if (
                (value := row.canonical_record.get(target.field)) is not None
                and value != ""
                and not (
                    isinstance(value, float)
                    and not math.isfinite(value)
                )
            )
        )
        if not cohort_rows:
            raise LifecycleConflictError(
                f"{target.target_key}: 学習対象にできる承認済みrowがありません"
            )
        groups: set[str] = set()
        for row in cohort_rows:
            raw_group = row.canonical_record.get(request.split.group_field)
            group = "" if raw_group is None else str(raw_group).strip()
            if not group:
                raise LifecycleConflictError(
                    f"{target.target_key}: split groupが空のrowがあります: "
                    f"{row.row_key}"
                )
            groups.add(group)
        ordered_groups = sorted(groups)
        if len(ordered_groups) < request.split.folds:
            raise LifecycleConflictError(
                f"{target.target_key}: {request.split.folds} foldsには"
                f"{request.split.folds}個以上のsplit groupが必要です"
            )
        assignments = tuple(
            TrainingSplitAssignment(
                group_key=group,
                fold=index % request.split.folds,
            )
            for index, group in enumerate(ordered_groups)
        )
        row_keys = tuple(row.row_key for row in cohort_rows)
        cohort_digest = semantic_digest(
            {
                "target_key": target.target_key,
                "target_field": target.field,
                "row_keys": row_keys,
            }
        )
        split_digest = semantic_digest(
            {
                "cohort_digest": cohort_digest,
                "split": request.split.model_dump(mode="json"),
                "split_assignments": [
                    item.model_dump(mode="json")
                    for item in assignments
                ],
            }
        )
        target_cohorts.append(
            TrainingTargetCohort(
                target_key=target.target_key,
                target_field=target.field,
                row_keys=row_keys,
                cohort_digest=cohort_digest,
                split_assignments=assignments,
                split_digest=split_digest,
            )
        )
    included_set = {
        row_key
        for cohort in target_cohorts
        for row_key in cohort.row_keys
    }
    included = tuple(
        row.row_key for row in selected_rows if row.row_key in included_set
    )
    if not included:
        raise LifecycleConflictError("学習対象にできる承認済みrowがありません")
    payload = {
        "dataset_digest": dataset.dataset_digest,
        "included_row_keys": included,
        "actor": request.actor,
        "purpose": request.purpose,
        "target_cohorts": [
            item.model_dump(mode="json") for item in target_cohorts
        ],
        "split": request.split.model_dump(mode="json"),
    }
    if policy is not None:
        payload["selection_policy"] = policy.model_dump(mode="json")
        payload["selection_policy_digest"] = policy.digest
    digest = semantic_digest(payload)
    return ApprovedTrainingSnapshot(
        id=f"training-snapshot-{digest.removeprefix('sha256:')[:24]}",
        canonical_dataset_revision_id=dataset.id,
        dataset_digest=dataset.dataset_digest,
        included_row_keys=included,
        row_count=len(included),
        actor=request.actor,
        purpose=request.purpose,
        target_cohorts=tuple(target_cohorts),
        split=request.split,
        selection_policy=policy,
        selection_policy_digest=policy.digest if policy else None,
        snapshot_digest=digest,
        created_at=created_at or datetime.now(UTC),
    )
