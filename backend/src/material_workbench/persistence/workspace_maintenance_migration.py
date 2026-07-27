from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from material_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "workspace-maintenance-events-v1"
MIGRATION_CHECKSUM = "audited-explicit-catalog-maintenance-v1"


def migrate_workspace_maintenance_events(database: str | Path) -> None:
    connection = connect_sqlite(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if row is not None:
            if row[0] != MIGRATION_CHECKSUM:
                raise RuntimeError(
                    "workspace maintenance migration checksum does not match"
                )
            columns = {
                str(item[1])
                for item in connection.execute(
                    "PRAGMA table_info(workspace_maintenance_events)"
                )
            }
            expected = {
                "id",
                "operation",
                "resource_kind",
                "resource_id",
                "reason",
                "detail_json",
                "created_at",
            }
            if not expected.issubset(columns):
                raise RuntimeError(
                    "workspace maintenance event schema is incomplete"
                )
            connection.commit()
            return
        connection.execute(
            "CREATE TABLE workspace_maintenance_events ("
            "id TEXT PRIMARY KEY,"
            "operation TEXT NOT NULL,"
            "resource_kind TEXT NOT NULL,"
            "resource_id TEXT NOT NULL,"
            "reason TEXT NOT NULL,"
            "detail_json TEXT NOT NULL,"
            "created_at TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "CREATE INDEX idx_workspace_maintenance_resource "
            "ON workspace_maintenance_events(resource_kind,resource_id,created_at)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (
                MIGRATION_ID,
                MIGRATION_CHECKSUM,
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
