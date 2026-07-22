from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from material_workbench.candidate_migration import (
    CANDIDATE_SAFETY_MIGRATION_ID,
    MIGRATION_ID as CANDIDATE_MIGRATION_ID,
    migrate_candidate_storage,
)
from material_workbench.workspace_catalog_migration import (
    CATALOG_TABLE_COLUMNS,
    MIGRATION_CHECKSUM,
    MIGRATION_ID,
    PROJECT_BINDING_COLUMNS,
    WorkspaceCatalogMigrationError,
    migrate_workspace_catalog,
)


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')}


def test_catalog_migration_preserves_workspace_and_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "workspace.db"
    migrate_candidate_storage(database)
    candidate_payload = {
        "name": "既存候補",
        "inputs": {"composition": {}, "process": {}, "categorical": {}, "heat_pattern": {}},
        "provenance": {"source_kind": "manual"},
    }
    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (
                "candidate-1",
                "default",
                "既存候補",
                json.dumps(candidate_payload, ensure_ascii=False),
                "2026-07-21T00:00:00+00:00",
                "2026-07-21T00:00:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO snapshots VALUES (?,?,?,?)",
            ("snapshot-1", "candidate-1", '{"prediction":"preserve"}', "2026-07-21T01:00:00+00:00"),
        )
        conn.execute(
            "UPDATE projects SET decision_candidate_id='candidate-1', decision_snapshot_id='snapshot-1' WHERE id='default'"
        )
    first = migrate_workspace_catalog(database)

    assert first.status == "migrated"
    assert first.legacy_projects == 1
    with sqlite3.connect(database) as conn:
        tables = _tables(conn)
        assert set(CATALOG_TABLE_COLUMNS) <= tables
        assert PROJECT_BINDING_COLUMNS <= _columns(conn, "projects")
        project = conn.execute(
            "SELECT name, task_id, dataset_view_revision_id, model_package_ref_id, "
            "binding_provenance, binding_migrated_at FROM projects WHERE id='default'"
        ).fetchone()
        assert project == (
            "焼鈍条件の候補検討",
            "annealed-properties-v1",
            None,
            None,
            "unbound_legacy",
            None,
        )
        migrations = dict(conn.execute("SELECT id,checksum FROM schema_migrations"))
        assert migrations[MIGRATION_ID] == MIGRATION_CHECKSUM
        assert CANDIDATE_MIGRATION_ID in migrations
        assert CANDIDATE_SAFETY_MIGRATION_ID in migrations
        assert conn.execute("SELECT payload FROM candidates WHERE id='candidate-1'").fetchone()[0] == json.dumps(
            candidate_payload, ensure_ascii=False
        )
        assert conn.execute("SELECT payload FROM snapshots WHERE id='snapshot-1'").fetchone()[0] == '{"prediction":"preserve"}'
        assert conn.execute(
            "SELECT decision_candidate_id,decision_snapshot_id FROM projects WHERE id='default'"
        ).fetchone() == ("candidate-1", "snapshot-1")

    second = migrate_workspace_catalog(database)
    assert second.status == "already-current"
    assert second.legacy_projects == 1
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations WHERE id=?", (MIGRATION_ID,)).fetchone()[0] == 1


def test_catalog_relations_represent_revisioned_assets_views_packages_and_series(tmp_path: Path) -> None:
    database = tmp_path / "relations.db"
    migrate_workspace_catalog(database)

    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            "INSERT INTO data_assets VALUES (?,?,?,?,?,?,?,NULL)",
            ("asset-1", "a" * 64, "source.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "managed", "library/asset-1.xlsx", "2026-07-22T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO dataset_profile_revisions VALUES (?,?,?,?,?,?,?,?,NULL)",
            ("profile-r1", "profile", 1, "Profile", "profile-digest", "contract-v1", "{}", "2026-07-22T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO dataset_revisions VALUES (?,?,?,?,?,?,NULL)",
            ("dataset-r1", "asset-1", "profile-r1", "contract-v1", "dataset-digest", "2026-07-22T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO dataset_view_revisions VALUES (?,?,?,?,?,?,?,NULL)",
            ("view-r1", "view", 1, "設備A", "single", "view-digest", "2026-07-22T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO dataset_view_members(dataset_view_revision_id,dataset_revision_id,ordinal) VALUES (?,?,?)",
            ("view-r1", "dataset-r1", 0),
        )
        conn.execute(
            "INSERT INTO model_package_refs(id,package_id,task_id,task_contract_digest,manifest_digest,locator,created_at) VALUES (?,?,?,?,?,?,?)",
            ("package-ref-1", "package-1", "annealed-properties-v1", "task-digest", "manifest-digest", "models/packages/package-1", "2026-07-22T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO project_series VALUES (?,?,?,?,?,NULL)",
            ("series-1", "一連の検討", "", "2026-07-22T00:00:00+00:00", "2026-07-22T00:00:00+00:00"),
        )
        conn.execute(
            "UPDATE projects SET dataset_view_revision_id=?, task_contract_digest=?, model_package_ref_id=?, model_package_manifest_digest=?, project_series_id=?, continuation_reason=?, binding_provenance=?, binding_migrated_at=? WHERE id='default'",
            ("view-r1", "task-digest", "package-ref-1", "manifest-digest", "series-1", "data_added", "assumed_current_at_upgrade", "2026-07-22T00:00:00+00:00"),
        )

        row = conn.execute(
            "SELECT dataset_view_revision_id,model_package_ref_id,project_series_id,binding_provenance FROM projects WHERE id='default'"
        ).fetchone()
        assert row == ("view-r1", "package-ref-1", "series-1", "assumed_current_at_upgrade")


def test_catalog_migration_rolls_back_catalog_changes(tmp_path: Path) -> None:
    database = tmp_path / "rollback.db"

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        migrate_workspace_catalog(
            database,
            failpoint=lambda point: (_ for _ in ()).throw(sqlite3.OperationalError("injected"))
            if point == "before_commit"
            else None,
        )

    with sqlite3.connect(database) as conn:
        assert not (set(CATALOG_TABLE_COLUMNS) & _tables(conn))
        assert not (PROJECT_BINDING_COLUMNS & _columns(conn, "projects"))
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE id=?", (MIGRATION_ID,)).fetchone() is None

    assert migrate_workspace_catalog(database).status == "migrated"


def test_catalog_migration_rejects_checksum_mismatch_without_mutation(tmp_path: Path) -> None:
    database = tmp_path / "checksum.db"
    migrate_workspace_catalog(database)
    with sqlite3.connect(database) as conn:
        conn.execute("UPDATE schema_migrations SET checksum='different' WHERE id=?", (MIGRATION_ID,))

    with pytest.raises(WorkspaceCatalogMigrationError, match="checksum"):
        migrate_workspace_catalog(database)

    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT checksum FROM schema_migrations WHERE id=?", (MIGRATION_ID,)).fetchone()[0] == "different"
