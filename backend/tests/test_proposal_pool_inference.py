from __future__ import annotations

from types import SimpleNamespace

import pytest

from decision_workbench.contracts.prediction_catalog_contracts import Support
from decision_workbench.application.proposal_service import _evaluate_proposal_pool


def _support() -> Support:
    return Support(
        status="supported",
        distance=0.1,
        percentile=20.0,
        message="supported",
        components={"all_inputs": 0.1},
        reference_count=10,
        supported_threshold=0.2,
        caution_threshold=0.3,
    )


class _OrderedBatchRuntime:
    supports_batch_prediction = True

    def __init__(self, *, reverse: bool = False, drop_last: bool = False):
        self.reverse = reverse
        self.drop_last = drop_last

    def predict_batch(self, candidates, **_):
        rows = [
            {
                "candidate_id": candidate.id,
                "warnings": [],
                "support": _support(),
                "similar": [],
            }
            for candidate in candidates
        ]
        if self.reverse:
            rows.reverse()
        if self.drop_last:
            rows.pop()
        return rows


class _ScalarRuntime:
    def __init__(self):
        self.support_calls: list[str] = []

    def predict_core(self, candidate, **_):
        return {"candidate_id": candidate.id, "warnings": []}

    def support_summary(self, candidate):
        self.support_calls.append(candidate.id)
        return _support()


def _candidates():
    return [
        SimpleNamespace(
            id="shared-base-id",
            model_copy=lambda *, update: SimpleNamespace(**update),
        ),
        SimpleNamespace(
            id="shared-base-id",
            model_copy=lambda *, update: SimpleNamespace(**update),
        ),
    ]


def test_pool_evaluator_preserves_scalar_order_and_adds_support() -> None:
    runtime = _ScalarRuntime()

    rows = _evaluate_proposal_pool(
        runtime,
        _candidates(),
        target_values={},
    )

    assert [row["candidate_id"] for row in rows] == [
        "shared-base-id",
        "shared-base-id",
    ]
    assert runtime.support_calls == [
        "shared-base-id:proposal-pool:0",
        "shared-base-id:proposal-pool:1",
    ]
    assert all(row["support"].status == "supported" for row in rows)
    assert all(row["similar"] == [] for row in rows)


@pytest.mark.parametrize(
    ("runtime", "message"),
    [
        (
            _OrderedBatchRuntime(reverse=True),
            "batch prediction did not preserve candidate order",
        ),
        (
            _OrderedBatchRuntime(drop_last=True),
            "batch prediction did not preserve candidate cardinality",
        ),
    ],
)
def test_pool_evaluator_rejects_invalid_batch_contract(
    runtime,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _evaluate_proposal_pool(
            runtime,
            _candidates(),
            target_values={},
        )
