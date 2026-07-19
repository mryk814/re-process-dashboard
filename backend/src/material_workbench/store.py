from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .schemas import ActualMeasurement, ActualMeasurementInput, Candidate, CandidateInput, Project, ProjectInput


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', purpose TEXT NOT NULL DEFAULT '', task_id TEXT NOT NULL DEFAULT 'annealed-properties-v1', target_values TEXT NOT NULL DEFAULT '{}', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL DEFAULT '');
                CREATE TABLE IF NOT EXISTS candidates (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS screening_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS actual_measurements (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, snapshot_id TEXT NOT NULL, property TEXT NOT NULL, mean REAL NOT NULL, std REAL NOT NULL, replicates INTEGER NOT NULL, unit TEXT NOT NULL, experiment_no TEXT NOT NULL, measured_at TEXT, note TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
            for name, definition in (("description", "TEXT NOT NULL DEFAULT ''"), ("purpose", "TEXT NOT NULL DEFAULT ''"), ("task_id", "TEXT NOT NULL DEFAULT 'annealed-properties-v1'"), ("target_values", "TEXT NOT NULL DEFAULT '{}'"), ("notes", "TEXT NOT NULL DEFAULT ''"), ("updated_at", "TEXT NOT NULL DEFAULT ''")):
                if name not in existing:
                    conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {definition}")
            actual_columns = {row[1] for row in conn.execute("PRAGMA table_info(actual_measurements)")}
            if "snapshot_id" not in actual_columns:
                conn.execute("ALTER TABLE actual_measurements ADD COLUMN snapshot_id TEXT NOT NULL DEFAULT ''")
            now = _now()
            conn.execute("INSERT OR IGNORE INTO projects(id, name, description, purpose, task_id, target_values, notes, created_at, updated_at) VALUES ('default', '焼鈍条件の候補検討', '', '', 'annealed-properties-v1', '{}', '', ?, ?)", (now, now))

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(id=row["id"], name=row["name"], description=row["description"], purpose=row["purpose"], task_id=row["task_id"], target_values=json.loads(row["target_values"]), notes=row["notes"], created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]))

    def get_project(self) -> Project:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = 'default'").fetchone()
        return self._project(row)

    def update_project(self, payload: ProjectInput) -> Project:
        now = _now()
        with self._connect() as conn:
            conn.execute("UPDATE projects SET name=?, description=?, purpose=?, task_id=?, target_values=?, notes=?, updated_at=? WHERE id='default'", (payload.name, payload.description, payload.purpose, payload.task_id, json.dumps(payload.target_values, ensure_ascii=False, sort_keys=True), payload.notes, now))
        return self.get_project()

    @staticmethod
    def _candidate(row: sqlite3.Row) -> Candidate:
        payload = json.loads(row["payload"])
        return Candidate(id=row["id"], project_id=row["project_id"], created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]), **payload)

    def list_candidates(self) -> list[Candidate]:
        with self._connect() as conn:
            return [self._candidate(row) for row in conn.execute("SELECT * FROM candidates ORDER BY created_at")]

    def get_candidate(self, candidate_id: str) -> Candidate | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        return self._candidate(row) if row else None

    def create_candidate(self, payload: CandidateInput, project_id: str = "default") -> Candidate:
        candidate_id, now = str(uuid.uuid4()), _now()
        with self._connect() as conn:
            conn.execute("INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?)", (candidate_id, project_id, payload.name, payload.model_dump_json(), now, now))
        return self.get_candidate(candidate_id)  # type: ignore[return-value]

    def update_candidate(self, candidate_id: str, payload: CandidateInput) -> Candidate | None:
        now = _now()
        with self._connect() as conn:
            result = conn.execute("UPDATE candidates SET name = ?, payload = ?, updated_at = ? WHERE id = ?", (payload.name, payload.model_dump_json(), now, candidate_id))
        return self.get_candidate(candidate_id) if result.rowcount else None

    def delete_candidate(self, candidate_id: str) -> bool:
        with self._connect() as conn:
            # Snapshots and actuals are owned by a candidate. Screening runs contain
            # immutable canonical inputs and intentionally remain independently auditable.
            conn.execute("DELETE FROM actual_measurements WHERE candidate_id = ?", (candidate_id,))
            conn.execute("DELETE FROM snapshots WHERE candidate_id = ?", (candidate_id,))
            return bool(conn.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,)).rowcount)

    def create_snapshot(self, candidate_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = {"id": str(uuid.uuid4()), "candidate_id": candidate_id, "created_at": _now(), "payload": payload}
        with self._connect() as conn:
            conn.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)", (snapshot["id"], candidate_id, json.dumps(payload, ensure_ascii=False, sort_keys=True), snapshot["created_at"]))
        return snapshot

    def list_snapshots(self, candidate_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM snapshots WHERE candidate_id = ? ORDER BY created_at DESC", (candidate_id,)).fetchall()
        return [{"id": row["id"], "candidate_id": row["candidate_id"], "created_at": row["created_at"], "payload": json.loads(row["payload"])} for row in rows]

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
        return {"id": row["id"], "candidate_id": row["candidate_id"], "created_at": row["created_at"], "payload": json.loads(row["payload"])} if row else None

    def create_screening_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        run = {"id": str(uuid.uuid4()), "project_id": "default", "created_at": _now(), **payload}
        with self._connect() as conn:
            conn.execute("INSERT INTO screening_runs VALUES (?, ?, ?, ?)", (run["id"], "default", json.dumps(payload, ensure_ascii=False, sort_keys=True), run["created_at"]))
        return run

    def get_screening_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM screening_runs WHERE id = ?", (run_id,)).fetchone()
        return {"id": row["id"], "project_id": row["project_id"], "created_at": row["created_at"], **json.loads(row["payload"])} if row else None

    def list_screening_runs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM screening_runs ORDER BY created_at DESC").fetchall()
        return [{"id": row["id"], "project_id": row["project_id"], "created_at": row["created_at"], **json.loads(row["payload"])} for row in rows]

    @staticmethod
    def _actual(row: sqlite3.Row) -> ActualMeasurement:
        return ActualMeasurement(id=row["id"], candidate_id=row["candidate_id"], snapshot_id=row["snapshot_id"], property=row["property"], mean=row["mean"], std=row["std"], replicates=row["replicates"], unit=row["unit"], experiment_no=row["experiment_no"], measured_at=row["measured_at"], note=row["note"], created_at=datetime.fromisoformat(row["created_at"]))

    def list_actuals(self, candidate_id: str) -> list[ActualMeasurement]:
        with self._connect() as conn:
            return [self._actual(row) for row in conn.execute("SELECT * FROM actual_measurements WHERE candidate_id=? ORDER BY created_at", (candidate_id,))]

    def create_actual(self, candidate_id: str, snapshot_id: str, payload: ActualMeasurementInput) -> ActualMeasurement:
        actual_id, now = str(uuid.uuid4()), _now()
        with self._connect() as conn:
            conn.execute("INSERT INTO actual_measurements(id, candidate_id, snapshot_id, property, mean, std, replicates, unit, experiment_no, measured_at, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (actual_id, candidate_id, snapshot_id, payload.property, payload.mean, payload.std, payload.replicates, payload.unit, payload.experiment_no, payload.measured_at.isoformat() if payload.measured_at else None, payload.note, now))
        return self.list_actuals(candidate_id)[-1]

    def delete_actual(self, actual_id: str) -> bool:
        with self._connect() as conn:
            return bool(conn.execute("DELETE FROM actual_measurements WHERE id=?", (actual_id,)).rowcount)
