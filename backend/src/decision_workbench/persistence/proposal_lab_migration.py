from __future__ import annotations

from pathlib import Path

from decision_workbench.persistence.sqlite_connection import sqlite_connection


def migrate_proposal_lab_reports(path: str | Path) -> None:
    with sqlite_connection(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proposal_lab_reports (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(project_id, id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proposal_lab_project "
            "ON proposal_lab_reports(project_id, created_at DESC)"
        )
