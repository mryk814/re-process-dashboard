"""Deterministic, explainable selection of an experiment batch."""
from __future__ import annotations

from collections import Counter
from typing import Any

from material_workbench.contracts.batch_proposal_contracts import (
    BatchProposalDefinition,
)
from material_workbench.contracts.design_space_contracts import DesignSpaceDefinition
from material_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
)
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.domain.proposal_geometry import (
    proposal_distance,
    scalar_axis_rms_distance,
)


class BatchSelectionError(ValueError):
    def __init__(
        self,
        failure_kind: str,
        message: str,
    ) -> None:
        self.failure_kind = failure_kind
        super().__init__(f"[{failure_kind}] {message}")


def _candidate_value(
    candidate: Candidate | CandidateInput,
    path: str,
) -> float | str | None:
    parts = path.split(".")
    if (
        len(parts) == 3
        and parts[0] == "heat_pattern"
        and parts[1].isdigit()
        and parts[2] in {"time_s", "temperature_c"}
    ):
        points = candidate.inputs.heat_pattern or ()
        index = int(parts[1])
        return getattr(points[index], parts[2]) if index < len(points) else None
    if len(parts) != 2:
        return None
    values = getattr(candidate.inputs, parts[0], {})
    return values.get(parts[1])


def _point_value(point: dict[str, Any], path: str) -> float | str | None:
    if path in point["inputs"]:
        return point["inputs"][path]
    candidate = CandidateInput.model_validate(point["candidate"])
    return _candidate_value(candidate, path)


def candidate_design_values(
    candidate: Candidate | CandidateInput,
    design_space: DesignSpaceDefinition,
) -> dict[str, float | str]:
    paths = (
        *(item.path for item in design_space.numeric_domains),
        *(item.path for item in design_space.categorical_domains),
        *(item.path for item in design_space.heat_pattern_domains),
    )
    return {
        path: value
        for path in paths
        if (value := _candidate_value(candidate, path)) is not None
    }


def canonical_condition_digest(point: dict[str, Any]) -> str:
    try:
        inputs = CandidateInput.model_validate(point["candidate"]).inputs.model_dump(
            mode="json"
        )
    except ValueError:
        inputs = point["inputs"]
    return semantic_digest(inputs)


def normalized_design_distance(
    left: dict[str, Any],
    right: dict[str, Any] | Candidate | CandidateInput,
    design_space: DesignSpaceDefinition,
) -> float:
    return scalar_axis_rms_distance(left, right, design_space)


def _matches(point: dict[str, Any], path: str, expected: str) -> bool:
    value = _point_value(point, path)
    return value is not None and str(value) == expected


def _estimated_cost(
    point: dict[str, Any],
    definition: BatchProposalDefinition,
) -> float:
    resource = definition.resources
    cost = resource.default_candidate_cost
    for rule in resource.cost_rules:
        if _matches(point, rule.path, rule.value):
            cost = rule.candidate_cost
    return float(cost)


def _setup_group(
    point: dict[str, Any],
    definition: BatchProposalDefinition,
) -> str | None:
    path = definition.resources.setup_group_path
    if not path:
        return None
    value = _point_value(point, path)
    return None if value is None else str(value)


def _pairwise_summary(
    selected: list[dict[str, Any]],
    design_space: DesignSpaceDefinition,
    *,
    distance_id: str,
    distance_version: str,
    distance_parameters: dict[str, float | str | bool],
) -> tuple[float, float]:
    unique = {
        item["point"]["pool_index"]: item["point"]
        for item in selected
    }
    points = list(unique.values())
    distances = [
        proposal_distance(
            distance_id,
            left,
            right,
            design_space,
            distance_version=distance_version,
            parameters=distance_parameters,
        )
        for index, left in enumerate(points)
        for right in points[index + 1 :]
    ]
    if not distances:
        return 0.0, 0.0
    return min(distances), sum(distances) / len(distances)


def select_experiment_batch(
    points: list[dict[str, Any]],
    definition: BatchProposalDefinition,
    design_space: DesignSpaceDefinition,
    *,
    seed: int,
    reference_candidates: dict[str, Candidate],
    distance_id: str = "scalar_axis_rms",
    distance_version: str = "1.0.0",
    distance_parameters: dict[str, float | str | bool] | None = None,
) -> dict[str, Any]:
    """Select a batch from an already acquisition-ranked shortlist.

    The selector never claims a joint acquisition.  It combines a normalized
    rank utility with Design-Space-normalized maximin distance and explicit
    pending/resource penalties.
    """

    distance_parameters = distance_parameters or {}
    declared_paths = {
        *design_space.fixed_values,
        *(item.path for item in design_space.numeric_domains),
        *(item.path for item in design_space.categorical_domains),
        *(item.path for item in design_space.heat_pattern_domains),
    }
    referenced_paths = {
        *(item.path for item in definition.category_quotas),
        *(item.path for item in definition.resources.cost_rules),
        *(
            (definition.resources.setup_group_path,)
            if definition.resources.setup_group_path
            else ()
        ),
    }
    unknown_paths = sorted(referenced_paths - declared_paths)
    if unknown_paths:
        raise ValueError(
            "batch constraintがDesign Space外のpathを参照しています: "
            + ", ".join(unknown_paths)
        )
    exact_controls = [
        point for point in points if point.get("_batch_source") == "exact_control"
    ]
    acquisition_ranked = [
        point for point in points if point.get("_batch_source") != "exact_control"
    ][: definition.candidate_pool_size]
    unique_points: list[dict[str, Any]] = []
    duplicate_exclusions: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for point in (*exact_controls, *acquisition_ranked):
        digest = canonical_condition_digest(point)
        point["_canonical_identity_digest"] = digest
        previous = seen.get(digest)
        if previous is not None:
            if (
                previous.get("_batch_source") == "exact_control"
                and point.get("_batch_source") == "exact_control"
            ):
                raise BatchSelectionError(
                    "feasibility_infeasible",
                    "異なるControl候補が同一のcanonical条件です。replicateへまとめてください",
                )
            duplicate_exclusions.append(
                {
                    "pool_index": point["pool_index"],
                    "reason": "canonical identityが候補pool内で重複",
                    "canonical_identity_digest": digest,
                }
            )
            continue
        seen[digest] = point
        unique_points.append(point)
    points = unique_points
    explicit_replicate_slots = sum(
        requirement.replicates - 1 for requirement in definition.controls
    )
    if definition.batch_size > len(points) + explicit_replicate_slots:
        raise BatchSelectionError(
            "feasibility_infeasible",
            "canonical重複除去後のbatch候補数がbatch size未満です",
        )
    hard_feasible = [
        point
        for point in points
        if all(
            item["achieved"] is not False
            for item in point["secondary_goal_evaluations"].values()
        )
    ]
    if len(hard_feasible) + explicit_replicate_slots < definition.batch_size:
        raise BatchSelectionError(
            "feasibility_infeasible",
            "副条件を満たす点だけではbatch sizeを満たせません。"
            "範囲または副条件を見直してください",
        )
    pending = [
        reference_candidates[candidate_id]
        for candidate_id in definition.pending_candidate_ids
    ]
    selected: list[dict[str, Any]] = []
    selected_counts: Counter[tuple[str, str]] = Counter()
    total_cost = 0.0
    setup_groups: set[str] = set()
    acquisition_feasible = [
        point
        for point in hard_feasible
        if point.get("_batch_source") != "exact_control"
    ]
    rank = {
        point["pool_index"]: index
        for index, point in enumerate(acquisition_feasible)
    }

    def quota_max_allows(point: dict[str, Any]) -> bool:
        return all(
            quota.max_count is None
            or not _matches(point, quota.path, quota.value)
            or selected_counts[(quota.path, quota.value)] < quota.max_count
            for quota in definition.category_quotas
        )

    def resource_allows(point: dict[str, Any]) -> bool:
        cost = _estimated_cost(point, definition)
        maximum = definition.resources.max_total_cost
        if maximum is not None and total_cost + cost > maximum + 1e-12:
            return False
        group = _setup_group(point, definition)
        groups = setup_groups | ({group} if group is not None else set())
        maximum_groups = definition.resources.max_setup_groups
        return maximum_groups is None or len(groups) <= maximum_groups

    def pending_distance(point: dict[str, Any]) -> float:
        if not pending:
            return 1.0
        return min(
            proposal_distance(
                distance_id,
                point,
                candidate,
                design_space,
                distance_version=distance_version,
                parameters=distance_parameters,
            )
            for candidate in pending
        )

    def duplicate_distance(point: dict[str, Any]) -> float:
        unique_selected = [
            item["point"]
            for item in selected
            if item["point"]["pool_index"] != point["pool_index"]
        ]
        if not unique_selected:
            return 1.0
        return min(
            proposal_distance(
                distance_id,
                point,
                other,
                design_space,
                distance_version=distance_version,
                parameters=distance_parameters,
            )
            for other in unique_selected
        )

    def can_select(point: dict[str, Any], *, allow_replicate: bool = False) -> bool:
        if not quota_max_allows(point) or not resource_allows(point):
            return False
        if not allow_replicate and any(
            item["point"]["pool_index"] == point["pool_index"]
            for item in selected
        ):
            return False
        if (
            not allow_replicate
            and definition.selector_id != "ranked_top_k_v1"
            and selected
            and duplicate_distance(point) < definition.near_duplicate_threshold
        ):
            return False
        distance = pending_distance(point)
        return not (
            definition.pending_policy == "avoid"
            and distance < definition.near_duplicate_threshold
        )

    def append(
        point: dict[str, Any],
        *,
        role: str,
        reason: str,
        combined_score: float,
        diversity_component: float,
        pending_component: float,
        resource_component: float,
    ) -> None:
        nonlocal total_cost
        cost = _estimated_cost(point, definition)
        group = _setup_group(point, definition)
        total_cost += cost
        if group is not None:
            setup_groups.add(group)
        for quota in definition.category_quotas:
            if _matches(point, quota.path, quota.value):
                selected_counts[(quota.path, quota.value)] += 1
        selected.append(
            {
                "point": point,
                "role": role,
                "reason": reason,
                "acquisition_component": (
                    0.0
                    if point.get("_batch_source") == "exact_control"
                    else 1.0
                    - rank[point["pool_index"]]
                    / max(1, len(acquisition_feasible) - 1)
                ),
                "diversity_component": diversity_component,
                "pending_penalty": pending_component,
                "resource_penalty": resource_component,
                "combined_score": combined_score,
                "estimated_cost": cost,
                "setup_group": group,
                "source": point.get("_batch_source", "acquisition_ranked"),
                "candidate_id": point.get("_candidate_id"),
                "candidate_revision": point.get("_candidate_revision"),
                "canonical_identity_digest": point["_canonical_identity_digest"],
            }
        )

    # Controls are exact, revision-pinned candidates injected by the application.
    for requirement in definition.controls:
        control = next(
            (
                point
                for point in hard_feasible
                if point.get("_batch_source") == "exact_control"
                and point.get("_candidate_id") == requirement.candidate_id
                and (
                    requirement.candidate_revision is None
                    or point.get("_candidate_revision")
                    == requirement.candidate_revision
                )
            ),
            None,
        )
        if control is None:
            raise BatchSelectionError(
                "feasibility_infeasible",
                f"exact Control候補 {requirement.candidate_id} が候補poolにありません",
            )
        if not quota_max_allows(control) or not resource_allows(control):
            raise BatchSelectionError(
                "feasibility_infeasible",
                f"exact Control候補 {requirement.candidate_id} がresource/quota制約を満たしません",
            )
        append(
            control,
            role="control",
            reason=(
                f"指定候補 {requirement.candidate_id} revision "
                f"{control['_candidate_revision']} をexact Controlとして固定"
            ),
            combined_score=1.0,
            diversity_component=0.0,
            pending_component=0.0,
            resource_component=0.0,
        )
        for replicate_index in range(1, requirement.replicates):
            if not resource_allows(control) or not quota_max_allows(control):
                raise BatchSelectionError(
                    "feasibility_infeasible",
                    "Control反復がresource constraintを超えます",
                )
            append(
                control,
                role="replicate",
                reason=f"control反復 {replicate_index + 1}/{requirement.replicates}",
                combined_score=1.0,
                diversity_component=0.0,
                pending_component=0.0,
                resource_component=0.0,
            )

    # Satisfy minimum category coverage before the free greedy phase.
    for quota in definition.category_quotas:
        while selected_counts[(quota.path, quota.value)] < quota.min_count:
            if len(selected) >= definition.batch_size:
                selected_non_controls = any(
                    item["source"] == "acquisition_ranked" for item in selected
                )
                raise BatchSelectionError(
                    (
                        "greedy_search_exhausted"
                        if selected_non_controls
                        else "feasibility_infeasible"
                    ),
                    f"category quotaを満たすbatch枠がありません: "
                    f"{quota.path}={quota.value}",
                )
            candidate = next(
                (
                    point
                    for point in hard_feasible
                    if _matches(point, quota.path, quota.value)
                    and can_select(point)
                ),
                None,
            )
            if candidate is None:
                matching_candidates_exist = any(
                    _matches(point, quota.path, quota.value)
                    for point in hard_feasible
                )
                raise BatchSelectionError(
                    (
                        "greedy_search_exhausted"
                        if matching_candidates_exist
                        else "feasibility_infeasible"
                    ),
                    (
                        f"greedy選抜ではcategory quotaを満たせません: "
                        f"{quota.path}={quota.value}。"
                        "これは数学的な実行可能解なしを意味しません"
                        if matching_candidates_exist
                        else f"category quotaを満たす候補がありません: "
                        f"{quota.path}={quota.value}"
                    ),
                )
            append(
                candidate,
                role="coverage",
                reason=f"{quota.path}={quota.value} の最小quotaを確保",
                combined_score=1.0,
                diversity_component=duplicate_distance(candidate),
                pending_component=0.0,
                resource_component=0.0,
            )

    while len(selected) < definition.batch_size:
        scored = []
        for point in hard_feasible:
            if not can_select(point):
                continue
            acquisition = 1.0 - rank[point["pool_index"]] / max(
                1, len(acquisition_feasible) - 1
            )
            diversity = duplicate_distance(point)
            pending_gap = pending_distance(point)
            pending_component = (
                definition.pending_penalty
                * max(
                    0.0,
                    definition.near_duplicate_threshold - pending_gap,
                )
                / max(definition.near_duplicate_threshold, 1e-12)
                if definition.pending_policy == "penalize"
                else 0.0
            )
            group = _setup_group(point, definition)
            new_setup = group is not None and group not in setup_groups
            resource_component = (
                definition.resources.setup_change_penalty if new_setup else 0.0
            )
            combined = (
                acquisition
                if definition.selector_id == "ranked_top_k_v1"
                else acquisition
                + definition.diversity_weight * diversity
                - pending_component
                - resource_component
            )
            scored.append(
                (
                    -combined,
                    point["pool_index"],
                    point,
                    acquisition,
                    diversity,
                    pending_component,
                    resource_component,
                )
            )
        if not scored:
            raise BatchSelectionError(
                "greedy_search_exhausted",
                "greedy選抜が候補を追加できなくなりました。"
                "これは数学的な実行可能解なしを意味しません",
            )
        (
            negative_score,
            _,
            point,
            acquisition,
            diversity,
            pending_component,
            resource_component,
        ) = min(scored)
        role = (
            "performance"
            if not selected or definition.selector_id == "ranked_top_k_v1"
            else "diversity"
        )
        append(
            point,
            role=role,
            reason=(
                f"獲得順位価値 {acquisition:.3f}、多様性 {diversity:.3f}、"
                f"pending減点 {pending_component:.3f}、資源減点 {resource_component:.3f}"
            ),
            combined_score=-negative_score,
            diversity_component=diversity,
            pending_component=pending_component,
            resource_component=resource_component,
        )

    selected_pool_indices = {
        item["point"]["pool_index"] for item in selected
    }
    excluded = list(duplicate_exclusions)
    for point in hard_feasible:
        if point["pool_index"] in selected_pool_indices:
            continue
        reason = "batch全体の価値で選抜外"
        if (
            pending
            and definition.pending_policy == "avoid"
            and pending_distance(point) < definition.near_duplicate_threshold
        ):
            reason = "pending candidateとの近接を回避"
        elif (
            definition.selector_id != "ranked_top_k_v1"
            and selected
            and duplicate_distance(point) < definition.near_duplicate_threshold
        ):
            reason = "選抜済み条件とのnear-duplicate"
        elif not resource_allows(point):
            reason = "resource constraint"
        excluded.append(
            {
                "pool_index": point["pool_index"],
                "reason": reason,
                "canonical_identity_digest": point["_canonical_identity_digest"],
            }
        )

    minimum, mean = _pairwise_summary(
        selected,
        design_space,
        distance_id=distance_id,
        distance_version=distance_version,
        distance_parameters=distance_parameters,
    )
    category_counts = {
        f"{quota.path}={quota.value}": selected_counts[(quota.path, quota.value)]
        for quota in definition.category_quotas
    }
    return {
        "schema_version": "batch-proposal-run/v2",
        "selector_id": definition.selector_id,
        "selector_version": "1.1.0",
        "distance_id": distance_id,
        "distance_version": distance_version,
        "distance_parameters": distance_parameters,
        "seed": seed,
        "tie_break_rule": "combined_score_desc_then_pool_index_asc",
        "definition": definition.model_dump(mode="json"),
        "selected": selected,
        "excluded": excluded,
        "summary": {
            "batch_size": definition.batch_size,
            "min_pairwise_distance": minimum,
            "mean_pairwise_distance": mean,
            "estimated_total_cost": total_cost,
            "setup_group_count": len(setup_groups),
            "category_counts": category_counts,
            "pending_reference_count": len(pending),
        },
        "candidate_pool": {
            "source": "acquisition_ranked_prefix_plus_exact_controls",
            "requested_acquisition_size": definition.candidate_pool_size,
            "acquisition_ranked_count": len(acquisition_ranked),
            "exact_control_count": len(exact_controls),
            "unique_condition_count": len(points),
            "duplicate_condition_count": len(duplicate_exclusions),
            "canonicalization": "candidate-inputs-semantic-digest",
            "pool_digest": semantic_digest(
                [
                    {
                        "pool_index": point["pool_index"],
                        "source": point.get("_batch_source", "acquisition_ranked"),
                        "candidate_id": point.get("_candidate_id"),
                        "candidate_revision": point.get("_candidate_revision"),
                        "canonical_identity_digest": point[
                            "_canonical_identity_digest"
                        ],
                    }
                    for point in points
                ]
            ),
        },
    }
