"""Transactional migration from task-specific candidates to canonical candidates.

The migration deliberately works on JSON dictionaries rather than application
schemas.  This keeps database upgrade available before the current application
models are imported and lets Store replace its candidate models atomically.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
import uuid
from typing import Any


MIGRATION_ID = "candidate-unification-v1"
MIGRATION_CHECKSUM = "nested-candidate-storage-v1"
CANDIDATE_SAFETY_MIGRATION_ID = "candidate-safety-v1"
CANDIDATE_SAFETY_MIGRATION_CHECKSUM = "candidate-revision-archive-v1"
HOT_PROJECT_ID = "hot-rolling-default"
ANNEALED_TASK_ID = "annealed-properties-v1"
HOT_TASK_ID = "hot-rolled-properties-v1"
Failpoint = Callable[[str], None]


class CandidateMigrationError(RuntimeError):
    """The database cannot be migrated without losing or inventing data."""


@dataclass(frozen=True)
class CandidateMigrationResult:
    status: str
    backup_path: Path | None
    annealed_candidates: int
    hot_rolling_candidates: int


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


def _json_object(raw: str, *, table: str, row_id: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CandidateMigrationError(f"{table} {row_id}: payload is not valid JSON") from exc
    if not isinstance(value, dict):
        raise CandidateMigrationError(f"{table} {row_id}: payload must be a JSON object")
    return value


def _annealed_payload(row: sqlite3.Row) -> dict[str, Any]:
    raise CandidateMigrationError(
        f"candidates {row['id']}: stored candidate belongs to the retired input contract; "
        "composition and process fields must be re-entered"
    )


def _hot_payload(row: sqlite3.Row) -> dict[str, Any]:
    raise CandidateMigrationError(
        f"hot_rolling_candidates {row['id']}: stored candidate belongs to the retired input contract; "
        "composition and process fields must be re-entered"
    )


def _create_common_tables(conn: sqlite3.Connection) -> None:
    statements = (
        "CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT 'annealed-properties-v1', target_values TEXT NOT NULL DEFAULT '{}', input_ranges TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '', decision_candidate_id TEXT NOT NULL DEFAULT '', decision_snapshot_id TEXT NOT NULL DEFAULT '', decision_note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '')",
        "CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1), archived_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS screening_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS actual_measurements (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, property TEXT NOT NULL, mean REAL NOT NULL, std REAL NOT NULL, replicates INTEGER NOT NULL, unit TEXT NOT NULL, experiment_no TEXT NOT NULL, measured_at TEXT, note TEXT NOT NULL, created_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS schema_migrations (id TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)",
    )
    for statement in statements:
        conn.execute(statement)


def _ensure_project_columns(conn: sqlite3.Connection) -> None:
    existing = _columns(conn, "projects")
    additions = (
        ("description", "TEXT NOT NULL DEFAULT ''"),
        ("purpose", "TEXT NOT NULL DEFAULT ''"),
        ("task_id", "TEXT NOT NULL DEFAULT 'annealed-properties-v1'"),
        ("target_values", "TEXT NOT NULL DEFAULT '{}'"),
        ("input_ranges", "TEXT NOT NULL DEFAULT '{}'"),
        ("notes", "TEXT NOT NULL DEFAULT ''"),
        ("decision_candidate_id", "TEXT NOT NULL DEFAULT ''"),
        ("decision_snapshot_id", "TEXT NOT NULL DEFAULT ''"),
        ("decision_note", "TEXT NOT NULL DEFAULT ''"),
        ("updated_at", "TEXT NOT NULL DEFAULT ''"),
    )
    for name, definition in additions:
        if name not in existing:
            conn.execute(f'ALTER TABLE projects ADD COLUMN "{name}" {definition}')
    conn.execute("UPDATE projects SET updated_at=created_at WHERE updated_at='' OR updated_at IS NULL")


def _ensure_actual_snapshot_column(conn: sqlite3.Connection) -> None:
    if "snapshot_id" not in _columns(conn, "actual_measurements"):
        conn.execute("ALTER TABLE actual_measurements ADD COLUMN snapshot_id TEXT NOT NULL DEFAULT ''")


def _ensure_default_project(conn: sqlite3.Connection) -> None:
    now = _now()
    conn.execute(
        "INSERT OR IGNORE INTO projects(id,name,description,purpose,task_id,target_values,input_ranges,notes,decision_candidate_id,decision_snapshot_id,decision_note,created_at,updated_at) VALUES ('default','焼鈍条件の候補検討','','','annealed-properties-v1','{}','{}','','','','',?,?)",
        (now, now),
    )


def _backup_database(source_path: Path, lock_conn: sqlite3.Connection, migration_id: str = MIGRATION_ID) -> Path:
    # BEGIN IMMEDIATE on lock_conn prevents a writer changing the source while a
    # second read connection takes a logical backup (including WAL content).
    suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    destination = source_path.with_name(f"{source_path.name}.pre-{migration_id}-{suffix}-{uuid.uuid4().hex[:8]}.bak")
    source: sqlite3.Connection | None = None
    target: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(source_path)
        target = sqlite3.connect(destination)
        source.backup(target)
        check = target.execute("PRAGMA quick_check").fetchone()
        if check is None or check[0] != "ok":
            raise CandidateMigrationError(f"backup integrity check failed: {check!r}")
    except Exception:
        if target is not None:
            target.close()
            target = None
        if source is not None:
            source.close()
            source = None
        if destination.exists():
            destination.unlink()
        lock_conn.rollback()
        raise
    finally:
        if target is not None:
            target.close()
        if source is not None:
            source.close()
    return destination


def _validate_references(conn: sqlite3.Connection, candidate_table: str) -> None:
    checks = (
        (
            f"SELECT s.id FROM snapshots s LEFT JOIN {candidate_table} c ON c.id=s.candidate_id WHERE c.id IS NULL LIMIT 1",
            "snapshot refers to a missing candidate",
        ),
        (
            f"SELECT a.id FROM actual_measurements a LEFT JOIN {candidate_table} c ON c.id=a.candidate_id WHERE c.id IS NULL LIMIT 1",
            "actual measurement refers to a missing candidate",
        ),
        (
            "SELECT a.id FROM actual_measurements a LEFT JOIN snapshots s ON s.id=a.snapshot_id AND s.candidate_id=a.candidate_id WHERE a.snapshot_id<>'' AND s.id IS NULL LIMIT 1",
            "actual measurement snapshot belongs to another or missing candidate",
        ),
        (
            f"SELECT p.id FROM projects p LEFT JOIN {candidate_table} c ON c.id=p.decision_candidate_id AND c.project_id=p.id WHERE p.decision_candidate_id<>'' AND c.id IS NULL LIMIT 1",
            "project decision candidate belongs to another or missing project",
        ),
        (
            "SELECT p.id FROM projects p LEFT JOIN snapshots s ON s.id=p.decision_snapshot_id AND s.candidate_id=p.decision_candidate_id WHERE p.decision_candidate_id<>'' AND s.id IS NULL LIMIT 1",
            "project decision snapshot belongs to another or missing candidate",
        ),
    )
    for sql, message in checks:
        row = conn.execute(sql).fetchone()
        if row is not None:
            raise CandidateMigrationError(f"{message}: {row[0]}")


def _assert_canonical_rows(conn: sqlite3.Connection) -> None:
    for row in conn.execute("SELECT id, project_id, payload FROM candidates"):
        payload = _json_object(row["payload"], table="candidates", row_id=row["id"])
        if set(payload) != {"name", "inputs", "provenance"} or not isinstance(payload.get("inputs"), dict):
            raise CandidateMigrationError(f"candidates {row['id']}: migration marker exists but payload is not nested candidate storage")
        if set(payload["inputs"]) != {"composition", "process", "categorical", "heat_pattern"}:
            raise CandidateMigrationError(f"candidates {row['id']}: nested inputs are incomplete")
        project = conn.execute("SELECT task_id FROM projects WHERE id=?", (row["project_id"],)).fetchone()
        if project is None:
            raise CandidateMigrationError(f"candidates {row['id']}: project is missing")


def _candidate_safety_is_current(conn: sqlite3.Connection) -> bool:
    if "schema_migrations" not in _tables(conn):
        return False
    applied = conn.execute(
        "SELECT checksum FROM schema_migrations WHERE id=?",
        (CANDIDATE_SAFETY_MIGRATION_ID,),
    ).fetchone()
    if applied is None:
        return False
    if applied[0] != CANDIDATE_SAFETY_MIGRATION_CHECKSUM:
        raise CandidateMigrationError("candidate safety migration checksum does not match this application")
    columns = _columns(conn, "candidates")
    if not {"revision", "archived_at"} <= columns:
        raise CandidateMigrationError("candidate safety migration is marked complete but columns are missing")
    invalid = conn.execute("SELECT id FROM candidates WHERE typeof(revision) <> 'integer' OR revision < 1 LIMIT 1").fetchone()
    if invalid is not None:
        raise CandidateMigrationError(f"candidate {invalid[0]} has an invalid revision")
    for row in conn.execute("SELECT id, archived_at FROM candidates WHERE archived_at IS NOT NULL"):
        try:
            datetime.fromisoformat(row["archived_at"])
        except (TypeError, ValueError) as exc:
            raise CandidateMigrationError(f"candidate {row['id']} has an invalid archived_at") from exc
    return True


def _apply_candidate_safety_schema(conn: sqlite3.Connection) -> None:
    columns = _columns(conn, "candidates")
    if "revision" not in columns:
        conn.execute("ALTER TABLE candidates ADD COLUMN revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1)")
    if "archived_at" not in columns:
        conn.execute("ALTER TABLE candidates ADD COLUMN archived_at TEXT")
    conn.execute(
        "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
        (CANDIDATE_SAFETY_MIGRATION_ID, CANDIDATE_SAFETY_MIGRATION_CHECKSUM, _now()),
    )


def migrate_candidate_storage(
    database: str | Path,
    *,
    failpoint: Failpoint | None = None,
) -> CandidateMigrationResult:
    """Create current storage or atomically migrate legacy candidate tables.

    Store should call this once at the start of ``_init`` and should not create
    ``hot_rolling_candidates`` itself.  ``failpoint`` is solely for exercising
    rollback behavior in focused tests.
    """

    path = Path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    callback = failpoint or (lambda _name: None)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    backup_path: Path | None = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        tables = _tables(conn)
        if is_new or not tables:
            _create_common_tables(conn)
            now = _now()
            conn.execute(
                "INSERT OR IGNORE INTO projects(id,name,description,purpose,task_id,target_values,input_ranges,notes,decision_candidate_id,decision_snapshot_id,decision_note,created_at,updated_at) VALUES ('default','焼鈍条件の候補検討','','','annealed-properties-v1','{}','{}','','','','',?,?)",
                (now, now),
            )
            conn.execute(
                "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
                (MIGRATION_ID, MIGRATION_CHECKSUM, now),
            )
            conn.execute(
                "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
                (CANDIDATE_SAFETY_MIGRATION_ID, CANDIDATE_SAFETY_MIGRATION_CHECKSUM, now),
            )
            callback("before_commit")
            conn.commit()
            return CandidateMigrationResult("initialized", None, 0, 0)

        if "schema_migrations" in tables:
            applied = conn.execute("SELECT checksum FROM schema_migrations WHERE id=?", (MIGRATION_ID,)).fetchone()
            if applied is not None:
                if applied[0] != MIGRATION_CHECKSUM:
                    raise CandidateMigrationError("candidate migration checksum does not match this application")
                if "hot_rolling_candidates" in tables:
                    raise CandidateMigrationError("candidate migration is marked complete but the legacy hot table exists")
                _assert_canonical_rows(conn)
                _validate_references(conn, "candidates")
                if not _candidate_safety_is_current(conn):
                    backup_path = _backup_database(path, conn, CANDIDATE_SAFETY_MIGRATION_ID)
                    callback("after_safety_backup")
                    _apply_candidate_safety_schema(conn)
                    callback("before_safety_commit")
                    conn.commit()
                    annealed_count = conn.execute("SELECT COUNT(*) FROM candidates c JOIN projects p ON p.id=c.project_id WHERE p.task_id=?", (ANNEALED_TASK_ID,)).fetchone()[0]
                    hot_count = conn.execute("SELECT COUNT(*) FROM candidates c JOIN projects p ON p.id=c.project_id WHERE p.task_id=?", (HOT_TASK_ID,)).fetchone()[0]
                    return CandidateMigrationResult("migrated-safety", backup_path, annealed_count, hot_count)
                conn.commit()
                annealed_count = conn.execute("SELECT COUNT(*) FROM candidates c JOIN projects p ON p.id=c.project_id WHERE p.task_id=?", (ANNEALED_TASK_ID,)).fetchone()[0]
                hot_count = conn.execute("SELECT COUNT(*) FROM candidates c JOIN projects p ON p.id=c.project_id WHERE p.task_id=?", (HOT_TASK_ID,)).fetchone()[0]
                return CandidateMigrationResult("already-current", None, annealed_count, hot_count)

        if "projects" not in tables:
            raise CandidateMigrationError("candidate database is missing required projects table")

        backup_path = _backup_database(path, conn)
        callback("after_backup")
        _create_common_tables(conn)
        _ensure_project_columns(conn)
        _ensure_actual_snapshot_column(conn)
        _ensure_default_project(conn)

        candidate_rows = conn.execute("SELECT * FROM candidates ORDER BY id").fetchall()
        hot_rows = (
            conn.execute("SELECT * FROM hot_rolling_candidates ORDER BY id").fetchall()
            if "hot_rolling_candidates" in _tables(conn)
            else []
        )
        candidate_ids = {row["id"] for row in candidate_rows}
        hot_ids = {row["id"] for row in hot_rows}
        collisions = sorted(candidate_ids & hot_ids)
        if collisions:
            raise CandidateMigrationError(f"candidate IDs collide across legacy tables: {collisions}")

        annealed = [(row, _annealed_payload(row)) for row in candidate_rows]
        hot = [(row, _hot_payload(row)) for row in hot_rows]
        for row, _payload in annealed:
            project = conn.execute("SELECT task_id FROM projects WHERE id=?", (row["project_id"],)).fetchone()
            if project is None:
                raise CandidateMigrationError(f"candidates {row['id']}: project is missing")
            if project[0] != ANNEALED_TASK_ID:
                raise CandidateMigrationError(f"candidates {row['id']}: legacy annealed payload belongs to {project[0]}")

        if hot:
            project = conn.execute("SELECT task_id FROM projects WHERE id=?", (HOT_PROJECT_ID,)).fetchone()
            if project is not None and project[0] != HOT_TASK_ID:
                raise CandidateMigrationError(f"reserved project {HOT_PROJECT_ID} has incompatible task {project[0]}")
            if project is None:
                created_at = min(str(row["created_at"]) for row, _ in hot)
                updated_at = max(str(row["updated_at"]) for row, _ in hot)
                conn.execute(
                    "INSERT INTO projects(id,name,description,purpose,task_id,target_values,input_ranges,notes,decision_candidate_id,decision_snapshot_id,decision_note,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (HOT_PROJECT_ID, "熱延条件の候補検討", "", "", HOT_TASK_ID, "{}", "{}", "", "", "", "", created_at, updated_at),
                )

        if "candidates_next" in _tables(conn):
            raise CandidateMigrationError("unexpected candidates_next table indicates an incomplete external migration")
        conn.execute("CREATE TABLE candidates_next (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1), archived_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        callback("after_next_create")
        conn.executemany(
            "INSERT INTO candidates_next(id,project_id,name,payload,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            [
                (row["id"], row["project_id"], row["name"], json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), row["created_at"], row["updated_at"])
                for row, payload in annealed
            ],
        )
        callback("after_annealed_copy")
        conn.executemany(
            "INSERT INTO candidates_next(id,project_id,name,payload,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            [
                (row["id"], HOT_PROJECT_ID, row["name"], json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")), row["created_at"], row["updated_at"])
                for row, payload in hot
            ],
        )
        callback("after_hot_copy")

        expected = len(annealed) + len(hot)
        actual = int(conn.execute("SELECT COUNT(*) FROM candidates_next").fetchone()[0])
        if actual != expected:
            raise CandidateMigrationError(f"candidate copy count differs: expected={expected}, actual={actual}")
        copied = {row[0] for row in conn.execute("SELECT id FROM candidates_next")}
        if copied != candidate_ids | hot_ids:
            raise CandidateMigrationError("candidate IDs differ after copy")
        _validate_references(conn, "candidates_next")
        callback("after_reference_check")

        conn.execute("DROP TABLE candidates")
        conn.execute("ALTER TABLE candidates_next RENAME TO candidates")
        if "hot_rolling_candidates" in _tables(conn):
            remaining = int(conn.execute("SELECT COUNT(*) FROM hot_rolling_candidates").fetchone()[0])
            if remaining != len(hot):
                raise CandidateMigrationError("legacy hot candidate table changed during migration")
            conn.execute("DROP TABLE hot_rolling_candidates")
        callback("after_hot_drop")
        _assert_canonical_rows(conn)
        _validate_references(conn, "candidates")
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, _now()),
        )
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (CANDIDATE_SAFETY_MIGRATION_ID, CANDIDATE_SAFETY_MIGRATION_CHECKSUM, _now()),
        )
        callback("before_commit")
        conn.commit()
        return CandidateMigrationResult("migrated", backup_path, len(annealed), len(hot))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
