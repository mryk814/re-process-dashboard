from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from material_workbench.persistence.data_lifecycle_payload_storage import (
    LifecyclePayloadUnavailableError,
)
from material_workbench.persistence.data_lifecycle_repository import (
    DataLifecycleRepository,
)
from material_workbench.persistence.data_lifecycle_summaries import (
    summarize_training,
)
from material_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "training-snapshot-selection-audit-v1"
MIGRATION_CHECKSUM = "persist-policy-and-total-counts-v2"


def migrate_training_snapshot_selection_audit(database: str | Path) -> None:
    connection = connect_sqlite(database)
    try:
        existing = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if existing is not None:
            if existing["checksum"] != MIGRATION_CHECKSUM:
                raise RuntimeError(
                    "Training Snapshot selection audit migration "
                    "checksum mismatch"
                )
            return
        snapshot_ids = tuple(
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM approved_training_snapshots ORDER BY id"
            )
        )
    finally:
        connection.close()

    repository = DataLifecycleRepository(database)
    projections: list[tuple[str, str]] = []
    for snapshot_id in snapshot_ids:
        try:
            snapshot = repository.get_training_snapshot(snapshot_id)
            revision = repository.get_canonical_revision(
                snapshot.canonical_dataset_revision_id
            )
            run = repository.get_curation_run(revision.curation_run_id)
        except LifecyclePayloadUnavailableError:
            # Keep the immutable Snapshot readable. Its owning connector will
            # report the unavailable Curation payload when audit detail is
            # requested instead of blocking unrelated workspaces at startup.
            continue
        projections.append(
            (
                summarize_training(
                    snapshot,
                    revision=revision,
                    run=run,
                ).model_dump_json(),
                snapshot_id,
            )
        )

    connection = connect_sqlite(database)
    try:
        for summary_payload, snapshot_id in projections:
            connection.execute(
                "UPDATE approved_training_snapshots SET summary_payload=? "
                "WHERE id=?",
                (summary_payload, snapshot_id),
            )
        connection.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) "
            "VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now(UTC).isoformat()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
