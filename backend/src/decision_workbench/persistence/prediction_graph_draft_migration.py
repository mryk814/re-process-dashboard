from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from decision_workbench.persistence.chain_catalog_migration import (
    migrate_chain_catalog,
)
from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "prediction-graph-drafts-v1"
MIGRATION_CHECKSUM = "mutable-graph-authoring-drafts-v1"
TABLE = "prediction_graph_drafts"
EXPECTED_COLUMNS = {
    "draft_id",
    "version",
    "content_json",
    "created_at",
    "updated_at",
}


def migrate_prediction_graph_drafts(database: str | Path) -> None:
    path = Path(database)
    migrate_chain_catalog(path)
    connection = connect_sqlite(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if row is not None:
            if row[0] != MIGRATION_CHECKSUM:
                raise RuntimeError(
                    "prediction graph draft migration checksum does not match"
                )
            columns = {
                str(item[1])
                for item in connection.execute(
                    f'PRAGMA table_info("{TABLE}")'
                )
            }
            if not EXPECTED_COLUMNS.issubset(columns):
                raise RuntimeError(
                    "prediction graph draft schema is incomplete"
                )
            connection.commit()
            return
        connection.execute(
            f"CREATE TABLE {TABLE} ("
            "draft_id TEXT PRIMARY KEY,"
            "version INTEGER NOT NULL CHECK(version >= 1),"
            "content_json TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL"
            ")"
        )
        connection.execute(
            f"CREATE INDEX idx_{TABLE}_updated "
            f"ON {TABLE}(updated_at,draft_id)"
        )
        connection.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) "
            "VALUES (?,?,?)",
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
