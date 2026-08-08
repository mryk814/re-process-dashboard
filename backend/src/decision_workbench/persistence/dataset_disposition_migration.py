"""Add immutable Dataset disposition columns without reconstructing legacy data."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "dataset-disposition-v1"
MIGRATION_CHECKSUM = "immutable-dataset-disposition-v1"
EXPECTED_COLUMNS = {
    "disposition_digest",
    "disposition_json",
    "disposition_status",
}


class DatasetDispositionMigrationError(RuntimeError):
    """The Dataset disposition storage schema is inconsistent."""


def migrate_dataset_disposition_storage(database: str | Path) -> None:
    """Add columns and mark old Dataset rows ``unknown_legacy``.

    The migration never calls a source importer.  Existing rows therefore
    remain explicitly unknown until a future Dataset Revision is registered.
    """

    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='dataset_revisions'"
        ).fetchone()
        if table is None:
            raise DatasetDispositionMigrationError(
                "dataset disposition migration requires dataset_revisions"
            )
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(dataset_revisions)")
        }
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise DatasetDispositionMigrationError(
                    "dataset disposition migration checksum does not match"
                )
            if EXPECTED_COLUMNS - columns:
                raise DatasetDispositionMigrationError(
                    "dataset disposition columns are missing"
                )
            conn.commit()
            return
        additions = (
            ("disposition_digest", "TEXT"),
            ("disposition_json", "TEXT"),
            ("disposition_status", "TEXT NOT NULL DEFAULT 'unknown_legacy'"),
        )
        for name, definition in additions:
            if name not in columns:
                conn.execute(
                    f'ALTER TABLE dataset_revisions ADD COLUMN "{name}" {definition}'
                )
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now(UTC).isoformat()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
