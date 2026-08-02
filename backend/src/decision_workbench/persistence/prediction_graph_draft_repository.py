from __future__ import annotations

from datetime import datetime
import json

from decision_workbench.contracts.prediction_graph_draft_contracts import (
    PredictionGraphDraftContent,
    PredictionGraphDraftDocument,
)


class PredictionGraphDraftNotFoundError(LookupError):
    pass


class PredictionGraphDraftConflictError(RuntimeError):
    def __init__(
        self,
        message: str,
        current: PredictionGraphDraftDocument,
    ) -> None:
        super().__init__(message)
        self.current = current


def _content_json(content: PredictionGraphDraftContent) -> str:
    return json.dumps(
        content.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _document(row: object) -> PredictionGraphDraftDocument:
    return PredictionGraphDraftDocument(
        draft_id=str(row["draft_id"]),  # type: ignore[index]
        version=int(row["version"]),  # type: ignore[index]
        content=PredictionGraphDraftContent.model_validate_json(
            str(row["content_json"])  # type: ignore[index]
        ),
        created_at=str(row["created_at"]),  # type: ignore[index]
        updated_at=str(row["updated_at"]),  # type: ignore[index]
    )


class PredictionGraphDraftRepository:
    def create_prediction_graph_draft(
        self,
        *,
        draft_id: str,
        content: PredictionGraphDraftContent,
        now: datetime,
    ) -> PredictionGraphDraftDocument:
        timestamp = now.isoformat()
        with self._connect() as connection:  # type: ignore[attr-defined]
            connection.execute(
                "INSERT INTO prediction_graph_drafts("
                "draft_id,version,content_json,created_at,updated_at"
                ") VALUES (?,?,?,?,?)",
                (
                    draft_id,
                    1,
                    _content_json(content),
                    timestamp,
                    timestamp,
                ),
            )
        return PredictionGraphDraftDocument(
            draft_id=draft_id,
            version=1,
            content=content,
            created_at=now,
            updated_at=now,
        )
    def get_prediction_graph_draft(
        self,
        draft_id: str,
    ) -> PredictionGraphDraftDocument | None:
        with self._connect() as connection:  # type: ignore[attr-defined]
            row = connection.execute(
                "SELECT draft_id,version,content_json,created_at,updated_at "
                "FROM prediction_graph_drafts WHERE draft_id=?",
                (draft_id,),
            ).fetchone()
        return _document(row) if row is not None else None

    def replace_prediction_graph_draft(
        self,
        *,
        draft_id: str,
        expected_version: int,
        content: PredictionGraphDraftContent,
        now: datetime,
    ) -> PredictionGraphDraftDocument:
        timestamp = now.isoformat()
        content_json = _content_json(content)
        with self._connect() as connection:  # type: ignore[attr-defined]
            next_version = expected_version + 1
            updated = connection.execute(
                "UPDATE prediction_graph_drafts "
                "SET version=?,content_json=?,updated_at=? "
                "WHERE draft_id=? AND version=? "
                "RETURNING created_at",
                (
                    next_version,
                    content_json,
                    timestamp,
                    draft_id,
                    expected_version,
                ),
            ).fetchone()
        if updated is None:
            current = self.get_prediction_graph_draft(draft_id)
            if current is None:
                raise PredictionGraphDraftNotFoundError(
                    f"Prediction Graph draftが見つかりません: {draft_id}"
                )
            raise PredictionGraphDraftConflictError(
                "Prediction Graph draftは別の画面で更新されています",
                current,
            )
        return PredictionGraphDraftDocument(
            draft_id=draft_id,
            version=next_version,
            content=content,
            created_at=str(updated["created_at"]),
            updated_at=now,
        )
