from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "ai-review-run-v1"
MIGRATION_CHECKSUM = "immutable-ai-review-run-and-append-only-disposition-v1"


class AiReviewMigrationError(RuntimeError):
    pass


def migrate_ai_reviews(database: str | Path) -> None:
    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise AiReviewMigrationError("AI review migration checksum does not match")
            run_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(ai_review_runs)")
            }
            disposition_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(ai_review_dispositions)")
            }
            if {
                "review_run_id",
                "project_id",
                "candidate_id",
                "candidate_revision",
                "state",
                "payload",
                "started_at",
                "completed_at",
            } - run_columns:
                raise AiReviewMigrationError("AI review run table is incomplete")
            if {
                "disposition_id",
                "review_run_id",
                "project_id",
                "payload",
                "recorded_at",
            } - disposition_columns:
                raise AiReviewMigrationError("AI review disposition table is incomplete")
            conn.commit()
            return
        for table in ("ai_review_runs", "ai_review_dispositions"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is not None:
                raise AiReviewMigrationError(
                    f"{table} exists without its migration marker"
                )
        conn.execute(
            "CREATE TABLE ai_review_runs ("
            "review_run_id TEXT PRIMARY KEY,"
            "project_id TEXT NOT NULL REFERENCES projects(id),"
            "candidate_id TEXT NOT NULL REFERENCES candidates(id),"
            "candidate_revision INTEGER NOT NULL CHECK(candidate_revision > 0),"
            "state TEXT NOT NULL CHECK(state IN "
            "('running','completed','partial','invalid','failed')),"
            "payload TEXT NOT NULL,"
            "started_at TEXT NOT NULL,"
            "completed_at TEXT"
            ")"
        )
        conn.execute(
            "CREATE INDEX idx_ai_review_runs_project_candidate "
            "ON ai_review_runs(project_id,candidate_id,started_at)"
        )
        conn.execute(
            "CREATE TABLE ai_review_dispositions ("
            "disposition_id TEXT PRIMARY KEY,"
            "review_run_id TEXT NOT NULL REFERENCES ai_review_runs(review_run_id),"
            "project_id TEXT NOT NULL REFERENCES projects(id),"
            "payload TEXT NOT NULL,"
            "recorded_at TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE INDEX idx_ai_review_dispositions_run "
            "ON ai_review_dispositions(review_run_id,recorded_at)"
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
