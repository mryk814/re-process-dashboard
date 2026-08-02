from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from decision_workbench.contracts.prediction_graph_draft_contracts import (
    PredictionGraphDraftCreateRequest,
    PredictionGraphDraftDocument,
    PredictionGraphDraftUpdateRequest,
)
from decision_workbench.persistence.prediction_graph_draft_repository import (
    PredictionGraphDraftNotFoundError,
)
from decision_workbench.persistence.store import Store


class PredictionGraphDraftUseCases:
    def __init__(self, store: Store) -> None:
        self.store = store

    def create(
        self,
        payload: PredictionGraphDraftCreateRequest,
    ) -> PredictionGraphDraftDocument:
        return self.store.create_prediction_graph_draft(
            draft_id=str(uuid4()),
            content=payload.content,
            now=datetime.now(UTC),
        )

    def get(self, draft_id: str) -> PredictionGraphDraftDocument:
        draft = self.store.get_prediction_graph_draft(draft_id)
        if draft is None:
            raise PredictionGraphDraftNotFoundError(
                f"Prediction Graph draftが見つかりません: {draft_id}"
            )
        return draft

    def update(
        self,
        draft_id: str,
        payload: PredictionGraphDraftUpdateRequest,
    ) -> PredictionGraphDraftDocument:
        return self.store.replace_prediction_graph_draft(
            draft_id=draft_id,
            expected_version=payload.expected_version,
            content=payload.content,
            now=datetime.now(UTC),
        )
