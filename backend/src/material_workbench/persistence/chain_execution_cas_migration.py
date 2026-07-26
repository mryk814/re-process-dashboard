"""Add an atomic generation claim for Chain execution state writes."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from material_workbench.persistence.sqlite_connection import connect_sqlite

from material_workbench.persistence.chain_execution_migration import (
    migrate_chain_execution,
)


MIGRATION_ID = "chain-execution-cas-v1"
MIGRATION_CHECKSUM = "atomic-scope-request-generation-v1"
TABLE = "chain_execution_claims"
COLUMNS = {"scope_id", "request_id", "generation", "updated_at"}


class ChainExecutionCasMigrationError(RuntimeError):
    pass


def migrate_chain_execution_cas(database: str | Path) -> None:
    path = Path(database)
    migrate_chain_execution(path)
    conn = connect_sqlite(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        table_exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        ).fetchone()
        if row is not None:
            if row[0] != MIGRATION_CHECKSUM:
                raise ChainExecutionCasMigrationError(
                    "chain execution CAS migration checksum does not match"
                )
            if table_exists is None:
                raise ChainExecutionCasMigrationError(
                    "chain execution CAS migration is marked complete but table is missing"
                )
            actual = {
                str(item[1])
                for item in conn.execute(f'PRAGMA table_info("{TABLE}")')
            }
            if COLUMNS - actual:
                raise ChainExecutionCasMigrationError(
                    "chain execution CAS migration columns are missing"
                )
            conn.commit()
            return
        if table_exists is not None:
            raise ChainExecutionCasMigrationError(
                "chain execution CAS table exists without migration marker"
            )
        conn.execute(
            "CREATE TABLE chain_execution_claims ("
            "scope_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, "
            "generation INTEGER NOT NULL CHECK(generation >= 1), updated_at TEXT NOT NULL)"
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
