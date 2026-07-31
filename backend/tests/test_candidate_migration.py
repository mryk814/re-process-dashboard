from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from decision_workbench.persistence.candidate_migration import (
    CandidateMigrationError,
    HOT_PROJECT_ID,
    MIGRATION_CHECKSUM,
    MIGRATION_ID,
    migrate_candidate_storage,
)


ANNEALED_PAYLOAD = {
    "name": "既存焼鈍候補",
    "composition": {"C": 0.08, "Mn": 1.5},
    "thickness_mm": 1.4,
    "line_speed_m_min": 100.0,
    "coating": "GI",
    "heat_pattern": [
        {"time_s": 0.0, "temperature_c": 25.0, "segment_start": False, "stage_name": None},
        {"time_s": 300.0, "temperature_c": 810.0, "segment_start": True, "stage_name": "均熱"},
    ],
}
HOT_PAYLOAD = {
    "name": "既存熱延候補",
    "composition": {"C": 0.1, "Mn": 1.8},
    "reheat_temperature_c": 1180.0,
    "hold_time_min": 30.0,
    "finish_temperature_c": 900.0,
    "coiling_temperature_c": 620.0,
    "cooling_rate_c_s": 35.0,
    "entry_thickness_mm": 35.0,
    "exit_thickness_mm": 3.5,
    "route": "B",
}
HOT_FIXED_CONTEXT_PAYLOAD = {
    **HOT_PAYLOAD,
    "equipment": "HR-LINE-1",
    "test_direction": "L",
}


def _legacy_database(path: Path, *, collision: bool = False, broken_reference: bool = False) -> None:
    with sqlite3.connect(path) as conn:
        for statement in (
            "CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT 'annealed-properties-v1', target_values TEXT NOT NULL DEFAULT '{}', input_ranges TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '', decision_candidate_id TEXT NOT NULL DEFAULT '', decision_snapshot_id TEXT NOT NULL DEFAULT '', decision_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '')",
            "CREATE TABLE candidates (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE TABLE hot_rolling_candidates (id TEXT PRIMARY KEY, name TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE TABLE snapshots (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
            "CREATE TABLE screening_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
            "CREATE TABLE actual_measurements (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, property TEXT NOT NULL, mean REAL NOT NULL, std REAL NOT NULL, replicates INTEGER NOT NULL, unit TEXT NOT NULL, experiment_no TEXT NOT NULL, measured_at TEXT, note TEXT NOT NULL, created_at TEXT NOT NULL)",
        ):
            conn.execute(statement)
        conn.execute(
            "INSERT INTO projects VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("default", "既存検討", "説明", "目的", "annealed-properties-v1", '{"TS":500}', "{}", "メモ", "anneal-1", "snapshot-1", "採用理由", "2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO candidates VALUES (?,?,?,?,?,?)",
            ("anneal-1", "default", ANNEALED_PAYLOAD["name"], json.dumps(ANNEALED_PAYLOAD, ensure_ascii=False), "2026-01-02T00:00:00+00:00", "2026-01-03T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO hot_rolling_candidates VALUES (?,?,?,?,?)",
            ("anneal-1" if collision else "hot-1", HOT_PAYLOAD["name"], json.dumps(HOT_PAYLOAD, ensure_ascii=False), "2026-01-04T00:00:00+00:00", "2026-01-05T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO snapshots VALUES (?,?,?,?)",
            ("snapshot-1", "missing" if broken_reference else "anneal-1", '{"snapshot_schema_version":"prediction-snapshot-v1"}', "2026-01-06T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO actual_measurements VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("actual-1", "anneal-1", "snapshot-1", "TS", 505.0, 3.0, 2, "MPa", "EXP-1", None, "note", "2026-01-07T00:00:00+00:00"),
        )


def _ledger(path: Path) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(path) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {table: conn.execute(f'SELECT * FROM "{table}" ORDER BY 1').fetchall() for table in tables}


def test_legacy_candidate_migration_is_rejected_without_mutation(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    _legacy_database(database)
    before = _ledger(database)

    with pytest.raises(CandidateMigrationError, match="retired input contract"):
        migrate_candidate_storage(database)
    assert _ledger(database) == before


def test_migration_rejects_legacy_fixed_context_even_when_it_matches(tmp_path: Path) -> None:
    database = tmp_path / "legacy-fixed-context.db"
    _legacy_database(database)
    with sqlite3.connect(database) as conn:
        conn.execute(
            "UPDATE hot_rolling_candidates SET payload=? WHERE id='hot-1'",
            (json.dumps(HOT_FIXED_CONTEXT_PAYLOAD, ensure_ascii=False),),
        )

    with pytest.raises(CandidateMigrationError, match="retired input contract"):
        migrate_candidate_storage(database)


def test_migration_rejects_legacy_fixed_context_mismatch_without_mutation(tmp_path: Path) -> None:
    database = tmp_path / "legacy-fixed-context-mismatch.db"
    _legacy_database(database)
    with sqlite3.connect(database) as conn:
        payload = {**HOT_FIXED_CONTEXT_PAYLOAD, "test_direction": "T"}
        conn.execute(
            "UPDATE hot_rolling_candidates SET payload=? WHERE id='hot-1'",
            (json.dumps(payload, ensure_ascii=False),),
        )
    before = _ledger(database)

    with pytest.raises(CandidateMigrationError, match="retired input contract"):
        migrate_candidate_storage(database)

    assert _ledger(database) == before


@pytest.mark.parametrize(
    "point",
    ["after_backup", "after_next_create", "after_annealed_copy", "after_hot_copy", "after_reference_check", "after_hot_drop", "before_commit"],
)
def test_failure_rolls_back_every_database_change_and_retry_succeeds(tmp_path: Path, point: str) -> None:
    database = tmp_path / f"rollback-{point}.db"
    _legacy_database(database)
    before = _ledger(database)

    def fail(name: str) -> None:
        if name == point:
            raise sqlite3.OperationalError(f"injected at {name}")

    expected_error = sqlite3.OperationalError if point == "after_backup" else CandidateMigrationError
    with pytest.raises(expected_error):
        migrate_candidate_storage(database, failpoint=fail)

    assert _ledger(database) == before
    with sqlite3.connect(database) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "hot_rolling_candidates" in names
        assert "candidates_next" not in names
        assert "schema_migrations" not in names


@pytest.mark.parametrize("failure", ["collision", "broken_reference"])
def test_ambiguous_or_orphaned_legacy_data_aborts_without_mutation(tmp_path: Path, failure: str) -> None:
    database = tmp_path / f"invalid-{failure}.db"
    _legacy_database(database, collision=failure == "collision", broken_reference=failure == "broken_reference")
    before = _ledger(database)

    with pytest.raises(CandidateMigrationError):
        migrate_candidate_storage(database)

    assert _ledger(database) == before


def test_new_database_is_created_directly_at_current_schema(tmp_path: Path) -> None:
    database = tmp_path / "new.db"

    result = migrate_candidate_storage(database)

    assert result.status == "initialized"
    with sqlite3.connect(database) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "hot_rolling_candidates" not in tables
        assert {"projects", "candidates", "snapshots", "screening_runs", "actual_measurements", "schema_migrations"} <= tables
        assert conn.execute("SELECT task_id FROM projects WHERE id='default'").fetchone()[0] == "annealed-properties-v1"
        assert conn.execute("SELECT display_decimals FROM projects WHERE id='default'").fetchone()[0] == "{}"


def _unification_v1_database(path: Path) -> None:
    payload = {
        "name": "既存共通候補",
        "inputs": {"composition": {}, "process": {}, "categorical": {}, "heat_pattern": None},
        "provenance": {"source_kind": "manual", "source_ref": None},
    }
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL, target_values TEXT NOT NULL DEFAULT '{}', input_ranges TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '', decision_candidate_id TEXT NOT NULL DEFAULT '', decision_snapshot_id TEXT NOT NULL DEFAULT '', decision_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE candidates (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE snapshots (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE screening_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE actual_measurements (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, property TEXT NOT NULL, mean REAL NOT NULL, std REAL NOT NULL, replicates INTEGER NOT NULL, unit TEXT NOT NULL, experiment_no TEXT NOT NULL, measured_at TEXT, note TEXT NOT NULL, created_at TEXT NOT NULL)")
        conn.execute("CREATE TABLE schema_migrations (id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)")
        conn.execute("INSERT INTO projects VALUES ('default','既存','','','annealed-properties-v1','{}','{}','','','','','2026-01-01T00:00:00+00:00','2026-01-01T00:00:00+00:00')")
        conn.execute("INSERT INTO candidates VALUES (?,?,?,?,?,?)", ("candidate-1", "default", "既存共通候補", json.dumps(payload, ensure_ascii=False), "2026-01-02T00:00:00+00:00", "2026-01-03T00:00:00+00:00"))
        conn.execute("INSERT INTO schema_migrations VALUES (?,?,?)", (MIGRATION_ID, MIGRATION_CHECKSUM, "2026-01-04T00:00:00+00:00"))


def test_candidate_safety_migration_backs_up_preserves_and_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "unification-v1.db"
    _unification_v1_database(database)

    result = migrate_candidate_storage(database)

    assert result.status == "migrated-safety"
    assert result.backup_path is not None and result.backup_path.exists()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM candidates WHERE id='candidate-1'").fetchone()
        assert row is not None
        assert row["revision"] == 1
        assert row["archived_at"] is None
        assert row["created_at"] == "2026-01-02T00:00:00+00:00"
        assert row["updated_at"] == "2026-01-03T00:00:00+00:00"
        assert json.loads(row["payload"])["name"] == "既存共通候補"
        assert conn.execute("SELECT display_decimals FROM projects WHERE id='default'").fetchone()[0] == "{}"
    assert migrate_candidate_storage(database).status == "already-current"
    with sqlite3.connect(database) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM candidates WHERE id='candidate-1'").fetchone()[0])
        payload["inputs"]["heat_time_basis"] = "elapsed_time"
        conn.execute(
            "UPDATE candidates SET payload=? WHERE id='candidate-1'",
            (json.dumps(payload, ensure_ascii=False),),
        )
    assert migrate_candidate_storage(database).status == "already-current"
    assert len(list(tmp_path.glob("*.pre-candidate-safety-v1-*.bak"))) == 1


def test_candidate_safety_migration_rolls_back_before_commit(tmp_path: Path) -> None:
    database = tmp_path / "safety-rollback.db"
    _unification_v1_database(database)

    with pytest.raises(sqlite3.OperationalError, match="injected"):
        migrate_candidate_storage(
            database,
            failpoint=lambda point: (_ for _ in ()).throw(sqlite3.OperationalError("injected")) if point == "before_safety_commit" else None,
        )

    with sqlite3.connect(database) as conn:
        assert "revision" not in {row[1] for row in conn.execute("PRAGMA table_info(candidates)")}
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE id='candidate-safety-v1'").fetchone() is None
    assert migrate_candidate_storage(database).status == "migrated-safety"
