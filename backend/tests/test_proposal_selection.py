import pytest

from material_workbench.contracts.design_space_contracts import (
    CategoricalDomain,
    ConditionalActivation,
    DesignSpaceDefinition,
    NumericDomain,
)
from material_workbench.contracts.proposal_contracts import (
    ProposalStrategyRequest,
)
from material_workbench.contracts.candidate_project_contracts import CandidateInput
from material_workbench.contracts.task_contracts import NumericRange
from material_workbench.domain.proposal_selection import (
    select_proposal_shortlist,
)
from material_workbench.application.proposal_strategy_registry import STRATEGIES
from material_workbench.tasks.task_registry import load_task_contracts


def _space(
    *,
    numeric: tuple[str, ...] = ("process.ls_mpm",),
    categorical: tuple[str, ...] = (),
    conditional: bool = False,
) -> DesignSpaceDefinition:
    return DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="proposal-selection-test",
        name="Proposal selection test",
        task_id="annealed-properties-v1",
        task_contract_digest="sha256:test",
        numeric_domains=tuple(
            NumericDomain(
                path=path,
                mode="range",
                range=NumericRange(min=0, max=100),
            )
            for path in numeric
        ),
        categorical_domains=tuple(
            CategoricalDomain(path=path, choices=("a", "b"))
            for path in categorical
        ),
        conditional_constraints=(
            (
                ConditionalActivation(
                    controller_path=categorical[0],
                    active_choices=("a",),
                    inactive_values={numeric[0]: 0},
                ),
            )
            if conditional
            else ()
        ),
    )


def _point(
    pool_index: int,
    value: float,
    *,
    inputs: dict[str, float | str] | None = None,
    category: str | None = None,
) -> dict:
    canonical = load_task_contracts()[
        "annealed-properties-v1"
    ].canonical_candidate
    candidate = CandidateInput(
        name=f"point-{pool_index}",
        inputs={
            "composition": dict(canonical.composition),
            "process": {
                **canonical.process,
                "ls_mpm": value,
            },
            "categorical": (
                {**canonical.categorical, "route": category}
                if category is not None
                else dict(canonical.categorical)
            ),
            "heat_pattern": (
                None
                if canonical.heat_pattern is None
                else [item.model_dump() for item in canonical.heat_pattern]
            ),
        },
        provenance=canonical.provenance,
    )
    return {
        "index": pool_index,
        "pool_index": pool_index,
        "inputs": inputs
        if inputs is not None
        else {"process.ls_mpm": value},
        "candidate": candidate.model_dump(mode="json"),
        "score": float(pool_index),
        "secondary_goal_evaluations": {},
    }


def test_ranked_and_diverse_policies_reuse_the_same_selector_identity() -> None:
    points = [
        _point(0, 0),
        _point(1, 1),
        _point(2, 2),
        _point(3, 100),
    ]
    strategy = STRATEGIES[0]
    ranked = select_proposal_shortlist(
        points,
        ProposalStrategyRequest(
            proposal_count=2,
            selection_policy="ranked_top_k_v1",
        ),
        _space(),
        strategy,
        seed=7,
    )
    diverse = select_proposal_shortlist(
        points,
        ProposalStrategyRequest(
            proposal_count=2,
            selection_policy="greedy_value_diversity_v1",
            diversity_weight=10,
        ),
        _space(),
        strategy,
        seed=7,
    )

    assert [item["pool_index"] for item in ranked["selected"]] == [0, 1]
    assert [item["pool_index"] for item in diverse["selected"]] == [0, 3]
    assert ranked["policy_version"] == diverse["policy_version"] == "1.1.0"
    assert ranked["effective_diversity_weight"] == 0
    assert diverse["effective_diversity_weight"] == 10


def test_selection_reports_canonical_shortfall_without_inventing_points() -> None:
    points = [_point(0, 0), _point(1, 0), _point(2, 100)]
    evidence = select_proposal_shortlist(
        points,
        ProposalStrategyRequest(
            proposal_count=3,
            selection_policy="ranked_top_k_v1",
        ),
        _space(),
        STRATEGIES[0],
        seed=9,
    )

    assert evidence["requested_count"] == 3
    assert evidence["actual_count"] == 2
    assert evidence["unique_count"] == 2
    assert evidence["shortfall_reason"]


@pytest.mark.parametrize("proposal_count", [1, 10])
def test_selection_accepts_public_count_boundaries(proposal_count: int) -> None:
    evidence = select_proposal_shortlist(
        [
            _point(index, float(index * 10))
            for index in range(10)
        ],
        ProposalStrategyRequest(proposal_count=proposal_count),
        _space(),
        STRATEGIES[0],
        seed=4,
    )

    assert evidence["requested_count"] == proposal_count
    assert evidence["actual_count"] == proposal_count


def test_selection_rejects_count_above_evaluated_points() -> None:
    with pytest.raises(ValueError, match="評価済み点数"):
        select_proposal_shortlist(
            [_point(0, 0), _point(1, 100)],
            ProposalStrategyRequest(proposal_count=3),
            _space(),
            STRATEGIES[0],
            seed=1,
        )


def test_diversity_distance_fails_closed_for_unsupported_spaces() -> None:
    request = ProposalStrategyRequest(
        proposal_count=2,
        selection_policy="greedy_value_diversity_v1",
    )
    points = [_point(0, 0), _point(1, 100)]
    with pytest.raises(ValueError, match="組成変数を扱う距離contract"):
        select_proposal_shortlist(
            [
                _point(0, 0, inputs={"composition.C": 0.1}),
                _point(1, 100, inputs={"composition.C": 0.2}),
            ],
            request,
            _space(numeric=("composition.C",)),
            STRATEGIES[0],
            seed=1,
        )
    with pytest.raises(ValueError, match="条件付き変数の距離contract"):
        select_proposal_shortlist(
            [
                _point(
                    0,
                    0,
                    inputs={"process.ls_mpm": 0, "categorical.route": "a"},
                    category="a",
                ),
                _point(
                    1,
                    100,
                    inputs={
                        "process.ls_mpm": 100,
                        "categorical.route": "b",
                    },
                    category="b",
                ),
            ],
            request,
            _space(
                categorical=("categorical.route",),
                conditional=True,
            ),
            STRATEGIES[0],
            seed=1,
        )
    with pytest.raises(ValueError, match="必要な入力値"):
        select_proposal_shortlist(
            [points[0], _point(1, 100, inputs={})],
            request,
            _space(),
            STRATEGIES[0],
            seed=1,
        )


def test_declared_category_uses_the_explicit_zero_one_distance_contract() -> None:
    evidence = select_proposal_shortlist(
        [
            _point(
                0,
                0,
                inputs={"categorical.route": "a"},
                category="a",
            ),
            _point(
                1,
                0,
                inputs={"categorical.route": "b"},
                category="b",
            ),
        ],
        ProposalStrategyRequest(
            proposal_count=2,
            selection_policy="greedy_value_diversity_v1",
        ),
        _space(numeric=(), categorical=("categorical.route",)),
        STRATEGIES[0],
        seed=1,
    )

    assert evidence["actual_count"] == 2
