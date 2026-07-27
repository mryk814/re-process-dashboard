from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from material_workbench.persistence.project_persistence_inventory import (
    PROJECT_PERSISTENCE,
)
from material_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "project-lifecycle-v1"
MIGRATION_CHECKSUM = "project-archive-and-explicit-purge-v1"


class ProjectLifecycleMigrationError(RuntimeError):
    pass


def remove_project_archive_write_guards(database: str | Path) -> None:
    """Remove runtime-only guards while startup migrations have exclusive use."""

    connection = connect_sqlite(database)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' "
            "AND (name='guard_project_delete' "
            "OR name LIKE 'guard_archived_%')"
        ).fetchall()
        for row in rows:
            name = str(row[0]).replace('"', '""')
            connection.execute(f'DROP TRIGGER "{name}"')
        connection.commit()
    finally:
        connection.close()


def migrate_project_lifecycle(database: str | Path) -> None:
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
        connection.execute(
            "CREATE TABLE IF NOT EXISTS project_purge_authorizations ("
            "project_id TEXT PRIMARY KEY)"
        )
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise ProjectLifecycleMigrationError(
                    "project lifecycle migration checksum does not match"
                )
            if "archived_at" not in columns:
                raise ProjectLifecycleMigrationError(
                    "project lifecycle column is incomplete"
                )
            connection.commit()
            return
        if "archived_at" not in columns:
            connection.execute(
                "ALTER TABLE projects ADD COLUMN archived_at TEXT"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_projects_active "
            "ON projects(archived_at,created_at,id)"
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


def install_project_archive_write_guards(database: str | Path) -> None:
    """Install database-level guards after every Project-scoped table exists."""

    connection = connect_sqlite(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "CREATE TRIGGER IF NOT EXISTS guard_archived_project_update "
            "BEFORE UPDATE ON projects "
            "WHEN OLD.archived_at IS NOT NULL AND NEW.archived_at IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT, 'project_archived'); END"
        )
        connection.execute(
            "CREATE TRIGGER IF NOT EXISTS guard_project_delete "
            "BEFORE DELETE ON projects "
            "WHEN NOT EXISTS ("
            "SELECT 1 FROM project_purge_authorizations "
            "WHERE project_id=OLD.id"
            ") BEGIN SELECT RAISE(ABORT, 'project_archived'); END"
        )
        for table in (
            table
            for table in PROJECT_PERSISTENCE.direct_tables
            if table != "projects"
        ):
            for operation in ("INSERT", "UPDATE"):
                trigger = f"guard_archived_{table}_{operation.lower()}"
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger} "
                    f"BEFORE {operation} ON {table} "
                    "WHEN EXISTS ("
                    "SELECT 1 FROM projects "
                    "WHERE id=NEW.project_id AND archived_at IS NOT NULL"
                    ") BEGIN SELECT RAISE(ABORT, 'project_archived'); END"
                )
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS guard_archived_{table}_delete "
                f"BEFORE DELETE ON {table} "
                "WHEN EXISTS ("
                "SELECT 1 FROM projects "
                "WHERE id=OLD.project_id AND archived_at IS NOT NULL"
                ") AND NOT EXISTS ("
                "SELECT 1 FROM project_purge_authorizations "
                "WHERE project_id=OLD.project_id"
                ") BEGIN SELECT RAISE(ABORT, 'project_archived'); END"
            )
        for table in PROJECT_PERSISTENCE.candidate_tables:
            for operation in ("INSERT", "UPDATE"):
                trigger = f"guard_archived_{table}_{operation.lower()}"
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger} "
                    f"BEFORE {operation} ON {table} "
                    "WHEN EXISTS ("
                    "SELECT 1 FROM candidates "
                    "JOIN projects ON projects.id=candidates.project_id "
                    "WHERE candidates.id=NEW.candidate_id "
                    "AND projects.archived_at IS NOT NULL"
                    ") BEGIN SELECT RAISE(ABORT, 'project_archived'); END"
                )
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS guard_archived_{table}_delete "
                f"BEFORE DELETE ON {table} "
                "WHEN EXISTS ("
                "SELECT 1 FROM candidates "
                "JOIN projects ON projects.id=candidates.project_id "
                "WHERE candidates.id=OLD.candidate_id "
                "AND projects.archived_at IS NOT NULL"
                ") AND NOT EXISTS ("
                "SELECT 1 FROM project_purge_authorizations "
                "WHERE project_id=("
                "SELECT project_id FROM candidates WHERE id=OLD.candidate_id"
                ")"
                ") BEGIN SELECT RAISE(ABORT, 'project_archived'); END"
            )
        for table in PROJECT_PERSISTENCE.scope_tables:
            for operation in ("INSERT", "UPDATE"):
                trigger = f"guard_archived_{table}_{operation.lower()}"
                connection.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger} "
                    f"BEFORE {operation} ON {table} "
                    "WHEN EXISTS ("
                    "SELECT 1 FROM projects "
                    "WHERE id=substr("
                    "NEW.scope_id,1,instr(NEW.scope_id,':')-1"
                    ") AND archived_at IS NOT NULL"
                    ") BEGIN SELECT RAISE(ABORT, 'project_archived'); END"
                )
            connection.execute(
                f"CREATE TRIGGER IF NOT EXISTS guard_archived_{table}_delete "
                f"BEFORE DELETE ON {table} "
                "WHEN EXISTS ("
                "SELECT 1 FROM projects "
                "WHERE id=substr("
                "OLD.scope_id,1,instr(OLD.scope_id,':')-1"
                ") AND archived_at IS NOT NULL"
                ") AND NOT EXISTS ("
                "SELECT 1 FROM project_purge_authorizations "
                "WHERE project_id=substr("
                "OLD.scope_id,1,instr(OLD.scope_id,':')-1"
                ")"
                ") BEGIN SELECT RAISE(ABORT, 'project_archived'); END"
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
