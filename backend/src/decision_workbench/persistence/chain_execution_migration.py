"""Add durable Chain memo, freshness state, and immutable execution snapshots."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from decision_workbench.persistence.sqlite_connection import connect_sqlite

from decision_workbench.persistence.chain_catalog_migration import migrate_chain_catalog


MIGRATION_ID = "chain-execution-v1"
MIGRATION_CHECKSUM = "durable-stage-memo-state-snapshot-v1"

TABLE_COLUMNS = {
    "chain_stage_memo": {
        "memo_key",
        "stage_id",
        "input_digest",
        "contract_digest",
        "package_manifest_digest",
        "canonical_input_json",
        "result_json",
        "created_at",
    },
    "chain_execution_state": {
        "scope_id",
        "request_id",
        "execution_json",
        "updated_at",
    },
}


class ChainExecutionMigrationError(RuntimeError):
    pass


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not str(row[0]).startswith("sqlite_")
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _assert_current(conn: sqlite3.Connection) -> None:
    missing_tables = set(TABLE_COLUMNS) - _tables(conn)
    if missing_tables:
        raise ChainExecutionMigrationError(
            f"chain execution migration is marked complete but tables are missing: {sorted(missing_tables)}"
        )
    for table, expected in TABLE_COLUMNS.items():
        missing = expected - _columns(conn, table)
        if missing:
            raise ChainExecutionMigrationError(
                f"chain execution migration is marked complete but {table} columns are missing: "
                f"{sorted(missing)}"
            )


def migrate_chain_execution(database: str | Path) -> None:
    path = Path(database)
    migrate_chain_catalog(path)
    conn = connect_sqlite(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if row is not None:
            if row[0] != MIGRATION_CHECKSUM:
                raise ChainExecutionMigrationError(
                    "chain execution migration checksum does not match"
                )
            _assert_current(conn)
            conn.commit()
            return
        existing = set(TABLE_COLUMNS) & _tables(conn)
        if existing:
            raise ChainExecutionMigrationError(
                f"chain execution tables exist without migration marker: {sorted(existing)}"
            )
        conn.execute(
            "CREATE TABLE chain_stage_memo ("
            "memo_key TEXT PRIMARY KEY, stage_id TEXT NOT NULL, input_digest TEXT NOT NULL, "
            "contract_digest TEXT NOT NULL, package_manifest_digest TEXT NOT NULL, "
            "canonical_input_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX idx_chain_stage_memo_input "
            "ON chain_stage_memo(stage_id,input_digest)"
        )
        conn.execute(
            "CREATE TABLE chain_execution_state ("
            "scope_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
            "execution_json TEXT NOT NULL, updated_at TEXT NOT NULL)"
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
