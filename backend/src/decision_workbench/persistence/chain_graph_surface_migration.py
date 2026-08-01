"""Persist the exact Stage contract surfaces validated for each Chain Revision."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "chain-graph-surfaces-v1"
MIGRATION_CHECKSUM = "revision-pinned-stage-contract-surfaces-v1"
TABLE = "chain_stage_contract_surfaces"
EXPECTED_COLUMNS = {"chain_revision_id", "stage_id", "surface_json", "created_at"}


class ChainGraphSurfaceMigrationError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _table_exists(conn: sqlite3.Connection) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{TABLE}")')}


def _assert_current(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn):
        raise ChainGraphSurfaceMigrationError("chain graph surface table is missing")
    missing = EXPECTED_COLUMNS - _columns(conn)
    if missing:
        raise ChainGraphSurfaceMigrationError(
            f"chain graph surface table is missing columns: {sorted(missing)}"
        )


def migrate_chain_graph_surfaces(database: str | Path) -> int:
    """Add a read-only companion table without rewriting immutable revisions."""

    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?", (MIGRATION_ID,)
        ).fetchone()
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise ChainGraphSurfaceMigrationError(
                    "chain graph surface migration checksum does not match"
                )
            _assert_current(conn)
            return 0
        if _table_exists(conn):
            raise ChainGraphSurfaceMigrationError(
                "chain graph surface table exists without migration marker"
            )
        conn.execute(
            "CREATE TABLE chain_stage_contract_surfaces ("
            "chain_revision_id TEXT NOT NULL REFERENCES chain_revisions(id), "
            "stage_id TEXT NOT NULL, surface_json TEXT NOT NULL, created_at TEXT NOT NULL, "
            "PRIMARY KEY(chain_revision_id,stage_id))"
        )
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, _now()),
        )
        _assert_current(conn)
        conn.commit()
        return 1
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
