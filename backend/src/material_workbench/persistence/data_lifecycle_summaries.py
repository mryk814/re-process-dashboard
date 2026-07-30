from __future__ import annotations

from material_workbench.contracts.data_lifecycle_contracts import (
    ApprovedTrainingSnapshot,
    ApprovedTrainingSnapshotSummary,
    CanonicalDatasetRevision,
    CanonicalDatasetRevisionSummary,
    CurationRun,
    CurationRunSummary,
    RawSourceSnapshot,
    RawSourceSnapshotSummary,
    TrainingSnapshotExclusionReasonSummary,
    TrainingTargetCohortSummary,
)


def summarize_raw(snapshot: RawSourceSnapshot) -> RawSourceSnapshotSummary:
    return RawSourceSnapshotSummary.model_validate(
        snapshot.model_dump(
            mode="json",
            include=set(RawSourceSnapshotSummary.model_fields),
        )
    )


def summarize_curation(run: CurationRun) -> CurationRunSummary:
    return CurationRunSummary.model_validate(
        {
            **run.model_dump(
                mode="json",
                include=set(CurationRunSummary.model_fields) - {"row_count"},
            ),
            "row_count": len(run.rows),
        }
    )


def summarize_canonical(
    revision: CanonicalDatasetRevision,
) -> CanonicalDatasetRevisionSummary:
    return CanonicalDatasetRevisionSummary(
        id=revision.id,
        curation_run_id=revision.curation_run_id,
        curation_digest=revision.curation_digest,
        dataset_digest=revision.dataset_digest,
        actor=revision.actor,
        reason=revision.reason,
        approved_row_count=len(revision.approved_row_keys),
        excluded_row_count=len(revision.excluded_row_keys),
        override_count=len(revision.overrides),
        approved_at=revision.approved_at,
    )


def summarize_training(
    snapshot: ApprovedTrainingSnapshot,
    *,
    revision: CanonicalDatasetRevision | None = None,
    run: CurationRun | None = None,
) -> ApprovedTrainingSnapshotSummary:
    included_keys = set(snapshot.included_row_keys)
    approved_keys = (
        set(revision.approved_row_keys)
        if revision is not None
        else set(included_keys)
    )
    excluded_keys = approved_keys - included_keys
    rows_by_key = (
        {row.row_key: row for row in run.rows}
        if run is not None
        else {}
    )
    reasons: list[TrainingSnapshotExclusionReasonSummary] = []
    policy_matched: set[str] = set()
    if snapshot.selection_policy is not None:
        for index, exclusion in enumerate(snapshot.selection_policy.exclusions):
            matching = {
                row_key
                for row_key in excluded_keys
                if (
                    (row := rows_by_key.get(row_key)) is not None
                    and row.canonical_record.get(exclusion.field)
                    in exclusion.values
                )
            }
            policy_matched.update(matching)
            values = ", ".join(str(value) for value in exclusion.values)
            reasons.append(
                TrainingSnapshotExclusionReasonSummary(
                    code=f"policy:{index}:{exclusion.field}",
                    label=f"{exclusion.field} が {values}",
                    count=len(matching),
                )
            )
    policy_excluded_keys = approved_keys & policy_matched
    selected_by_policy_keys = approved_keys - policy_excluded_keys
    unexplained_keys = excluded_keys - policy_excluded_keys
    if unexplained_keys:
        if snapshot.schema_version == "approved-training-snapshot/v1":
            reasons.append(
                TrainingSnapshotExclusionReasonSummary(
                    code="legacy_reason_unrecorded",
                    label="旧契約のため理由未記録",
                    count=len(unexplained_keys),
                )
            )
        else:
            reasons.append(
                TrainingSnapshotExclusionReasonSummary(
                    code="missing_all_targets",
                    label="全ての目的変数が欠測",
                    count=len(unexplained_keys),
                )
            )
    return ApprovedTrainingSnapshotSummary(
        schema_version=snapshot.schema_version,
        id=snapshot.id,
        canonical_dataset_revision_id=snapshot.canonical_dataset_revision_id,
        dataset_digest=snapshot.dataset_digest,
        row_count=snapshot.row_count,
        actor=snapshot.actor,
        purpose=snapshot.purpose,
        target_cohorts=tuple(
            TrainingTargetCohortSummary(
                target_key=cohort.target_key,
                target_field=cohort.target_field,
                row_count=len(cohort.row_keys),
                excluded_row_count=len(
                    selected_by_policy_keys - set(cohort.row_keys)
                ),
                cohort_digest=cohort.cohort_digest,
                split_digest=cohort.split_digest,
                split_group_count=len(cohort.split_assignments),
            )
            for cohort in snapshot.target_cohorts
        ),
        split=snapshot.split,
        selection_policy=snapshot.selection_policy,
        selection_policy_digest=snapshot.selection_policy_digest,
        approved_row_count=len(approved_keys),
        included_row_count=len(included_keys),
        excluded_row_count=len(excluded_keys),
        policy_excluded_row_count=len(policy_excluded_keys),
        exclusion_reasons=tuple(reasons),
        snapshot_digest=snapshot.snapshot_digest,
        created_at=snapshot.created_at,
    )
