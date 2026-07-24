from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3


MIGRATION_ID = "decision-activity-run-v1"
MIGRATION_CHECKSUM = "immutable-decision-activity-run-v1"


class DecisionActivityMigrationError(RuntimeError):
    pass


def migrate_decision_activity_runs(database: str | Path) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise DecisionActivityMigrationError("decision activity migration checksum does not match")
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(decision_activity_runs)")
            }
            expected = {
                "id", "semantic_identity", "project_id", "candidate_id",
                "activity_id", "activity_version", "payload", "created_at",
            }
            if expected - columns:
                raise DecisionActivityMigrationError("decision activity run table is incomplete")
            conn.commit()
            return
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decision_activity_runs'"
        ).fetchone()
        if exists is not None:
            raise DecisionActivityMigrationError(
                "decision activity run table exists without its migration marker"
            )
        conn.execute(
            "CREATE TABLE decision_activity_runs ("
            "id TEXT PRIMARY KEY,"
            "semantic_identity TEXT NOT NULL UNIQUE,"
            "project_id TEXT NOT NULL REFERENCES projects(id),"
            "candidate_id TEXT NOT NULL REFERENCES candidates(id),"
            "activity_id TEXT NOT NULL,"
            "activity_version TEXT NOT NULL,"
            "payload TEXT NOT NULL,"
            "created_at TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE INDEX idx_decision_activity_runs_project_candidate "
            "ON decision_activity_runs(project_id,candidate_id,created_at)"
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
