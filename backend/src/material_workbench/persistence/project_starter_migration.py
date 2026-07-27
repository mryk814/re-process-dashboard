from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from material_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "project-starter-identity-v1"
MIGRATION_CHECKSUM = "explicit-project-starter-identity-v1"


class ProjectStarterMigrationError(RuntimeError):
    pass


def migrate_project_starter_identity(database: str | Path) -> None:
    connection = connect_sqlite(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        marker = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(projects)")
        }
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise ProjectStarterMigrationError(
                    "project starter migration checksum does not match"
                )
            if "is_starter" not in columns:
                raise ProjectStarterMigrationError(
                    "project starter identity column is incomplete"
                )
            connection.commit()
            return
        if "is_starter" not in columns:
            connection.execute(
                "ALTER TABLE projects ADD COLUMN is_starter "
                "INTEGER NOT NULL DEFAULT 0 CHECK(is_starter IN (0,1))"
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
