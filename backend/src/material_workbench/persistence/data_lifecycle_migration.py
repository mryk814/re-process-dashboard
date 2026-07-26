from __future__ import annotations

import sqlite3

from material_workbench.persistence.sqlite_connection import connect_sqlite
from datetime import UTC, datetime
from pathlib import Path


MIGRATION_ID = "source-data-lifecycle-v1"
MIGRATION_CHECKSUM = "connector-raw-curation-approval-training-v1"

TABLES = {
    "source_connectors": (
        "CREATE TABLE source_connectors ("
        "id TEXT PRIMARY KEY, configuration_digest TEXT NOT NULL UNIQUE, "
        "payload TEXT NOT NULL, created_at TEXT NOT NULL)"
    ),
    "source_fetch_attempts": (
        "CREATE TABLE source_fetch_attempts ("
        "id TEXT PRIMARY KEY, connector_id TEXT NOT NULL REFERENCES source_connectors(id), "
        "status TEXT NOT NULL, snapshot_id TEXT, payload TEXT NOT NULL, "
        "started_at TEXT NOT NULL)"
    ),
    "raw_source_snapshots": (
        "CREATE TABLE raw_source_snapshots ("
        "id TEXT PRIMARY KEY, connector_id TEXT NOT NULL REFERENCES source_connectors(id), "
        "content_sha256 TEXT NOT NULL, selection_digest TEXT NOT NULL, "
        "snapshot_digest TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, captured_at TEXT NOT NULL, "
        "UNIQUE(connector_id,content_sha256,selection_digest))"
    ),
    "curation_recipes": (
        "CREATE TABLE curation_recipes ("
        "id TEXT PRIMARY KEY, recipe_id TEXT NOT NULL, version INTEGER NOT NULL, "
        "recipe_digest TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL, "
        "UNIQUE(recipe_id,version))"
    ),
    "source_curation_runs": (
        "CREATE TABLE source_curation_runs ("
        "id TEXT PRIMARY KEY, raw_snapshot_id TEXT NOT NULL REFERENCES raw_source_snapshots(id), "
        "recipe_id TEXT NOT NULL REFERENCES curation_recipes(id), profile_digest TEXT NOT NULL, "
        "curation_digest TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, created_at TEXT NOT NULL, "
        "UNIQUE(raw_snapshot_id,recipe_id,profile_digest))"
    ),
    "canonical_dataset_approvals": (
        "CREATE TABLE canonical_dataset_approvals ("
        "id TEXT PRIMARY KEY, curation_run_id TEXT NOT NULL REFERENCES source_curation_runs(id), "
        "dataset_digest TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, approved_at TEXT NOT NULL)"
    ),
    "approved_training_snapshots": (
        "CREATE TABLE approved_training_snapshots ("
        "id TEXT PRIMARY KEY, canonical_dataset_revision_id TEXT NOT NULL "
        "REFERENCES canonical_dataset_approvals(id), snapshot_digest TEXT NOT NULL UNIQUE, "
        "payload TEXT NOT NULL, created_at TEXT NOT NULL)"
    ),
}


def migrate_data_lifecycle(database: str | Path) -> None:
    conn = connect_sqlite(database)
    try:
        _migrate(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE id=?",
        (MIGRATION_ID,),
    ).fetchone()
    if row is not None:
        if row[0] != MIGRATION_CHECKSUM:
            raise RuntimeError("Source data lifecycle migration checksum mismatch")
        for table in TABLES:
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is None:
                raise RuntimeError(f"Source data lifecycle table is missing: {table}")
        return
    for statement in TABLES.values():
        conn.execute(statement)
    conn.execute(
        "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
        (MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now(UTC).isoformat()),
    )
