from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "decision-replay-v1"
MIGRATION_CHECKSUM = "append-only-decision-case-and-replay-run-v1"


class DecisionReplayMigrationError(RuntimeError):
    pass


def migrate_decision_replay(database: str | Path) -> None:
    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?", (MIGRATION_ID,)
        ).fetchone()
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise DecisionReplayMigrationError(
                    "decision replay migration checksum does not match"
                )
            expected = {
                "decision_cases": {
                    "id", "semantic_identity", "project_id", "task_id",
                    "task_contract_digest", "objective_definition_digest",
                    "decision_timestamp", "payload", "created_at",
                },
                "decision_replay_runs": {
                    "id", "semantic_identity", "project_id", "case_id",
                    "payload", "created_at",
                },
            }
            for table, columns in expected.items():
                actual = {
                    str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
                }
                if columns - actual:
                    raise DecisionReplayMigrationError(
                        f"{table} is incomplete"
                    )
            conn.commit()
            return
        for table in ("decision_cases", "decision_replay_runs"):
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone() is not None:
                raise DecisionReplayMigrationError(
                    f"{table} exists without its migration marker"
                )
        conn.execute(
            "CREATE TABLE decision_cases ("
            "id TEXT PRIMARY KEY,"
            "semantic_identity TEXT NOT NULL UNIQUE,"
            "project_id TEXT NOT NULL REFERENCES projects(id),"
            "task_id TEXT NOT NULL,"
            "task_contract_digest TEXT NOT NULL,"
            "objective_definition_digest TEXT,"
            "decision_timestamp TEXT NOT NULL,"
            "payload TEXT NOT NULL,"
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX idx_decision_cases_compatible "
            "ON decision_cases(task_id,task_contract_digest,objective_definition_digest,decision_timestamp)"
        )
        conn.execute(
            "CREATE TABLE decision_replay_runs ("
            "id TEXT PRIMARY KEY,"
            "semantic_identity TEXT NOT NULL UNIQUE,"
            "project_id TEXT NOT NULL REFERENCES projects(id),"
            "case_id TEXT NOT NULL REFERENCES decision_cases(id),"
            "payload TEXT NOT NULL,"
            "created_at TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE INDEX idx_decision_replay_runs_case "
            "ON decision_replay_runs(project_id,case_id,created_at)"
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
