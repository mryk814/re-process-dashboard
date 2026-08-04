"""Additive persistence for immutable-context Model Playground Runs."""

from __future__ import annotations

from pathlib import Path

from decision_workbench.persistence.sqlite_connection import sqlite_connection


TABLE = "model_exploration_runs"


def migrate_model_exploration_runs(database: str | Path) -> None:
    with sqlite_connection(database) as connection:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id TEXT PRIMARY KEY,
                context_digest TEXT NOT NULL,
                execution_revision INTEGER NOT NULL CHECK(execution_revision >= 1),
                payload_json TEXT NOT NULL,
                execution_payload_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_updated "
            f"ON {TABLE}(updated_at DESC, id)"
        )

