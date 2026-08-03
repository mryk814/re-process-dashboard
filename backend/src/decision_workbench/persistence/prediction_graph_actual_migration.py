"""Add immutable actual evidence for Prediction Graph Decision Outputs."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from decision_workbench.persistence.chain_catalog_migration import (
    migrate_chain_catalog,
)
from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "prediction-graph-decision-output-actual-v1"
MIGRATION_CHECKSUM = "immutable-graph-output-actual-json-v1"
TABLE = "prediction_graph_decision_output_actuals"
EXPECTED_COLUMNS = {
    "id",
    "project_id",
    "candidate_id",
    "snapshot_id",
    "output_id",
    "payload_json",
    "created_at",
}


class PredictionGraphActualMigrationError(RuntimeError):
    pass


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{TABLE}")')
    }


def _assert_current(conn: sqlite3.Connection) -> None:
    missing = EXPECTED_COLUMNS - _columns(conn)
    if missing:
        raise PredictionGraphActualMigrationError(
            f"{TABLE} columns are missing: {sorted(missing)}"
        )


def migrate_prediction_graph_actuals(database: str | Path) -> None:
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
                raise PredictionGraphActualMigrationError(
                    "Prediction Graph actual migration checksum does not match"
                )
            _assert_current(conn)
            conn.commit()
            return
        if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (TABLE,),
        ).fetchone():
            raise PredictionGraphActualMigrationError(
                f"{TABLE} exists without its migration marker"
            )
        conn.execute(
            f"CREATE TABLE {TABLE} ("
            "id TEXT PRIMARY KEY, "
            "project_id TEXT NOT NULL REFERENCES projects(id), "
            "candidate_id TEXT NOT NULL, "
            "snapshot_id TEXT NOT NULL REFERENCES chain_snapshot_records(id), "
            "output_id TEXT NOT NULL, payload_json TEXT NOT NULL, "
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            f"CREATE INDEX idx_{TABLE}_candidate_created "
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
