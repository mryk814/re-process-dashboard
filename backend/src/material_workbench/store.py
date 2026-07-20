from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .candidate_migration import migrate_candidate_storage
from .schemas import ActualMeasurement, ActualMeasurementInput, Candidate, CandidateInput, Project, ProjectInput


MAX_CANDIDATES_PER_PROJECT = 10


class ProjectNotFoundError(LookupError):
    pass


class CandidateLimitError(ValueError):
    pass


class InvalidProjectDecisionError(ValueError):
    pass


class CandidateCopyConflictError(ValueError):
    pass


class CandidateArchivedError(ValueError):
    pass


class CandidateRevisionConflictError(ValueError):
    def __init__(self, current: Candidate) -> None:
        super().__init__("候補は別の操作で更新されています")
        self.current = current


class StoreDataIntegrityError(RuntimeError):
    pass


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
        migrate_candidate_storage(self.path)

    @staticmethod
    def _project(row: sqlite3.Row) -> Project:
        return Project(id=row["id"], name=row["name"], description=row["description"], purpose=row["purpose"], task_id=row["task_id"], target_values=json.loads(row["target_values"]), input_ranges=json.loads(row["input_ranges"]), notes=row["notes"], decision_candidate_id=row["decision_candidate_id"], decision_snapshot_id=row["decision_snapshot_id"], decision_note=row["decision_note"], created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]))

    def list_projects(self) -> list[Project]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at, id").fetchall()
        return [self._project(row) for row in rows]

    def get_project(self, project_id: str = "default") -> Project | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        return self._project(row) if row else None

    def create_project(self, payload: ProjectInput, initial_candidate: CandidateInput | None = None) -> Project:
        project_id, now = str(uuid.uuid4()), _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if initial_candidate is not None and initial_candidate.provenance.source_kind == "copy":
                reference = initial_candidate.provenance.source_ref
                source = conn.execute(
                    "SELECT candidates.revision, projects.task_id FROM candidates "
                    "JOIN projects ON projects.id=candidates.project_id "
                    "WHERE candidates.id=? AND candidates.project_id=?",
                    (reference.candidate_id, reference.project_id),
                ).fetchone()
                if source is None or source["revision"] != reference.candidate_revision:
                    raise CandidateCopyConflictError("コピー元候補またはrevisionが一致しません")
                if source["task_id"] != payload.task_id:
                    raise CandidateCopyConflictError("異なる予測タスクの候補はコピーできません")
            conn.execute(
                "INSERT INTO projects(id, name, description, purpose, task_id, target_values, input_ranges, notes, decision_candidate_id, decision_snapshot_id, decision_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, payload.name, payload.description, payload.purpose, payload.task_id, json.dumps(payload.target_values, ensure_ascii=False, sort_keys=True), json.dumps({key: value.model_dump() for key, value in payload.input_ranges.items()}, ensure_ascii=False, sort_keys=True), payload.notes, payload.decision_candidate_id, payload.decision_snapshot_id, payload.decision_note, now, now),
            )
            if initial_candidate is not None:
                candidate_id = str(uuid.uuid4())
                conn.execute(
                    "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (candidate_id, project_id, initial_candidate.name, initial_candidate.model_dump_json(), now, now),
                )
        return self.get_project(project_id)  # type: ignore[return-value]

    def ensure_project(self, project_id: str, payload: ProjectInput) -> Project:
        existing = self.get_project(project_id)
        if existing is not None:
            if existing.task_id != payload.task_id:
                raise ValueError(f"reserved project {project_id} belongs to another task")
            return existing
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO projects(id, name, description, purpose, task_id, target_values, input_ranges, notes, decision_candidate_id, decision_snapshot_id, decision_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (project_id, payload.name, payload.description, payload.purpose, payload.task_id, json.dumps(payload.target_values, ensure_ascii=False, sort_keys=True), json.dumps({key: value.model_dump() for key, value in payload.input_ranges.items()}, ensure_ascii=False, sort_keys=True), payload.notes, "", "", "", now, now),
            )
        return self.get_project(project_id)  # type: ignore[return-value]

    def update_project(self, project_id: str, payload: ProjectInput) -> Project | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_decision(conn, project_id, payload.decision_candidate_id, payload.decision_snapshot_id)
            result = conn.execute("UPDATE projects SET name=?, description=?, purpose=?, task_id=?, target_values=?, input_ranges=?, notes=?, decision_candidate_id=?, decision_snapshot_id=?, decision_note=?, updated_at=? WHERE id=?", (payload.name, payload.description, payload.purpose, payload.task_id, json.dumps(payload.target_values, ensure_ascii=False, sort_keys=True), json.dumps({key: value.model_dump() for key, value in payload.input_ranges.items()}, ensure_ascii=False, sort_keys=True), payload.notes, payload.decision_candidate_id, payload.decision_snapshot_id, payload.decision_note, now, project_id))
        return self.get_project(project_id) if result.rowcount else None

    @staticmethod
    def _validate_decision(conn: sqlite3.Connection, project_id: str, candidate_id: str, snapshot_id: str) -> None:
        if not candidate_id:
            return
        if conn.execute("SELECT 1 FROM candidates WHERE id=? AND project_id=?", (candidate_id, project_id)).fetchone() is None:
            raise InvalidProjectDecisionError("採用候補は同じプロジェクトから選択してください")
        if conn.execute("SELECT 1 FROM snapshots WHERE id=? AND candidate_id=?", (snapshot_id, candidate_id)).fetchone() is None:
            raise InvalidProjectDecisionError("判断時点の予測スナップショットが見つかりません")

    def update_project_decision(self, project_id: str, candidate_id: str, snapshot_id: str, note: str) -> Project | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._validate_decision(conn, project_id, candidate_id, snapshot_id)
            result = conn.execute(
                "UPDATE projects SET decision_candidate_id=?, decision_snapshot_id=?, decision_note=?, updated_at=? WHERE id=?",
                (candidate_id, snapshot_id, note, now, project_id),
            )
        return self.get_project(project_id) if result.rowcount else None

    @staticmethod
    def _candidate(row: sqlite3.Row) -> Candidate:
        payload = json.loads(row["payload"])
        return Candidate(id=row["id"], project_id=row["project_id"], revision=row["revision"], archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None, created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]), **payload)

    def list_candidates(self, project_id: str = "default", *, include_archived: bool = False) -> list[Candidate]:
        with self._connect() as conn:
            where = "project_id = ?" if include_archived else "project_id = ? AND archived_at IS NULL"
            return [self._candidate(row) for row in conn.execute(f"SELECT * FROM candidates WHERE {where} ORDER BY created_at", (project_id,))]

    def get_candidate(self, candidate_id: str, project_id: str | None = None, *, include_archived: bool = False) -> Candidate | None:
        with self._connect() as conn:
            active = "" if include_archived else " AND archived_at IS NULL"
            if project_id is None:
                row = conn.execute(f"SELECT * FROM candidates WHERE id = ?{active}", (candidate_id,)).fetchone()
            else:
                row = conn.execute(f"SELECT * FROM candidates WHERE id = ? AND project_id = ?{active}", (candidate_id, project_id)).fetchone()
        return self._candidate(row) if row else None

    def project_history(self, project_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute("BEGIN")
            project_row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if project_row is None:
                return None
            candidate_rows = conn.execute(
                "SELECT * FROM candidates WHERE project_id=? ORDER BY updated_at DESC, created_at DESC",
                (project_id,),
            ).fetchall()
            snapshot_rows = conn.execute(
                "SELECT snapshots.* FROM snapshots JOIN candidates ON candidates.id=snapshots.candidate_id "
                "WHERE candidates.project_id=? ORDER BY snapshots.created_at DESC",
                (project_id,),
            ).fetchall()
            actual_rows = conn.execute(
                "SELECT actual_measurements.* FROM actual_measurements "
                "JOIN candidates ON candidates.id=actual_measurements.candidate_id "
                "WHERE candidates.project_id=? ORDER BY actual_measurements.created_at DESC",
                (project_id,),
            ).fetchall()

        project = self._project(project_row)
        snapshots_by_candidate: dict[str, list[dict[str, Any]]] = {}
        for row in snapshot_rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise StoreDataIntegrityError(f"snapshot {row['id']} を読み取れません") from exc
            version = payload.get("snapshot_schema_version") if isinstance(payload, dict) else None
            if version not in {"prediction-snapshot-v1", "prediction-snapshot-v2"}:
                raise StoreDataIntegrityError(f"snapshot {row['id']} の形式を解釈できません")
            raw_candidate = payload.get("raw_candidate")
            candidate_revision = raw_candidate.get("revision") if version == "prediction-snapshot-v2" and isinstance(raw_candidate, dict) else None
            prediction = payload.get("prediction")
            if not isinstance(prediction, dict) or not isinstance(prediction.get("predictions"), dict):
                raise StoreDataIntegrityError(f"snapshot {row['id']} の予測要約を読み取れません")
            snapshots_by_candidate.setdefault(row["candidate_id"], []).append({
                "id": row["id"],
                "candidate_id": row["candidate_id"],
                "created_at": row["created_at"],
                "candidate_revision": candidate_revision,
                "prediction_summary": prediction["predictions"],
                "model_ref": payload.get("provenance"),
            })
        actuals_by_candidate: dict[str, list[ActualMeasurement]] = {}
        for row in actual_rows:
            snapshot_ids = {item["id"] for item in snapshots_by_candidate.get(row["candidate_id"], [])}
            if row["snapshot_id"] not in snapshot_ids:
                raise StoreDataIntegrityError(f"actual {row['id']} の固定snapshotが見つかりません")
            actuals_by_candidate.setdefault(row["candidate_id"], []).append(self._actual(row))
        items = []
        for row in candidate_rows:
            candidate = self._candidate(row)
            decision = None
            if project.decision_candidate_id == candidate.id and project.decision_snapshot_id:
                if project.decision_snapshot_id not in {item["id"] for item in snapshots_by_candidate.get(candidate.id, [])}:
                    raise StoreDataIntegrityError("採用判断の固定snapshotが見つかりません")
                decision = {
                    "candidate_id": candidate.id,
                    "snapshot_id": project.decision_snapshot_id,
                    "note": project.decision_note,
                }
            items.append({
                "candidate": candidate,
                "current": {"revision": candidate.revision, "updated_at": candidate.updated_at},
                "snapshots": snapshots_by_candidate.get(candidate.id, []),
                "actuals": actuals_by_candidate.get(candidate.id, []),
                "decision": decision,
            })
        return {"project": project, "candidates": items}

    def create_candidate(self, payload: CandidateInput, project_id: str = "default") -> Candidate:
        return self.create_candidates([payload], project_id)[0]

    def create_candidates(self, payloads: list[CandidateInput], project_id: str = "default") -> list[Candidate]:
        if not payloads:
            return []
        records: list[tuple[str, str, str, str, str, str]] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ProjectNotFoundError(project_id)
            current = int(conn.execute("SELECT COUNT(*) FROM candidates WHERE project_id = ? AND archived_at IS NULL", (project_id,)).fetchone()[0])
            if current + len(payloads) > MAX_CANDIDATES_PER_PROJECT:
                raise CandidateLimitError(f"候補は1プロジェクトにつき最大{MAX_CANDIDATES_PER_PROJECT}件です")
            for payload in payloads:
                candidate_id, now = str(uuid.uuid4()), _now()
                records.append((candidate_id, project_id, payload.name, payload.model_dump_json(), now, now))
            conn.executemany("INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)", records)
        created = [self.get_candidate(candidate_id) for candidate_id, *_ in records]
        if any(candidate is None for candidate in created):
            raise RuntimeError("作成した候補を再取得できませんでした")
        return created  # type: ignore[return-value]

    def update_candidate(self, candidate_id: str, project_id: str, payload: CandidateInput, expected_revision: int) -> Candidate | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE candidates SET name = ?, payload = ?, revision = revision + 1, updated_at = ? WHERE id = ? AND project_id = ? AND revision = ? AND archived_at IS NULL",
                (payload.name, payload.model_dump_json(), now, candidate_id, project_id, expected_revision),
            )
            if not result.rowcount:
                row = conn.execute("SELECT * FROM candidates WHERE id=? AND project_id=?", (candidate_id, project_id)).fetchone()
                if row is None:
                    return None
                current = self._candidate(row)
                if current.archived_at is not None:
                    raise CandidateArchivedError("archive済み候補は編集できません")
                raise CandidateRevisionConflictError(current)
        return self.get_candidate(candidate_id, project_id)

    def delete_candidate(self, candidate_id: str, project_id: str, expected_revision: int) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM candidates WHERE id=? AND project_id=?", (candidate_id, project_id)).fetchone()
            if row is None:
                return False
            current = self._candidate(row)
            if current.archived_at is not None:
                raise CandidateArchivedError("候補はすでにarchiveされています")
            if current.revision != expected_revision:
                raise CandidateRevisionConflictError(current)
            referenced = bool(
                conn.execute("SELECT 1 FROM projects WHERE decision_candidate_id=?", (candidate_id,)).fetchone()
                or conn.execute("SELECT 1 FROM snapshots WHERE candidate_id=?", (candidate_id,)).fetchone()
                or conn.execute("SELECT 1 FROM actual_measurements WHERE candidate_id=?", (candidate_id,)).fetchone()
            )
            if not referenced:
                for screening_row in conn.execute("SELECT payload FROM screening_runs WHERE project_id=?", (project_id,)):
                    try:
                        screening_payload = json.loads(screening_row["payload"])
                    except (TypeError, json.JSONDecodeError) as exc:
                        raise StoreDataIntegrityError("スクリーニング履歴を読み取れません") from exc
                    if not isinstance(screening_payload, dict):
                        raise StoreDataIntegrityError("スクリーニング履歴の形式が不正です")
                    if screening_payload.get("base_candidate_id") == candidate_id:
                        referenced = True
                        break
            if referenced:
                now = _now()
                updated = conn.execute(
                    "UPDATE candidates SET archived_at=?, revision=revision+1, updated_at=? WHERE id=? AND project_id=? AND revision=? AND archived_at IS NULL",
                    (now, now, candidate_id, project_id, expected_revision),
                )
                if not updated.rowcount:
                    latest = conn.execute("SELECT * FROM candidates WHERE id=? AND project_id=?", (candidate_id, project_id)).fetchone()
                    if latest is None:
                        return False
                    raise CandidateRevisionConflictError(self._candidate(latest))
                return True
            return bool(conn.execute("DELETE FROM candidates WHERE id=? AND project_id=? AND revision=?", (candidate_id, project_id, expected_revision)).rowcount)

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

    def create_screening_run(self, payload: dict[str, Any], project_id: str = "default") -> dict[str, Any]:
        run = {"id": str(uuid.uuid4()), "project_id": project_id, "created_at": _now(), **payload}
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ProjectNotFoundError(project_id)
            conn.execute("INSERT INTO screening_runs VALUES (?, ?, ?, ?)", (run["id"], project_id, json.dumps(payload, ensure_ascii=False, sort_keys=True), run["created_at"]))
        return run

    def get_screening_run(self, run_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        with self._connect() as conn:
            if project_id is None:
                row = conn.execute("SELECT * FROM screening_runs WHERE id = ?", (run_id,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM screening_runs WHERE id = ? AND project_id = ?", (run_id, project_id)).fetchone()
        return {"id": row["id"], "project_id": row["project_id"], "created_at": row["created_at"], **json.loads(row["payload"])} if row else None

    def list_screening_runs(self, project_id: str = "default") -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM screening_runs WHERE project_id = ? ORDER BY created_at DESC", (project_id,)).fetchall()
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
