from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "candidate-revision-history-v1"
MIGRATION_CHECKSUM = "immutable-candidate-revisions-v1"


class CandidateRevisionMigrationError(RuntimeError):
    pass


def migrate_candidate_revisions(database: str | Path) -> None:
    """Add immutable candidate revisions and seed the current revision.

    The table intentionally has no foreign key to ``candidates``. A derived
    candidate must keep its source revision readable even when the source is
    later removed from the active candidate list.
    """

    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        applied = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if applied is not None:
            if applied[0] != MIGRATION_CHECKSUM:
                raise CandidateRevisionMigrationError(
                    "candidate revision migration checksum does not match"
                )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(candidate_revisions)")
            }
            expected = {
                "candidate_id",
                "project_id",
                "revision",
                "name",
                "payload",
                "archived_at",
                "created_at",
                "updated_at",
            }
            if expected - columns:
                raise CandidateRevisionMigrationError(
                    "candidate revision history table is incomplete"
                )
            missing = conn.execute(
                "SELECT candidates.id FROM candidates "
                "LEFT JOIN candidate_revisions "
                "ON candidate_revisions.candidate_id=candidates.id "
                "AND candidate_revisions.revision=candidates.revision "
                "WHERE candidate_revisions.candidate_id IS NULL LIMIT 1"
            ).fetchone()
            if missing is not None:
                raise CandidateRevisionMigrationError(
                    f"candidate {missing[0]} has no immutable current revision"
                )
            conn.commit()
            return

        exists = conn.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='candidate_revisions'"
        ).fetchone()
        if exists is not None:
            raise CandidateRevisionMigrationError(
                "candidate revision history exists without its migration marker"
            )
        conn.execute(
            "CREATE TABLE candidate_revisions ("
            "candidate_id TEXT NOT NULL,"
            "project_id TEXT NOT NULL,"
            "revision INTEGER NOT NULL CHECK(revision >= 1),"
            "name TEXT NOT NULL,"
            "payload TEXT NOT NULL,"
            "archived_at TEXT,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "PRIMARY KEY(candidate_id,revision)"
            ")"
        )
        conn.execute(
            "CREATE INDEX idx_candidate_revisions_project "
            "ON candidate_revisions(project_id,candidate_id,revision)"
        )
        conn.execute(
            "INSERT INTO candidate_revisions("
            "candidate_id,project_id,revision,name,payload,archived_at,created_at,updated_at"
            ") SELECT id,project_id,revision,name,payload,archived_at,created_at,updated_at "
            "FROM candidates"
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
