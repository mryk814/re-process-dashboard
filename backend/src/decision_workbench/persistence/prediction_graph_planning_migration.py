"""Persist explicit Graph objectives and immutable goal-search runs."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from decision_workbench.persistence.chain_catalog_migration import (
    migrate_chain_catalog,
)
from decision_workbench.persistence.sqlite_connection import connect_sqlite

MIGRATION_ID = "prediction-graph-planning-v1"
MIGRATION_CHECKSUM = "graph-objective-goal-search-json-v1"
OBJECTIVES_TABLE = "prediction_graph_objectives"
RUNS_TABLE = "prediction_graph_goal_search_runs"
EXPECTED_COLUMNS = {
    OBJECTIVES_TABLE: {
        "id",
        "project_id",
        "payload_json",
        "created_at",
    },
    RUNS_TABLE: {
        "id",
        "project_id",
        "objective_id",
        "payload_json",
        "created_at",
    },
}


class PredictionGraphPlanningMigrationError(RuntimeError):
    pass


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        is not None
    )


def _assert_current(conn: sqlite3.Connection) -> None:
    for table, expected in EXPECTED_COLUMNS.items():
        columns = {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}
        missing = expected - columns
        if missing:
            raise PredictionGraphPlanningMigrationError(
                f"{table} columns are missing: {sorted(missing)}"
            )


def migrate_prediction_graph_planning(database: str | Path) -> None:
    path = Path(database)
    migrate_chain_catalog(path)
    conn = connect_sqlite(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?", (MIGRATION_ID,)
        ).fetchone()
        if row is not None:
            if row[0] != MIGRATION_CHECKSUM:
                raise PredictionGraphPlanningMigrationError(
                    "Prediction Graph planning migration checksum does not match"
                )
            _assert_current(conn)
            conn.commit()
            return
        existing = [table for table in EXPECTED_COLUMNS if _table_exists(conn, table)]
        if existing:
            raise PredictionGraphPlanningMigrationError(
                "Prediction Graph planning tables exist without their "
                f"migration marker: {', '.join(existing)}"
            )
        conn.execute(
            f"CREATE TABLE {OBJECTIVES_TABLE} ("
            "id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), "
            "payload_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            f"CREATE TABLE {RUNS_TABLE} ("
            "id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), "
            "objective_id TEXT NOT NULL REFERENCES prediction_graph_objectives(id), "
            "payload_json TEXT NOT NULL, created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX idx_prediction_graph_objectives_project ON prediction_graph_objectives(project_id,created_at)"
        )
        conn.execute(
            "CREATE INDEX idx_prediction_graph_goal_runs_project ON prediction_graph_goal_search_runs(project_id,created_at)"
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
