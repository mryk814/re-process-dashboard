"""Add immutable Project Design Space bindings without inventing legacy history."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3


MIGRATION_ID = "project-design-space-v1"
MIGRATION_CHECKSUM = "immutable-project-design-space-v1"


class ProjectDesignSpaceMigrationError(RuntimeError):
    pass


def migrate_project_design_spaces(database: str | Path) -> None:
    conn = sqlite3.connect(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?", (MIGRATION_ID,)
        ).fetchone()
        expected = {
            "design_space_json",
            "design_space_digest",
            "design_space_binding_provenance",
        }
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(projects)")
        }
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise ProjectDesignSpaceMigrationError(
                    "project Design Space migration checksum does not match"
                )
            if expected - columns:
                raise ProjectDesignSpaceMigrationError(
                    "project Design Space columns are incomplete"
                )
            conn.commit()
            return
        for name, definition in (
            ("design_space_json", "TEXT"),
            ("design_space_digest", "TEXT"),
            (
                "design_space_binding_provenance",
                "TEXT NOT NULL DEFAULT 'unbound_legacy'",
            ),
        ):
            if name not in columns:
                conn.execute(f'ALTER TABLE projects ADD COLUMN "{name}" {definition}')
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
