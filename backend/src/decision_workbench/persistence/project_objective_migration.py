"""Add immutable Objective Definition bindings without rewriting legacy Projects."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "project-objective-definition-v1"
MIGRATION_CHECKSUM = "immutable-project-objective-definition-v1"


class ProjectObjectiveMigrationError(RuntimeError):
    pass


def migrate_project_objectives(database: str | Path) -> None:
    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?", (MIGRATION_ID,)
        ).fetchone()
        expected = {
            "objective_definition_json",
            "objective_definition_digest",
            "objective_binding_provenance",
        }
        revisions_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='project_objective_revisions'"
        ).fetchone()
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(projects)")}
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise ProjectObjectiveMigrationError(
                    "project Objective migration checksum does not match"
                )
            if expected - columns or revisions_table is None:
                raise ProjectObjectiveMigrationError(
                    "project Objective columns are incomplete"
                )
            conn.commit()
            return
        for name, definition in (
            ("objective_definition_json", "TEXT"),
            ("objective_definition_digest", "TEXT"),
            (
                "objective_binding_provenance",
                "TEXT NOT NULL DEFAULT 'unbound_legacy'",
            ),
        ):
            if name not in columns:
                conn.execute(f'ALTER TABLE projects ADD COLUMN "{name}" {definition}')
        conn.execute(
            "CREATE TABLE IF NOT EXISTS project_objective_revisions ("
            "project_id TEXT NOT NULL,"
            "objective_digest TEXT NOT NULL,"
            "revision INTEGER NOT NULL,"
            "payload TEXT NOT NULL,"
            "binding_provenance TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "PRIMARY KEY(project_id,objective_digest),"
            "UNIQUE(project_id,revision)"
            ")"
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
