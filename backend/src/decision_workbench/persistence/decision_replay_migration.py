from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "decision-replay-v1"
MIGRATION_CHECKSUM = "append-only-decision-case-and-replay-run-v1"
ATTACHMENT_MIGRATION_ID = "decision-case-actual-attachment-v1"
ATTACHMENT_MIGRATION_CHECKSUM = "append-only-decision-case-actual-attachment-v1"
ATTACHMENT_TABLE = "decision_case_actual_attachments"


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
            _migrate_actual_attachments(conn)
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
        _migrate_actual_attachments(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate_actual_attachments(conn) -> None:
    marker = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE id=?", (ATTACHMENT_MIGRATION_ID,)
    ).fetchone()
    expected = {
        "id", "semantic_identity", "case_id", "actual_id", "candidate_id",
        "candidate_revision", "prediction_snapshot_id", "payload", "attached_at",
    }
    if marker is not None:
        if marker[0] != ATTACHMENT_MIGRATION_CHECKSUM:
            raise DecisionReplayMigrationError(
                "decision case Actual attachment migration checksum does not match"
            )
        actual = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({ATTACHMENT_TABLE})")}
        if expected - actual:
            raise DecisionReplayMigrationError(
                "decision case Actual attachment table is incomplete"
            )
        return
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (ATTACHMENT_TABLE,)
    ).fetchone() is not None:
        raise DecisionReplayMigrationError(
            "decision case Actual attachment table exists without its migration marker"
        )
    conn.execute(
        "CREATE TABLE decision_case_actual_attachments ("
        "id TEXT PRIMARY KEY,semantic_identity TEXT NOT NULL UNIQUE,"
        "case_id TEXT NOT NULL REFERENCES decision_cases(id),actual_id TEXT NOT NULL,"
        "candidate_id TEXT NOT NULL,candidate_revision INTEGER NOT NULL,"
        "prediction_snapshot_id TEXT NOT NULL,payload TEXT NOT NULL,attached_at TEXT NOT NULL,"
        "UNIQUE(case_id,actual_id))"
    )
    conn.execute(
        "CREATE INDEX idx_decision_case_actual_attachments_case "
        "ON decision_case_actual_attachments(case_id,attached_at)"
    )
    # PR-local v1 Workspaces may already contain Cases whose retrospective Actuals
    # lived in the Case payload. Preserve them as immutable attachment records.
    for row in conn.execute("SELECT id,semantic_identity,payload,created_at FROM decision_cases"):
        payload = json.loads(row[2])
        for evidence in payload.get("retrospective_actuals", []):
            actual = evidence["actual"]
            candidate = evidence["candidate"]
            identity_input = {
                "case_identity": row[1], "actual_id": actual["id"],
                "candidate": candidate, "prediction_snapshot_created_at": evidence["prediction_snapshot_created_at"],
            }
            semantic_identity = "sha256:" + hashlib.sha256(
                json.dumps(identity_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            attachment_id = f"decision-case-actual-attachment-{semantic_identity[7:31]}"
            attachment_payload = {
                "schema_version": "decision-case-actual-attachment/v1",
                "actual": actual, "candidate": candidate,
                "prediction_snapshot_id": actual["snapshot_id"],
                "prediction_snapshot_created_at": evidence["prediction_snapshot_created_at"],
            }
            conn.execute(
                "INSERT OR IGNORE INTO decision_case_actual_attachments("
                "id,semantic_identity,case_id,actual_id,candidate_id,candidate_revision,"
                "prediction_snapshot_id,payload,attached_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (attachment_id, semantic_identity, row[0], actual["id"], candidate["candidate_id"],
                 candidate["candidate_revision"], actual["snapshot_id"],
                 json.dumps(attachment_payload, ensure_ascii=False, sort_keys=True), row[3]),
            )
    conn.execute(
        "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
        (ATTACHMENT_MIGRATION_ID, ATTACHMENT_MIGRATION_CHECKSUM, datetime.now(UTC).isoformat()),
    )
