"""Additive SQLite migration for Data Library and immutable Project bindings.

This module owns only storage identity.  Discovering the current source,
profile, task contract, and active package is deliberately left to the
application bootstrap, because a database migration cannot reconstruct those
facts by itself without inventing history.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from decision_workbench.persistence.sqlite_connection import connect_sqlite

from decision_workbench.persistence.candidate_migration import migrate_candidate_storage


MIGRATION_ID = "workspace-catalog-v1"
MIGRATION_CHECKSUM = "data-library-project-bindings-v1"
Failpoint = Callable[[str], None]


class WorkspaceCatalogMigrationError(RuntimeError):
    """The workspace catalog schema is inconsistent with this migration."""


@dataclass(frozen=True)
class WorkspaceCatalogMigrationResult:
    status: str
    legacy_projects: int


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


CATALOG_TABLE_COLUMNS = {
    "data_assets": {
        "id", "sha256", "original_filename", "media_type", "locator_kind",
        "locator", "created_at", "archived_at",
    },
    "dataset_profile_revisions": {
        "id", "profile_id", "revision", "name", "profile_digest",
        "canonical_contract_digest", "effective_profile_json", "created_at",
        "archived_at",
    },
    "dataset_revisions": {
        "id", "data_asset_id", "profile_revision_id",
        "canonicalization_contract_digest", "dataset_digest", "created_at",
        "archived_at",
    },
    "dataset_view_revisions": {
        "id", "view_id", "revision", "name", "kind", "view_digest",
        "created_at", "archived_at",
    },
    "dataset_view_members": {
        "dataset_view_revision_id", "dataset_revision_id", "ordinal",
        "cohort_key", "cohort_label", "provenance_json",
    },
    "model_package_refs": {
        "id", "package_id", "task_id", "task_contract_digest",
        "manifest_digest", "locator", "manifest_json", "created_at",
        "archived_at",
    },
    "project_series": {
        "id", "name", "description", "created_at", "updated_at", "archived_at",
    },
}

PROJECT_BINDING_COLUMNS = {
    "dataset_view_revision_id",
    "task_contract_digest",
    "model_package_ref_id",
    "model_package_manifest_digest",
    "project_series_id",
    "predecessor_project_id",
    "continuation_reason",
    "binding_provenance",
    "binding_migrated_at",
}


def _assert_current_schema(conn: sqlite3.Connection) -> None:
    tables = _tables(conn)
    missing_tables = CATALOG_TABLE_COLUMNS.keys() - tables
    if missing_tables:
        raise WorkspaceCatalogMigrationError(
            f"workspace catalog migration is marked complete but tables are missing: {sorted(missing_tables)}"
        )
    for table, expected in CATALOG_TABLE_COLUMNS.items():
        missing = expected - _columns(conn, table)
        if missing:
            raise WorkspaceCatalogMigrationError(
                f"workspace catalog migration is marked complete but {table} columns are missing: {sorted(missing)}"
            )
    missing_project_columns = PROJECT_BINDING_COLUMNS - _columns(conn, "projects")
    if missing_project_columns:
        raise WorkspaceCatalogMigrationError(
            "workspace catalog migration is marked complete but projects columns are missing: "
            f"{sorted(missing_project_columns)}"
        )


def _migration_is_current(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE id=?",
        (MIGRATION_ID,),
    ).fetchone()
    if row is None:
        return False
    if row[0] != MIGRATION_CHECKSUM:
        raise WorkspaceCatalogMigrationError(
            "workspace catalog migration checksum does not match this application"
        )
    _assert_current_schema(conn)
    return True


def _create_catalog_tables(conn: sqlite3.Connection) -> None:
    statements = (
        "CREATE TABLE data_assets (id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, original_filename TEXT NOT NULL, media_type TEXT NOT NULL, locator_kind TEXT NOT NULL CHECK(locator_kind IN ('managed','bundled')), locator TEXT NOT NULL, created_at TEXT NOT NULL, archived_at TEXT)",
        "CREATE TABLE dataset_profile_revisions (id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), name TEXT NOT NULL, profile_digest TEXT NOT NULL UNIQUE, canonical_contract_digest TEXT NOT NULL, effective_profile_json TEXT NOT NULL, created_at TEXT NOT NULL, archived_at TEXT, UNIQUE(profile_id,revision))",
        "CREATE TABLE dataset_revisions (id TEXT PRIMARY KEY, data_asset_id TEXT NOT NULL REFERENCES data_assets(id), profile_revision_id TEXT NOT NULL REFERENCES dataset_profile_revisions(id), canonicalization_contract_digest TEXT NOT NULL, dataset_digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, archived_at TEXT, UNIQUE(data_asset_id,profile_revision_id,canonicalization_contract_digest))",
        "CREATE TABLE dataset_view_revisions (id TEXT PRIMARY KEY, view_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision >= 1), name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('single','cohort_comparison')), view_digest TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, archived_at TEXT, UNIQUE(view_id,revision))",
        "CREATE TABLE dataset_view_members (dataset_view_revision_id TEXT NOT NULL REFERENCES dataset_view_revisions(id), dataset_revision_id TEXT NOT NULL REFERENCES dataset_revisions(id), ordinal INTEGER NOT NULL CHECK(ordinal >= 0), cohort_key TEXT NOT NULL DEFAULT '', cohort_label TEXT NOT NULL DEFAULT '', provenance_json TEXT NOT NULL DEFAULT '{}', PRIMARY KEY(dataset_view_revision_id,dataset_revision_id), UNIQUE(dataset_view_revision_id,ordinal))",
        "CREATE TABLE model_package_refs (id TEXT PRIMARY KEY, package_id TEXT NOT NULL, task_id TEXT NOT NULL, task_contract_digest TEXT NOT NULL, manifest_digest TEXT NOT NULL, locator TEXT NOT NULL, manifest_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, archived_at TEXT, UNIQUE(package_id,manifest_digest))",
        "CREATE TABLE project_series (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT)",
    )
    for statement in statements:
        conn.execute(statement)
    indexes = (
        "CREATE INDEX idx_dataset_revisions_asset ON dataset_revisions(data_asset_id)",
        "CREATE INDEX idx_dataset_revisions_profile ON dataset_revisions(profile_revision_id)",
        "CREATE INDEX idx_dataset_view_members_dataset ON dataset_view_members(dataset_revision_id)",
        "CREATE INDEX idx_model_package_refs_task ON model_package_refs(task_id)",
        "CREATE INDEX idx_project_series_archived ON project_series(archived_at)",
    )
    for statement in indexes:
        conn.execute(statement)


def _add_project_binding_columns(conn: sqlite3.Connection) -> None:
    additions = (
        ("dataset_view_revision_id", "TEXT"),
        ("task_contract_digest", "TEXT NOT NULL DEFAULT ''"),
        ("model_package_ref_id", "TEXT"),
        ("model_package_manifest_digest", "TEXT NOT NULL DEFAULT ''"),
        ("project_series_id", "TEXT"),
        ("predecessor_project_id", "TEXT"),
        ("continuation_reason", "TEXT NOT NULL DEFAULT ''"),
        ("binding_provenance", "TEXT NOT NULL DEFAULT 'unbound_legacy'"),
        ("binding_migrated_at", "TEXT"),
    )
    existing = _columns(conn, "projects")
    for name, definition in additions:
        if name not in existing:
            conn.execute(f'ALTER TABLE projects ADD COLUMN "{name}" {definition}')


def migrate_workspace_catalog(
    database: str | Path,
    *,
    failpoint: Failpoint | None = None,
) -> WorkspaceCatalogMigrationResult:
    """Create the catalog schema without rewriting existing workspace records.

    Candidate storage is brought current first, so invoking this migration on a
    new database is safe and cannot leave a schema that the older candidate
    migration mistakes for a legacy workspace.
    """

    path = Path(database)
    migrate_candidate_storage(path)
    callback = failpoint or (lambda _name: None)
    conn = connect_sqlite(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _migration_is_current(conn):
            legacy_projects = int(
                conn.execute(
                    "SELECT COUNT(*) FROM projects WHERE binding_provenance='unbound_legacy'"
                ).fetchone()[0]
            )
            conn.commit()
            return WorkspaceCatalogMigrationResult("already-current", legacy_projects)

        catalog_tables = set(CATALOG_TABLE_COLUMNS) & _tables(conn)
        if catalog_tables:
            raise WorkspaceCatalogMigrationError(
                "workspace catalog tables exist without the migration marker: "
                f"{sorted(catalog_tables)}"
            )

        _create_catalog_tables(conn)
        _add_project_binding_columns(conn)
        legacy_projects = int(conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0])
        callback("before_commit")
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, _now()),
        )
        _assert_current_schema(conn)
        conn.commit()
        return WorkspaceCatalogMigrationResult("migrated", legacy_projects)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
