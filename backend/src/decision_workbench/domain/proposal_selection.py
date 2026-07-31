"""Select a small, persisted proposal shortlist from evaluated screening points."""
from __future__ import annotations

from typing import Any

from decision_workbench.contracts.batch_proposal_contracts import (
    BatchProposalDefinition,
)
from decision_workbench.contracts.design_space_contracts import (
    DesignSpaceDefinition,
)
from decision_workbench.contracts.proposal_contracts import (
    ProposalStrategyDefinition,
    ProposalStrategyRequest,
)
from decision_workbench.domain.batch_selector import (
    BatchSelectionError,
    canonical_condition_digest,
    select_experiment_batch,
)
from decision_workbench.execution.inference_work_graph import semantic_digest


PROPOSAL_NEAR_DUPLICATE_THRESHOLD = 0.05


def _validate_diversity_distance(
    design_space: DesignSpaceDefinition,
    strategy: ProposalStrategyDefinition,
    points: list[dict[str, Any]],
) -> None:
    """Fail closed when the selected distance cannot represent the space."""

    declared_paths = {
        *(item.path for item in design_space.numeric_domains),
        *(item.path for item in design_space.categorical_domains),
        *(item.path for item in design_space.heat_pattern_domains),
    }
    if not declared_paths:
        raise ValueError("条件の違いを測るDesign Space軸がありません")
    missing_axes = sorted(
        {
            path
            for point in points
            for path in declared_paths
            if path not in point.get("inputs", {})
        }
    )
    if missing_axes:
        raise ValueError(
            "距離contractに必要な入力値がありません: "
            + ", ".join(missing_axes)
        )
    if design_space.conditional_constraints:
        raise ValueError(
            "条件付き変数の距離contractがないため、"
            "「条件が重ならないよう選ぶ」は利用できません"
        )
    has_varying_composition = any(
        path.startswith("composition.") for path in declared_paths
    )
    if has_varying_composition and (
        strategy.distance_id != "group_weighted_bounded_clr_rms"
    ):
        raise ValueError(
            "組成変数を扱う距離contractがないため、"
            "「条件が重ならないよう選ぶ」は利用できません"
        )
    supported_distance_ids = {
        "scalar_axis_rms",
        "group_weighted_bounded_clr_rms",
    }
    if strategy.distance_id not in supported_distance_ids:
        raise ValueError(
            f"提案選択に未対応の距離contractです: {strategy.distance_id}"
        )


def select_proposal_shortlist(
    points: list[dict[str, Any]],
    request: ProposalStrategyRequest,
    design_space: DesignSpaceDefinition,
    strategy: ProposalStrategyDefinition,
    *,
    seed: int,
) -> dict[str, Any]:
    """Reuse the allow-listed batch selector without claiming a batch plan."""

    if request.selection_policy == "greedy_value_diversity_v1":
        _validate_diversity_distance(design_space, strategy, points)
    if request.proposal_count > len(points):
        raise ValueError(
            f"提案件数{request.proposal_count}件は"
            f"評価済み点数{len(points)}件以下にしてください"
        )

    eligible = [
        point
        for point in points
        if all(
            item.get("achieved") is not False
            for item in point.get("secondary_goal_evaluations", {}).values()
        )
    ]
    unique_count = len(
        {canonical_condition_digest(point) for point in eligible}
    )
    actual_target = min(request.proposal_count, unique_count)
    shortfall_reason = (
        "副条件を満たす重複しない評価点が"
        f"{unique_count}件だったため、提案は{actual_target}件です"
        if actual_target < request.proposal_count
        else None
    )
    selected_run: dict[str, Any] | None = None
    last_error: BatchSelectionError | None = None
    while actual_target > 0:
        definition = BatchProposalDefinition(
            selector_id=request.selection_policy,
            batch_size=actual_target,
            candidate_pool_size=len(points),
            diversity_weight=request.diversity_weight,
            near_duplicate_threshold=PROPOSAL_NEAR_DUPLICATE_THRESHOLD,
        )
        try:
            selected_run = select_experiment_batch(
                [dict(point) for point in points],
                definition,
                design_space,
                seed=seed,
                reference_candidates={},
                distance_id=strategy.distance_id,
                distance_version=strategy.distance_version,
                distance_parameters=strategy.distance_parameters,
            )
            break
        except BatchSelectionError as exc:
            last_error = exc
            actual_target -= 1
            shortfall_reason = (
                "条件間の重なりを避けると"
                f"{actual_target}件までしか提案できませんでした: {exc}"
            )

    point_index_by_pool = {
        point["pool_index"]: point["index"] for point in points
    }
    selected = (
        []
        if selected_run is None
        else [
            {
                "point_index": point_index_by_pool[item["point"]["pool_index"]],
                "pool_index": item["point"]["pool_index"],
                "order": order,
                "acquisition_component": item["acquisition_component"],
                "diversity_component": item["diversity_component"],
                "combined_score": item["combined_score"],
                "canonical_identity_digest": item[
                    "canonical_identity_digest"
                ],
            }
            for order, item in enumerate(selected_run["selected"], start=1)
        ]
    )
    if not selected and last_error is not None:
        shortfall_reason = f"提案できる条件がありませんでした: {last_error}"
    return {
        "schema_version": "proposal-selection/v1",
        "requested_count": request.proposal_count,
        "actual_count": len(selected),
        "eligible_count": len(eligible),
        "unique_count": unique_count,
        "policy_id": request.selection_policy,
        "policy_version": (
            selected_run["selector_version"]
            if selected_run is not None
            else "1.1.0"
        ),
        "tie_break_rule": "combined_score_desc_then_pool_index_asc",
        "value_component_identity": "acquisition_rank_utility",
        "candidate_pool_digest": (
            selected_run["candidate_pool"]["pool_digest"]
            if selected_run is not None
            else semantic_digest([])
        ),
        "distance_id": strategy.distance_id,
        "distance_version": strategy.distance_version,
        "distance_parameters": strategy.distance_parameters,
        "requested_diversity_weight": request.diversity_weight,
        "effective_diversity_weight": (
            request.diversity_weight
            if request.selection_policy == "greedy_value_diversity_v1"
            else 0.0
        ),
        "near_duplicate_threshold": PROPOSAL_NEAR_DUPLICATE_THRESHOLD,
        "selected": selected,
        "shortfall_reason": shortfall_reason,
    }
