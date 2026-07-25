"""Persist immutable explicit Chain distribution runs."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from material_workbench.persistence.chain_execution_cas_migration import (
    migrate_chain_execution_cas,
)


MIGRATION_ID = "chain-uncertainty-v1"
MIGRATION_CHECKSUM = "immutable-explicit-monte-carlo-runs-v1"
TABLE = "chain_distribution_runs"
COLUMNS = {
    "id",
    "project_id",
    "candidate_id",
    "candidate_revision",
    "chain_revision_digest",
    "payload_json",
    "created_at",
}


class ChainUncertaintyMigrationError(RuntimeError):
    pass


def migrate_chain_uncertainty(database: str | Path) -> None:
    path = Path(database)
    migrate_chain_execution_cas(path)
    conn = sqlite3.connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?", (MIGRATION_ID,)
        ).fetchone()
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)
        ).fetchone()
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM or table is None:
                raise ChainUncertaintyMigrationError(
                    "chain uncertainty migration marker does not match its table"
                )
            actual = {
                str(row[1])
                for row in conn.execute(f'PRAGMA table_info("{TABLE}")')
            }
            if COLUMNS - actual:
                raise ChainUncertaintyMigrationError(
                    "chain uncertainty migration columns are missing"
                )
            conn.commit()
            return
        if table is not None:
            raise ChainUncertaintyMigrationError(
                "chain uncertainty table exists without migration marker"
            )
        conn.execute(
            "CREATE TABLE chain_distribution_runs ("
            "id TEXT PRIMARY KEY, project_id TEXT NOT NULL, candidate_id TEXT NOT NULL, "
            "candidate_revision INTEGER NOT NULL CHECK(candidate_revision >= 1), "
            "chain_revision_digest TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX idx_chain_distribution_candidate "
            "ON chain_distribution_runs(project_id,candidate_id,created_at DESC)"
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
