"""Add immutable Chain revisions and explicit Project scientific identity."""
from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from material_workbench.contracts.chain_contracts import (
    SingleTaskProjectIdentity,
)
from material_workbench.persistence.workspace_catalog_migration import (
    migrate_workspace_catalog,
)
from material_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "chain-catalog-v1"
MIGRATION_CHECKSUM = "immutable-chain-revisions-project-identity-v1"


class ChainCatalogMigrationError(RuntimeError):
    pass


CHAIN_TABLE_COLUMNS = {
    "chain_definitions": {
        "id",
        "chain_id",
        "definition_digest",
        "definition_json",
        "created_at",
    },
    "chain_revisions": {
        "id",
        "chain_id",
        "revision",
        "revision_digest",
        "revision_json",
        "created_at",
    },
    "chain_snapshot_records": {
        "id",
        "project_id",
        "candidate_id",
        "candidate_revision",
        "identity_json",
        "payload_json",
        "created_at",
    },
}
PROJECT_IDENTITY_COLUMN = "scientific_identity_json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if not str(row[0]).startswith("sqlite_")
    }


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def _assert_current(conn: sqlite3.Connection) -> None:
    missing_tables = set(CHAIN_TABLE_COLUMNS) - _tables(conn)
    if missing_tables:
        raise ChainCatalogMigrationError(
            f"chain catalog migration is marked complete but tables are missing: {sorted(missing_tables)}"
        )
    for table, expected in CHAIN_TABLE_COLUMNS.items():
        missing = expected - _columns(conn, table)
        if missing:
            raise ChainCatalogMigrationError(
                f"chain catalog migration is marked complete but {table} columns are missing: "
                f"{sorted(missing)}"
            )
    if PROJECT_IDENTITY_COLUMN not in _columns(conn, "projects"):
        raise ChainCatalogMigrationError(
            "chain catalog migration is marked complete but Project identity is missing"
        )
    null_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM projects WHERE {PROJECT_IDENTITY_COLUMN} IS NULL"
        ).fetchone()[0]
    )
    if null_count:
        raise ChainCatalogMigrationError(
            "chain catalog migration left Projects without scientific identity"
        )


def _migration_is_current(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE id=?",
        (MIGRATION_ID,),
    ).fetchone()
    if row is None:
        return False
    if row[0] != MIGRATION_CHECKSUM:
        raise ChainCatalogMigrationError("chain catalog migration checksum does not match")
    _assert_current(conn)
    return True


def _create_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE chain_definitions ("
        "id TEXT PRIMARY KEY, chain_id TEXT NOT NULL, definition_digest TEXT NOT NULL UNIQUE, "
        "definition_json TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_chain_definitions_chain_digest "
        "ON chain_definitions(chain_id,definition_digest)"
    )
    conn.execute(
        "CREATE TABLE chain_revisions ("
        "id TEXT PRIMARY KEY, chain_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), "
        "revision_digest TEXT NOT NULL UNIQUE, revision_json TEXT NOT NULL, created_at TEXT NOT NULL, "
        "UNIQUE(chain_id,revision))"
    )
    conn.execute(
        "CREATE TABLE chain_snapshot_records ("
        "id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), "
        "candidate_id TEXT NOT NULL, candidate_revision INTEGER NOT NULL CHECK(candidate_revision >= 1), "
        "identity_json TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX idx_chain_snapshots_project_created "
        "ON chain_snapshot_records(project_id,created_at)"
    )


def _single_task_identity_from_row(row: sqlite3.Row | tuple[object, ...]) -> SingleTaskProjectIdentity:
    provenance = str(row[6])
    if provenance == "unbound_legacy":
        if any(row[index] for index in range(2, 6)):
            raise ChainCatalogMigrationError(
                f"Project {row[0]} is marked unbound but has immutable references"
            )
        return SingleTaskProjectIdentity(
            identity_kind="single_task",
            task_id=str(row[1]),
            binding_provenance="unbound_legacy",
        )
    values = tuple(row[index] for index in range(2, 6))
    if not all(values):
        raise ChainCatalogMigrationError(
            f"Project {row[0]} has a partial immutable single-Task binding"
        )
    return SingleTaskProjectIdentity(
        identity_kind="single_task",
        task_id=str(row[1]),
        dataset_view_revision_id=str(row[2]),
        task_contract_digest=str(row[3]),
        model_package_ref_id=str(row[4]),
        model_package_manifest_digest=str(row[5]),
        binding_provenance=provenance,  # type: ignore[arg-type]
    )


def _identity_json(identity: SingleTaskProjectIdentity) -> str:
    return json.dumps(
        identity.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _backfill_single_task_identity(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id,task_id,dataset_view_revision_id,task_contract_digest,"
        "model_package_ref_id,model_package_manifest_digest,binding_provenance "
        "FROM projects ORDER BY id"
    ).fetchall()
    for row in rows:
        identity = _single_task_identity_from_row(row)
        conn.execute(
            f"UPDATE projects SET {PROJECT_IDENTITY_COLUMN}=? WHERE id=?",
            (_identity_json(identity), row[0]),
        )
    return len(rows)


def refresh_single_task_project_identities(database: str | Path) -> int:
    """Mirror completed catalog binding into the explicit union identity.

    Workspace bootstrap may bind a formerly-unbound legacy Project after the
    schema migration. This mirrors that already-recorded provenance; it never
    derives a package or rewrites a Chain identity.
    """

    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if not _migration_is_current(conn):
            raise ChainCatalogMigrationError("chain catalog must be migrated first")
        rows = conn.execute(
            "SELECT id,task_id,dataset_view_revision_id,task_contract_digest,"
            "model_package_ref_id,model_package_manifest_digest,binding_provenance,"
            "scientific_identity_json FROM projects ORDER BY id"
        ).fetchall()
        changed = 0
        for row in rows:
            current = json.loads(str(row[7]))
            if current.get("identity_kind") == "chain":
                continue
            identity = _single_task_identity_from_row(row)
            encoded = _identity_json(identity)
            if encoded != row[7]:
                conn.execute(
                    "UPDATE projects SET scientific_identity_json=? WHERE id=?",
                    (encoded, row[0]),
                )
                changed += 1
        _assert_current(conn)
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate_chain_catalog(database: str | Path) -> int:
    """Migrate additively; existing Project and snapshot payloads are not rewritten."""

    path = Path(database)
    migrate_workspace_catalog(path)
    conn = connect_sqlite(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _migration_is_current(conn):
            return 0
        existing = set(CHAIN_TABLE_COLUMNS) & _tables(conn)
        if existing:
            raise ChainCatalogMigrationError(
                f"chain catalog tables exist without migration marker: {sorted(existing)}"
            )
        if PROJECT_IDENTITY_COLUMN in _columns(conn, "projects"):
            raise ChainCatalogMigrationError(
                "Project identity column exists without chain catalog migration marker"
            )
        _create_tables(conn)
        conn.execute(
            f'ALTER TABLE projects ADD COLUMN "{PROJECT_IDENTITY_COLUMN}" TEXT'
        )
        migrated = _backfill_single_task_identity(conn)
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, _now()),
        )
        _assert_current(conn)
        conn.commit()
        return migrated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
