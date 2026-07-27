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
) -> ApprovedTrainingSnapshotSummary:
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
                cohort_digest=cohort.cohort_digest,
                split_digest=cohort.split_digest,
                split_group_count=len(cohort.split_assignments),
            )
            for cohort in snapshot.target_cohorts
        ),
        split=snapshot.split,
        snapshot_digest=snapshot.snapshot_digest,
        created_at=snapshot.created_at,
    )
