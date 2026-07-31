from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
)
from decision_workbench.domain.candidate_policy import MAX_CANDIDATES_PER_PROJECT
from decision_workbench.persistence.store_support import (
    CandidateArchivedError,
    CandidateLimitError,
    CandidateRevisionConflictError,
    ProjectNotFoundError,
    _now,
)


class CandidateRepository:
    @staticmethod
    def _candidate(row: sqlite3.Row) -> Candidate:
        payload = json.loads(row["payload"])
        return Candidate(
            id=row["id"],
            project_id=row["project_id"],
            revision=row["revision"],
            archived_at=datetime.fromisoformat(row["archived_at"])
            if row["archived_at"]
            else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            **payload,
        )

    def list_candidates(
        self, project_id: str = "default", *, include_archived: bool = False
    ) -> list[Candidate]:
        with self._connect() as conn:
            where = (
                "project_id = ?"
                if include_archived
                else "project_id = ? AND archived_at IS NULL"
            )
            return [
                self._candidate(row)
                for row in conn.execute(
                    f"SELECT * FROM candidates WHERE {where} ORDER BY created_at",
                    (project_id,),
                )
            ]

    def get_candidate(
        self,
        candidate_id: str,
        project_id: str | None = None,
        *,
        include_archived: bool = False,
    ) -> Candidate | None:
        with self._connect() as conn:
            active = "" if include_archived else " AND archived_at IS NULL"
            if project_id is None:
                row = conn.execute(
                    f"SELECT * FROM candidates WHERE id = ?{active}", (candidate_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    f"SELECT * FROM candidates WHERE id = ? AND project_id = ?{active}",
                    (candidate_id, project_id),
                ).fetchone()
        return self._candidate(row) if row else None

    def get_candidate_revision(
        self,
        candidate_id: str,
        revision: int,
        project_id: str | None = None,
    ) -> Candidate | None:
        with self._connect() as conn:
            if project_id is None:
                row = conn.execute(
                    "SELECT candidate_id AS id,project_id,revision,name,payload,"
                    "archived_at,created_at,updated_at FROM candidate_revisions "
                    "WHERE candidate_id=? AND revision=?",
                    (candidate_id, revision),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT candidate_id AS id,project_id,revision,name,payload,"
                    "archived_at,created_at,updated_at FROM candidate_revisions "
                    "WHERE candidate_id=? AND project_id=? AND revision=?",
                    (candidate_id, project_id, revision),
                ).fetchone()
        return self._candidate(row) if row else None

    @staticmethod
    def _record_candidate_revision(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
        conn.execute(
            "INSERT INTO candidate_revisions("
            "candidate_id,project_id,revision,name,payload,archived_at,created_at,updated_at"
            ") VALUES (?,?,?,?,?,?,?,?)",
            (
                row["id"],
                row["project_id"],
                row["revision"],
                row["name"],
                row["payload"],
                row["archived_at"],
                row["created_at"],
                row["updated_at"],
            ),
        )

    def create_candidate(
        self, payload: CandidateInput, project_id: str = "default"
    ) -> Candidate:
        return self.create_candidates([payload], project_id)[0]

    def create_candidates(
        self, payloads: list[CandidateInput], project_id: str = "default"
    ) -> list[Candidate]:
        if not payloads:
            return []
        records: list[tuple[str, str, str, str, str, str]] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                conn.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                is None
            ):
                raise ProjectNotFoundError(project_id)
            current = int(
                conn.execute(
                    "SELECT COUNT(*) FROM candidates WHERE project_id = ? AND archived_at IS NULL",
                    (project_id,),
                ).fetchone()[0]
            )
            if current + len(payloads) > MAX_CANDIDATES_PER_PROJECT:
                raise CandidateLimitError(
                    f"候補は1プロジェクトにつき最大{MAX_CANDIDATES_PER_PROJECT}件です"
                )
            for payload in payloads:
                candidate_id, now = str(uuid.uuid4()), _now()
                records.append(
                    (
                        candidate_id,
                        project_id,
                        payload.name,
                        payload.model_dump_json(),
                        now,
                        now,
                    )
                )
            conn.executemany(
                "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                records,
            )
            for candidate_id, *_ in records:
                row = conn.execute(
                    "SELECT * FROM candidates WHERE id=?",
                    (candidate_id,),
                ).fetchone()
                self._record_candidate_revision(conn, row)
        created = [self.get_candidate(candidate_id) for candidate_id, *_ in records]
        if any(candidate is None for candidate in created):
            raise RuntimeError("作成した候補を再取得できませんでした")
        return created  # type: ignore[return-value]

    def create_screening_candidates(
        self,
        payloads: list[tuple[int, CandidateInput]],
        run_id: str,
        project_id: str,
    ) -> tuple[list[Candidate], list[int]]:
        records: list[tuple[str, str, str, str, str, str]] = []
        skipped: list[int] = []
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                conn.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                is None
            ):
                raise ProjectNotFoundError(project_id)
            active_rows = conn.execute(
                "SELECT * FROM candidates WHERE project_id = ? AND archived_at IS NULL",
                (project_id,),
            ).fetchall()
            existing = set()
            for row in active_rows:
                candidate = self._candidate(row)
                provenance = candidate.provenance.model_dump(mode="json")
                reference = provenance.get("source_ref") or {}
                if (
                    provenance.get("source_kind") == "screening"
                    and reference.get("run_id") == run_id
                ):
                    existing.add(int(reference.get("point_index", -1)))
            unique_payloads: list[tuple[int, CandidateInput]] = []
            seen = set(existing)
            for point_index, payload in payloads:
                if point_index in seen:
                    skipped.append(point_index)
                    continue
                seen.add(point_index)
                unique_payloads.append((point_index, payload))
            if len(active_rows) + len(unique_payloads) > MAX_CANDIDATES_PER_PROJECT:
                raise CandidateLimitError(
                    f"候補は1プロジェクトにつき最大{MAX_CANDIDATES_PER_PROJECT}件です"
                )
            for _, payload in unique_payloads:
                candidate_id, now = str(uuid.uuid4()), _now()
                records.append(
                    (
                        candidate_id,
                        project_id,
                        payload.name,
                        payload.model_dump_json(),
                        now,
                        now,
                    )
                )
            if records:
                conn.executemany(
                    "INSERT INTO candidates(id,project_id,name,payload,created_at,updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    records,
                )
                for candidate_id, *_ in records:
                    row = conn.execute(
                        "SELECT * FROM candidates WHERE id=?",
                        (candidate_id,),
                    ).fetchone()
                    self._record_candidate_revision(conn, row)
        created = [
            self.get_candidate(candidate_id, project_id) for candidate_id, *_ in records
        ]
        if any(candidate is None for candidate in created):
            raise RuntimeError("作成した候補を再取得できませんでした")
        return created, skipped  # type: ignore[return-value]

    def update_candidate(
        self,
        candidate_id: str,
        project_id: str,
        payload: CandidateInput,
        expected_revision: int,
    ) -> Candidate | None:
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE candidates SET name = ?, payload = ?, revision = revision + 1, updated_at = ? WHERE id = ? AND project_id = ? AND revision = ? AND archived_at IS NULL",
                (
                    payload.name,
                    payload.model_dump_json(),
                    now,
                    candidate_id,
                    project_id,
                    expected_revision,
                ),
            )
            if not result.rowcount:
                row = conn.execute(
                    "SELECT * FROM candidates WHERE id=? AND project_id=?",
                    (candidate_id, project_id),
                ).fetchone()
                if row is None:
                    return None
                current = self._candidate(row)
                if current.archived_at is not None:
                    raise CandidateArchivedError("archive済み候補は編集できません")
                raise CandidateRevisionConflictError(current)
            row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            self._record_candidate_revision(conn, row)
        return self.get_candidate(candidate_id, project_id)

    def delete_candidate(
        self, candidate_id: str, project_id: str, expected_revision: int
    ) -> bool:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            if row is None:
                return False
            current = self._candidate(row)
            if current.archived_at is not None:
                raise CandidateArchivedError("候補はすでにarchiveされています")
            if current.revision != expected_revision:
                raise CandidateRevisionConflictError(current)
            now = _now()
            updated = conn.execute(
                "UPDATE candidates SET archived_at=?, revision=revision+1, updated_at=? "
                "WHERE id=? AND project_id=? AND revision=? AND archived_at IS NULL",
                (now, now, candidate_id, project_id, expected_revision),
            )
            if not updated.rowcount:
                latest = conn.execute(
                    "SELECT * FROM candidates WHERE id=? AND project_id=?",
                    (candidate_id, project_id),
                ).fetchone()
                if latest is None:
                    return False
                raise CandidateRevisionConflictError(self._candidate(latest))
            archived = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            self._record_candidate_revision(conn, archived)
            return True

    def restore_candidate(self, candidate_id: str, project_id: str) -> Candidate | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            if row is None:
                return None
            if row["archived_at"] is None:
                return self._candidate(row)
            active_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM candidates WHERE project_id=? AND archived_at IS NULL",
                    (project_id,),
                ).fetchone()[0]
            )
            if active_count >= MAX_CANDIDATES_PER_PROJECT:
                raise CandidateLimitError(
                    f"候補は1プロジェクトにつき最大{MAX_CANDIDATES_PER_PROJECT}件です"
                )
            now = _now()
            conn.execute(
                "UPDATE candidates SET archived_at=NULL, revision=revision+1, updated_at=? "
                "WHERE id=? AND project_id=? AND archived_at IS NOT NULL",
                (now, candidate_id, project_id),
            )
            restored = conn.execute(
                "SELECT * FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            self._record_candidate_revision(conn, restored)
            return self._candidate(restored)
