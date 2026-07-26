from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from material_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "lineage-node-review-v1"
MIGRATION_CHECKSUM = "project-scoped-lineage-review-v1"


class LineageReviewMigrationError(RuntimeError):
    pass


def migrate_lineage_reviews(database: str | Path) -> None:
    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if row is not None:
            if row[0] != MIGRATION_CHECKSUM:
                raise LineageReviewMigrationError("lineage review migration checksum does not match")
            columns = {
                str(item[1])
                for item in conn.execute("PRAGMA table_info(lineage_node_reviews)")
            }
            expected = {
                "project_id", "entity_key", "entity_type", "status",
                "note", "created_at", "updated_at",
            }
            if expected - columns:
                raise LineageReviewMigrationError("lineage review table is incomplete")
            conn.commit()
            return
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lineage_node_reviews'"
        ).fetchone()
        if exists is not None:
            raise LineageReviewMigrationError(
                "lineage review table exists without its migration marker"
            )
        conn.execute(
            "CREATE TABLE lineage_node_reviews ("
            "project_id TEXT NOT NULL REFERENCES projects(id),"
            "entity_key TEXT NOT NULL,"
            "entity_type TEXT NOT NULL,"
            "status TEXT NOT NULL CHECK(status IN ('noted','later','accepted','needs_fix','hidden')),"
            "note TEXT NOT NULL DEFAULT '',"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL,"
            "PRIMARY KEY(project_id,entity_key)"
            ")"
        )
        conn.execute(
            "CREATE INDEX idx_lineage_node_reviews_project_status "
            "ON lineage_node_reviews(project_id,status,updated_at)"
        )
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (
                MIGRATION_ID,
                MIGRATION_CHECKSUM,
                datetime.now(UTC).isoformat(),
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
