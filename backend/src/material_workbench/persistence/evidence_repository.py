from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from typing import Any
from material_workbench.contracts.schemas import (
    ActualMeasurement,
    LineageNodeReview,
    LineageNodeReviewInput,
)
from material_workbench.contracts.ai_review_contracts import (
    AiReviewDisposition,
    AiReviewRun,
)
from material_workbench.persistence.store_support import (
    InvalidProjectDecisionError,
    ProjectNotFoundError,
    StoreDataIntegrityError,
    _now,
)


class EvidenceRepository:
    @staticmethod
    def _lineage_review(row: sqlite3.Row) -> LineageNodeReview:
        return LineageNodeReview(
            project_id=row["project_id"],
            entity_key=row["entity_key"],
            entity_type=row["entity_type"],
            status=row["status"],
            note=row["note"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_lineage_reviews(self, project_id: str) -> list[LineageNodeReview]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM lineage_node_reviews WHERE project_id=? "
                "ORDER BY updated_at DESC, entity_key",
                (project_id,),
            ).fetchall()
        return [self._lineage_review(row) for row in rows]

    def get_lineage_review(
        self, project_id: str, entity_key: str
    ) -> LineageNodeReview | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lineage_node_reviews WHERE project_id=? AND entity_key=?",
                (project_id, entity_key),
            ).fetchone()
        return self._lineage_review(row) if row else None

    def upsert_lineage_review(
        self,
        project_id: str,
        entity_key: str,
        payload: LineageNodeReviewInput,
    ) -> LineageNodeReview:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO lineage_node_reviews("
                "project_id,entity_key,entity_type,status,note,created_at,updated_at"
                ") VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(project_id,entity_key) DO UPDATE SET "
                "entity_type=excluded.entity_type,status=excluded.status,"
                "note=excluded.note,updated_at=excluded.updated_at",
                (
                    project_id,
                    entity_key,
                    payload.entity_type,
                    payload.status,
                    payload.note,
                    now,
                    now,
                ),
            )
        return self.get_lineage_review(project_id, entity_key)  # type: ignore[return-value]

    def delete_lineage_review(self, project_id: str, entity_key: str) -> bool:
        with self._connect() as conn:
            result = conn.execute(
                "DELETE FROM lineage_node_reviews WHERE project_id=? AND entity_key=?",
                (project_id, entity_key),
            )
        return bool(result.rowcount)

    @staticmethod
    def _validate_decision(
        conn: sqlite3.Connection, project_id: str, candidate_id: str, snapshot_id: str
    ) -> None:
        if not candidate_id:
            return
        project_row = conn.execute(
            "SELECT scientific_identity_json FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if project_row is None:
            raise InvalidProjectDecisionError("プロジェクトが見つかりません")
        try:
            identity_kind = json.loads(project_row["scientific_identity_json"]).get(
                "identity_kind"
            )
        except (TypeError, json.JSONDecodeError, AttributeError) as exc:
            raise InvalidProjectDecisionError(
                "プロジェクトの固定identityを確認できません"
            ) from exc
        if (
            conn.execute(
                "SELECT 1 FROM candidates WHERE id=? AND project_id=?",
                (candidate_id, project_id),
            ).fetchone()
            is None
        ):
            raise InvalidProjectDecisionError(
                "採用候補は同じプロジェクトから選択してください"
            )
        if identity_kind == "chain":
            snapshot = conn.execute(
                "SELECT 1 FROM chain_snapshot_records "
                "WHERE id=? AND project_id=? AND candidate_id=?",
                (snapshot_id, project_id, candidate_id),
            ).fetchone()
        else:
            snapshot = conn.execute(
                "SELECT 1 FROM snapshots WHERE id=? AND candidate_id=?",
                (snapshot_id, candidate_id),
            ).fetchone()
        if snapshot is None:
            raise InvalidProjectDecisionError(
                "プロジェクト種別に対応する判断時点のSnapshotが見つかりません"
            )

    def create_snapshot(
        self, candidate_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        snapshot = {
            "id": str(uuid.uuid4()),
            "candidate_id": candidate_id,
            "created_at": _now(),
            "payload": payload,
        }
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO snapshots VALUES (?, ?, ?, ?)",
                (
                    snapshot["id"],
                    candidate_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    snapshot["created_at"],
                ),
            )
        return snapshot

    def list_snapshots(self, candidate_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE candidate_id = ? ORDER BY created_at DESC",
                (candidate_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "candidate_id": row["candidate_id"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        return (
            {
                "id": row["id"],
                "candidate_id": row["candidate_id"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload"]),
            }
            if row
            else None
        )

    def create_screening_run(
        self, payload: dict[str, Any], project_id: str = "default"
    ) -> dict[str, Any]:
        run = {
            "id": str(uuid.uuid4()),
            "project_id": project_id,
            "created_at": _now(),
            **payload,
        }
        with self._connect() as conn:
            if (
                conn.execute(
                    "SELECT 1 FROM projects WHERE id = ?", (project_id,)
                ).fetchone()
                is None
            ):
                raise ProjectNotFoundError(project_id)
            conn.execute(
                "INSERT INTO screening_runs VALUES (?, ?, ?, ?)",
                (
                    run["id"],
                    project_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    run["created_at"],
                ),
            )
        return run

    def get_screening_run(
        self, run_id: str, project_id: str | None = None
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            if project_id is None:
                row = conn.execute(
                    "SELECT * FROM screening_runs WHERE id = ?", (run_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM screening_runs WHERE id = ? AND project_id = ?",
                    (run_id, project_id),
                ).fetchone()
        return (
            {
                "id": row["id"],
                "project_id": row["project_id"],
                "created_at": row["created_at"],
                **json.loads(row["payload"]),
            }
            if row
            else None
        )

    def list_screening_runs(self, project_id: str = "default") -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM screening_runs WHERE project_id = ? ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "project_id": row["project_id"],
                "created_at": row["created_at"],
                **json.loads(row["payload"]),
            }
            for row in rows
        ]

    def delete_screening_run(self, run_id: str, project_id: str = "default") -> bool:
        with self._connect() as conn:
            deleted = conn.execute(
                "DELETE FROM screening_runs WHERE id = ? AND project_id = ?",
                (run_id, project_id),
            )
        return deleted.rowcount == 1

    @staticmethod
    def _decision_activity_run(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "semantic_identity": row["semantic_identity"],
            "project_id": row["project_id"],
            "created_at": row["created_at"],
            **json.loads(row["payload"]),
        }

    def create_decision_activity_run(
        self,
        *,
        semantic_identity: str,
        project_id: str,
        candidate_id: str,
        activity_id: str,
        activity_version: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        run_id = f"activity-{semantic_identity.removeprefix('sha256:')[:24]}"
        created_at = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if (
                conn.execute(
                    "SELECT 1 FROM candidates WHERE id=? AND project_id=?",
                    (candidate_id, project_id),
                ).fetchone()
                is None
            ):
                raise ProjectNotFoundError(project_id)
            conn.execute(
                "INSERT OR IGNORE INTO decision_activity_runs("
                "id,semantic_identity,project_id,candidate_id,activity_id,"
                "activity_version,payload,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    semantic_identity,
                    project_id,
                    candidate_id,
                    activity_id,
                    activity_version,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )
            row = conn.execute(
                "SELECT * FROM decision_activity_runs WHERE semantic_identity=?",
                (semantic_identity,),
            ).fetchone()
        if row is None:
            raise StoreDataIntegrityError("検討アクティビティを保存できませんでした")
        return self._decision_activity_run(row)

    def get_decision_activity_run_by_identity(
        self, semantic_identity: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decision_activity_runs WHERE semantic_identity=?",
                (semantic_identity,),
            ).fetchone()
        return self._decision_activity_run(row) if row else None

    def get_decision_activity_run(
        self, run_id: str, project_id: str
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decision_activity_runs WHERE id=? AND project_id=?",
                (run_id, project_id),
            ).fetchone()
        return self._decision_activity_run(row) if row else None

    def list_decision_activity_runs(
        self, project_id: str, candidate_id: str | None = None
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM decision_activity_runs WHERE project_id=?"
        parameters: tuple[str, ...] = (project_id,)
        if candidate_id is not None:
            query += " AND candidate_id=?"
            parameters = (project_id, candidate_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, parameters).fetchall()
        return [self._decision_activity_run(row) for row in rows]

    @staticmethod
    def _ai_review_run(row: sqlite3.Row) -> AiReviewRun:
        return AiReviewRun.model_validate_json(row["payload"])

    def finalize_ai_review_run(self, run: AiReviewRun) -> AiReviewRun:
        if run.state == "running" or run.completed_at is None:
            raise ValueError("AI review finalization requires a terminal run")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state,project_id,candidate_id,candidate_revision,payload "
                "FROM ai_review_runs WHERE review_run_id=?",
                (run.review_run_id,),
            ).fetchone()
            if row is None:
                raise StoreDataIntegrityError("AI Review Runが見つかりません")
            if row["state"] != "running":
                raise StoreDataIntegrityError("確定済みAI Review Runは変更できません")
            existing = AiReviewRun.model_validate_json(row["payload"])
            terminal_fields = {
                "state",
                "completed_at",
                "findings",
                "summary",
                "suggested_actions",
                "limitations",
                "failure_reason",
            }
            existing_envelope = existing.model_dump(
                mode="json", exclude=terminal_fields
            )
            submitted_envelope = run.model_dump(mode="json", exclude=terminal_fields)
            if submitted_envelope != existing_envelope:
                raise StoreDataIntegrityError(
                    "AI Review Runのimmutable envelopeが変わっています"
                )
            if (
                row["project_id"] != run.project_id
                or row["candidate_id"] != run.candidate_id
                or int(row["candidate_revision"])
                != run.provenance.reviewed_candidate_revision
            ):
                raise StoreDataIntegrityError("AI Review Runのidentityが変わっています")
            updated = conn.execute(
                "UPDATE ai_review_runs SET state=?,payload=?,completed_at=? "
                "WHERE review_run_id=? AND state='running'",
                (
                    run.state,
                    run.model_dump_json(),
                    run.completed_at.isoformat(),
                    run.review_run_id,
                ),
            )
            if updated.rowcount != 1:
                raise StoreDataIntegrityError("AI Review Runを確定できませんでした")
        return run

    def get_ai_review_run(
        self, project_id: str, review_run_id: str
    ) -> AiReviewRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM ai_review_runs "
                "WHERE project_id=? AND review_run_id=?",
                (project_id, review_run_id),
            ).fetchone()
        return self._ai_review_run(row) if row else None

    def list_ai_review_runs(
        self, project_id: str, candidate_id: str | None = None
    ) -> list[AiReviewRun]:
        query = "SELECT payload FROM ai_review_runs WHERE project_id=?"
        parameters: tuple[str, ...] = (project_id,)
        if candidate_id is not None:
            query += " AND candidate_id=?"
            parameters = (project_id, candidate_id)
        query += " ORDER BY started_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, parameters).fetchall()
        return [self._ai_review_run(row) for row in rows]

    def append_ai_review_disposition(
        self, disposition: AiReviewDisposition
    ) -> AiReviewDisposition:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT state,project_id FROM ai_review_runs WHERE review_run_id=?",
                (disposition.review_run_id,),
            ).fetchone()
            if run is None or run["project_id"] != disposition.project_id:
                raise StoreDataIntegrityError("AI Review Runが見つかりません")
            if run["state"] == "running":
                raise StoreDataIntegrityError("実行中のAI Reviewへ判断を記録できません")
            conn.execute(
                "INSERT INTO ai_review_dispositions("
                "disposition_id,review_run_id,project_id,payload,recorded_at"
                ") VALUES (?,?,?,?,?)",
                (
                    disposition.disposition_id,
                    disposition.review_run_id,
                    disposition.project_id,
                    disposition.model_dump_json(),
                    disposition.recorded_at.isoformat(),
                ),
            )
        return disposition

    def list_ai_review_dispositions(
        self, project_id: str, review_run_id: str
    ) -> list[AiReviewDisposition]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM ai_review_dispositions "
                "WHERE project_id=? AND review_run_id=? ORDER BY recorded_at",
                (project_id, review_run_id),
            ).fetchall()
        return [AiReviewDisposition.model_validate_json(row["payload"]) for row in rows]

    @staticmethod
    def _actual(row: sqlite3.Row) -> ActualMeasurement:
        return ActualMeasurement(
            id=row["id"],
            candidate_id=row["candidate_id"],
            snapshot_id=row["snapshot_id"],
            property=row["property"],
            mean=row["mean"],
            std=row["std"],
            replicates=row["replicates"],
            unit=row["unit"],
            experiment_no=row["experiment_no"],
            measured_at=row["measured_at"],
            note=row["note"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_actuals(self, candidate_id: str) -> list[ActualMeasurement]:
        with self._connect() as conn:
            return [
                self._actual(row)
                for row in conn.execute(
                    "SELECT * FROM actual_measurements WHERE candidate_id=? ORDER BY created_at",
                    (candidate_id,),
                )
            ]

    def list_project_actuals(self, project_id: str) -> list[ActualMeasurement]:
        """Return the complete, stable incumbent population for one Project."""

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT actual_measurements.* FROM actual_measurements "
                "JOIN candidates ON candidates.id=actual_measurements.candidate_id "
                "WHERE candidates.project_id=? "
                "ORDER BY actual_measurements.id",
                (project_id,),
            ).fetchall()
        return [self._actual(row) for row in rows]

    def delete_actual(self, actual_id: str) -> bool:
        with self._connect() as conn:
            return bool(
                conn.execute(
                    "DELETE FROM actual_measurements WHERE id=?", (actual_id,)
                ).rowcount
            )
