"""Add immutable actual-conditioned Chain analysis variants."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from material_workbench.persistence.sqlite_connection import connect_sqlite

from material_workbench.persistence.chain_execution_migration import (
    migrate_chain_execution,
)


MIGRATION_ID = "chain-analysis-variant-v1"
MIGRATION_CHECKSUM = "immutable-actual-conditioned-stage-c-v1"
TABLE = "chain_analysis_variant_records"
REQUIRED_COLUMNS = {
    "id",
    "project_id",
    "candidate_id",
    "candidate_revision",
    "comparison_snapshot_id",
    "payload_json",
    "created_at",
}


class ChainAnalysisVariantMigrationError(RuntimeError):
    pass


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{TABLE}")')}


def _assert_current(conn: sqlite3.Connection) -> None:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (TABLE,),
    ).fetchone()
    if exists is None:
        raise ChainAnalysisVariantMigrationError(
            "chain analysis variant migration is marked complete but table is missing"
        )
    missing = REQUIRED_COLUMNS - _columns(conn)
    if missing:
        raise ChainAnalysisVariantMigrationError(
            f"chain analysis variant columns are missing: {sorted(missing)}"
        )


def migrate_chain_analysis_variant(database: str | Path) -> None:
    path = Path(database)
    migrate_chain_execution(path)
    conn = connect_sqlite(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if row is not None:
            if row[0] != MIGRATION_CHECKSUM:
                raise ChainAnalysisVariantMigrationError(
                    "chain analysis variant migration checksum does not match"
                )
            _assert_current(conn)
            conn.commit()
            return
        existing = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        ).fetchone()
        if existing is not None:
            raise ChainAnalysisVariantMigrationError(
                "chain analysis variant table exists without migration marker"
            )
        conn.execute(
            f"CREATE TABLE {TABLE} ("
            "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, candidate_id TEXT NOT NULL, "
            "candidate_revision INTEGER NOT NULL, comparison_snapshot_id TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            f"CREATE INDEX idx_chain_analysis_variant_candidate "
            f"ON {TABLE}(project_id,candidate_id,created_at)"
        )
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now(UTC).isoformat()),
        )
        _assert_current(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
