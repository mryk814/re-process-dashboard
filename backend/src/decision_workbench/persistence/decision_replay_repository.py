from __future__ import annotations

import json
import sqlite3

from decision_workbench.contracts.decision_replay_contracts import (
    DecisionCase,
    DecisionReplayRun,
)
from decision_workbench.persistence.store_support import (
    ProjectNotFoundError,
    StoreDataIntegrityError,
    _now,
)


class DecisionReplayRepository:
    @staticmethod
    def _case(row: sqlite3.Row) -> DecisionCase:
        return DecisionCase.model_validate(
            {
                "id": row["id"],
                "semantic_identity": row["semantic_identity"],
                "project_id": row["project_id"],
                "created_at": row["created_at"],
                **json.loads(row["payload"]),
            }
        )

    @staticmethod
    def _run(row: sqlite3.Row) -> DecisionReplayRun:
        return DecisionReplayRun.model_validate(
            {
                "id": row["id"],
                "semantic_identity": row["semantic_identity"],
                "project_id": row["project_id"],
                "case_id": row["case_id"],
                "created_at": row["created_at"],
                **json.loads(row["payload"]),
            }
        )

    def create_decision_case(
        self,
        *,
        case_id: str,
        semantic_identity: str,
        project_id: str,
        task_id: str,
        task_contract_digest: str,
        objective_definition_digest: str | None,
        decision_timestamp: str,
        payload: dict[str, object],
    ) -> DecisionCase:
        created_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM projects WHERE id=?", (project_id,)
            ).fetchone() is None:
                raise ProjectNotFoundError(project_id)
            conn.execute(
                "INSERT OR IGNORE INTO decision_cases("
                "id,semantic_identity,project_id,task_id,task_contract_digest,"
                "objective_definition_digest,decision_timestamp,payload,created_at"
                ") VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    case_id, semantic_identity, project_id, task_id,
                    task_contract_digest, objective_definition_digest,
                    decision_timestamp,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM decision_cases WHERE semantic_identity=?",
                (semantic_identity,),
            ).fetchone()
        if row is None:
            raise StoreDataIntegrityError("Decision Caseを保存できませんでした")
        stored = self._case(row)
        if stored.id != case_id:
            raise StoreDataIntegrityError("Decision Case identityが衝突しました")
        return stored

    def get_decision_case(self, project_id: str, case_id: str) -> DecisionCase | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decision_cases WHERE project_id=? AND id=?",
                (project_id, case_id),
            ).fetchone()
        return self._case(row) if row is not None else None

    def list_decision_cases(self, project_id: str) -> list[DecisionCase]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decision_cases WHERE project_id=? "
                "ORDER BY decision_timestamp DESC,id",
                (project_id,),
            ).fetchall()
        return [self._case(row) for row in rows]

    def list_compatible_decision_cases(
        self,
        *,
        task_id: str,
        task_contract_digest: str,
        objective_definition_digest: str | None,
    ) -> list[DecisionCase]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decision_cases WHERE task_id=? "
                "AND task_contract_digest=? "
                "AND objective_definition_digest IS ? "
                "ORDER BY decision_timestamp DESC,id",
                (task_id, task_contract_digest, objective_definition_digest),
            ).fetchall()
        return [self._case(row) for row in rows]

    def create_decision_replay_run(
        self,
        *,
        run_id: str,
        semantic_identity: str,
        project_id: str,
        case_id: str,
        payload: dict[str, object],
    ) -> DecisionReplayRun:
        created_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM decision_cases WHERE id=? AND project_id=?",
                (case_id, project_id),
            ).fetchone() is None:
                raise ProjectNotFoundError(project_id)
            conn.execute(
                "INSERT OR IGNORE INTO decision_replay_runs("
                "id,semantic_identity,project_id,case_id,payload,created_at"
                ") VALUES (?,?,?,?,?,?)",
                (
                    run_id, semantic_identity, project_id, case_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True), created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM decision_replay_runs WHERE semantic_identity=?",
                (semantic_identity,),
            ).fetchone()
        if row is None:
            raise StoreDataIntegrityError("Decision Replay Runを保存できませんでした")
        stored = self._run(row)
        if stored.id != run_id:
            raise StoreDataIntegrityError("Decision Replay Run identityが衝突しました")
        return stored

    def list_decision_replay_runs(
        self, project_id: str, case_id: str | None = None
    ) -> list[DecisionReplayRun]:
        query = "SELECT * FROM decision_replay_runs WHERE project_id=?"
        parameters: tuple[str, ...] = (project_id,)
        if case_id is not None:
            query += " AND case_id=?"
            parameters = (project_id, case_id)
        query += " ORDER BY created_at DESC,id"
        with self._connect() as conn:
            rows = conn.execute(query, parameters).fetchall()
        return [self._run(row) for row in rows]
