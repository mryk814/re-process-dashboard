from datetime import UTC, datetime

import pytest

from material_workbench.application.batch_selector_registry import (
    batch_selector_availability,
    require_batch_selector,
)
from material_workbench.contracts.batch_proposal_contracts import (
    BatchCategoryQuota,
    BatchControlRequirement,
    BatchProposalDefinition,
    BatchResourceConstraint,
)
from material_workbench.contracts.design_space_contracts import (
    CategoricalDomain,
    DesignSpaceDefinition,
    NumericDomain,
)
from material_workbench.contracts.candidate_project_contracts import Candidate
from material_workbench.contracts.task_contracts import NumericRange
from material_workbench.domain.batch_selector import select_experiment_batch
from material_workbench.domain.batch_selector import BatchSelectionError
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.tasks.task_registry import load_task_contracts


def _space(*, categorical: bool = False) -> DesignSpaceDefinition:
    fixture = load_task_contracts()["annealed-properties-v1"]
    return DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="batch-test",
        name="Batch test",
        task_id="annealed-properties-v1",
        task_contract_digest=semantic_digest(
            fixture.task_definition.model_dump(mode="json")
        ),
        numeric_domains=(
            NumericDomain(
                path="composition.C",
                mode="range",
                range=NumericRange(min=0, max=1),
            ),
        ),
        categorical_domains=(
            CategoricalDomain(
                path="categorical.route",
                choices=("A", "B"),
            ),
        )
        if categorical
        else (),
    )


def _point(
    pool_index: int,
    value: float,
    *,
    category: str = "A",
    score: float | None = None,
) -> dict:
    return {
        "pool_index": pool_index,
        "inputs": {
            "composition.C": value,
            "categorical.route": category,
        },
        "candidate": {},
        "score": value if score is None else score,
        "secondary_goal_evaluations": {},
    }


def _candidate(candidate_id: str, c_value: float) -> Candidate:
    fixture = load_task_contracts()["annealed-properties-v1"]
    canonical = fixture.canonical_candidate
    now = datetime.now(UTC)
    composition = dict(canonical.composition)
    composition["C"] = c_value
    return Candidate(
        id=candidate_id,
        project_id="test",
        revision=1,
        created_at=now,
        updated_at=now,
        name=candidate_id,
        inputs={
            "composition": composition,
            "process": canonical.process,
            "categorical": canonical.categorical,
            "heat_pattern": [
                point.model_dump() for point in canonical.heat_pattern or ()
            ],
        },
        provenance=canonical.provenance,
    )


def _exact_control(
    pool_index: int,
    candidate_id: str,
    value: float,
    *,
    category: str = "A",
) -> dict:
    return {
        **_point(pool_index, value, category=category),
        "_batch_source": "exact_control",
        "_candidate_id": candidate_id,
        "_candidate_revision": 1,
    }


def test_diversity_selector_includes_a_different_region() -> None:
    points = [
        _point(0, 0.00, score=0.00),
        _point(1, 0.01, score=0.01),
        _point(2, 0.90, score=0.20),
        _point(3, 0.50, score=0.30),
    ]
    top_k = select_experiment_batch(
        points,
        BatchProposalDefinition(
            selector_id="ranked_top_k_v1",
            batch_size=2,
            diversity_weight=0,
        ),
        _space(),
        seed=7,
        reference_candidates={},
    )
    diverse = select_experiment_batch(
        points,
        BatchProposalDefinition(
            batch_size=2,
            diversity_weight=2,
            near_duplicate_threshold=0,
        ),
        _space(),
        seed=7,
        reference_candidates={},
    )
    assert [item["point"]["pool_index"] for item in top_k["selected"]] == [0, 1]
    assert [item["point"]["pool_index"] for item in diverse["selected"]] == [0, 2]
    assert diverse["summary"]["min_pairwise_distance"] == pytest.approx(0.9)


def test_batch_selector_registry_does_not_claim_joint_methods_without_capability() -> None:
    capability = load_task_contracts()[
        "annealed-properties-v1"
    ].runtime_capability
    availability = {
        item.definition.selector_id: item
        for item in batch_selector_availability(capability, target="TS")
    }
    assert availability["ranked_top_k_v1"].available
    assert availability["greedy_value_diversity_v1"].available
    assert not availability["batch_thompson_v1"].available
    assert not availability["joint_q_acquisition_v1"].available
    assert any(
        "joint sample" in reason
        for reason in availability["joint_q_acquisition_v1"].reasons
    )
    with pytest.raises(ValueError, match="joint sample"):
        require_batch_selector(
            "joint_q_acquisition_v1",
            capability,
            target="TS",
        )


def test_pending_candidate_is_avoided_and_reported() -> None:
    points = [
        _point(0, 0.01, score=0),
        _point(1, 0.40, score=0.1),
        _point(2, 0.80, score=0.2),
    ]
    result = select_experiment_batch(
        points,
        BatchProposalDefinition(
            batch_size=2,
            pending_candidate_ids=("pending",),
            pending_policy="avoid",
            near_duplicate_threshold=0.1,
            diversity_weight=0,
        ),
        _space(),
        seed=11,
        reference_candidates={"pending": _candidate("pending", 0)},
    )
    assert 0 not in {
        item["point"]["pool_index"] for item in result["selected"]
    }
    assert {
        item["reason"] for item in result["excluded"] if item["pool_index"] == 0
    } == {"pending candidateとの近接を回避"}
    assert result["summary"]["pending_reference_count"] == 1


def test_control_replicates_quota_and_resource_contract_are_enforced() -> None:
    points = [
        _exact_control(10, "control", 0.10, category="A"),
        _point(0, 0.10, category="A", score=0),
        _point(1, 0.30, category="A", score=0.1),
        _point(2, 0.70, category="B", score=0.2),
        _point(3, 0.95, category="B", score=0.3),
    ]
    definition = BatchProposalDefinition(
        batch_size=4,
        controls=(BatchControlRequirement(candidate_id="control", replicates=2),),
        category_quotas=(
            BatchCategoryQuota(
                path="categorical.route",
                value="B",
                min_count=1,
                max_count=2,
            ),
        ),
        resources=BatchResourceConstraint(
            max_total_cost=4,
            setup_group_path="categorical.route",
            max_setup_groups=2,
        ),
        near_duplicate_threshold=0,
    )
    first = select_experiment_batch(
        points,
        definition,
        _space(categorical=True),
        seed=19,
        reference_candidates={"control": _candidate("control", 0.1)},
    )
    second = select_experiment_batch(
        points,
        definition,
        _space(categorical=True),
        seed=19,
        reference_candidates={"control": _candidate("control", 0.1)},
    )
    assert [item["role"] for item in first["selected"][:2]] == [
        "control",
        "replicate",
    ]
    assert first["selected"][0]["point"]["pool_index"] == first["selected"][1]["point"]["pool_index"]
    assert first["summary"]["category_counts"]["categorical.route=B"] >= 1
    assert first["summary"]["estimated_total_cost"] <= 4
    assert first["summary"]["setup_group_count"] <= 2
    assert max(
        item["acquisition_component"]
        for item in first["selected"]
        if item["source"] == "acquisition_ranked"
    ) == 1.0
    assert [
        (item["point"]["pool_index"], item["role"])
        for item in first["selected"]
    ] == [
        (item["point"]["pool_index"], item["role"])
        for item in second["selected"]
    ]


def test_resource_constraint_rejects_an_impossible_quota_combination() -> None:
    points = [
        _exact_control(10, "control", 0.10, category="A"),
        _point(0, 0.10, category="A"),
        _point(1, 0.80, category="B"),
    ]
    with pytest.raises(ValueError, match="category quota|batch size|resource"):
        select_experiment_batch(
            points,
            BatchProposalDefinition(
                batch_size=2,
                controls=(
                    BatchControlRequirement(candidate_id="control", replicates=1),
                ),
                category_quotas=(
                    BatchCategoryQuota(
                        path="categorical.route",
                        value="B",
                        min_count=1,
                    ),
                ),
                resources=BatchResourceConstraint(
                    setup_group_path="categorical.route",
                    max_setup_groups=1,
                ),
                near_duplicate_threshold=0,
            ),
            _space(categorical=True),
            seed=23,
            reference_candidates={"control": _candidate("control", 0.1)},
        )


def test_batch_constraints_cannot_silently_reference_outside_design_space() -> None:
    with pytest.raises(ValueError, match="Design Space外"):
        select_experiment_batch(
            [_point(0, 0.1), _point(1, 0.9)],
            BatchProposalDefinition(
                batch_size=1,
                category_quotas=(
                    BatchCategoryQuota(
                        path="categorical.undeclared",
                        value="X",
                        min_count=1,
                    ),
                ),
            ),
            _space(),
            seed=29,
            reference_candidates={},
        )


def test_screening_api_persists_batch_and_promotes_only_selected_conditions(client) -> None:
    selectors = client.get(
        "/api/projects/default/batch-selectors",
        params={"target": "TS"},
    )
    assert selectors.status_code == 200
    selector_by_id = {
        item["definition"]["selector_id"]: item for item in selectors.json()
    }
    assert selector_by_id["greedy_value_diversity_v1"]["available"]
    assert not selector_by_id["joint_q_acquisition_v1"]["available"]

    candidates = client.get("/api/projects/default/candidates").json()
    candidate = candidates[0]
    control_inputs = {
        **candidate["inputs"],
        "composition": {
            **candidate["inputs"]["composition"],
            "C": 0.10,
        },
    }
    control_response = client.post(
        "/api/projects/default/candidates",
        json={"name": "exact Control", "inputs": control_inputs},
    )
    assert control_response.status_code == 201, control_response.text
    control_candidate = control_response.json()
    goal_body = {
        "purpose": "goal_search",
        "base_candidate_id": candidate["id"],
        "base_inputs": candidate["inputs"],
        "samples": 48,
        "seed": 198,
        "target": "TS",
        "target_goal": {"direction": "at_least", "lower": 500},
        "variables": {
            "composition.C": {"mode": "range", "min": 0.04, "max": 0.12},
            "process.ls_mpm": {"mode": "range", "min": 80, "max": 130},
        },
    }
    goal_response = client.post("/api/screening", json=goal_body)
    assert goal_response.status_code == 201, goal_response.text
    body = {
        **goal_body,
        "purpose": "experiment_batch",
        "source_run_id": goal_response.json()["id"],
        "batch_definition": {
            "batch_size": 4,
            "candidate_pool_size": 24,
            "selector_id": "greedy_value_diversity_v1",
            "diversity_weight": 1.5,
            "near_duplicate_threshold": 0.01,
            "pending_candidate_ids": [candidate["id"]],
            "pending_policy": "penalize",
            "pending_penalty": 1,
            "controls": [
                {
                    "candidate_id": control_candidate["id"],
                    "candidate_revision": control_candidate["revision"],
                    "replicates": 2,
                }
            ],
        }
    }
    response = client.post("/api/screening", json=body)
    assert response.status_code == 201, response.text
    run = response.json()
    batch = run["batch_proposal"]
    assert batch["selector_id"] == "greedy_value_diversity_v1"
    assert len(batch["selected"]) == 4
    assert batch["candidate_pool"]["requested_acquisition_size"] == 24
    assert batch["candidate_pool"]["exact_control_count"] == 1
    assert batch["candidate_pool"]["pool_digest"].startswith("sha256:")
    assert batch["summary"]["pending_reference_count"] == 1
    assert all(item["reason"] for item in batch["selected"])
    assert [item["source"] for item in batch["selected"][:2]] == [
        "exact_control",
        "exact_control",
    ]
    assert batch["selected"][0]["candidate_id"] == control_candidate["id"]
    assert (
        batch["selected"][0]["candidate_revision"]
        == control_candidate["revision"]
    )
    assert batch["selected"][0]["point_index"] is None
    assert "exact Control" in batch["selected"][0]["reason"]
    point_indices = list(
        dict.fromkeys(
            item["point_index"]
            for item in batch["selected"]
            if item["point_index"] is not None
        )
    )
    created = client.post(
        f"/api/screening/{run['id']}/candidates",
        json={"point_indices": point_indices},
    )
    assert created.status_code == 201, created.text
    assert len(created.json()["candidates"]) == len(point_indices)
    restored = client.get(f"/api/screening/{run['id']}")
    assert restored.status_code == 200
    assert restored.json()["batch_proposal"] == batch

    outside_goal_body = {
        **goal_body,
        "variables": {
            **goal_body["variables"],
            "composition.C": {"mode": "range", "min": 0.075, "max": 0.09},
        },
    }
    outside_goal = client.post("/api/screening", json=outside_goal_body)
    assert outside_goal.status_code == 201, outside_goal.text
    outside = {
        **outside_goal_body,
        "purpose": "experiment_batch",
        "source_run_id": outside_goal.json()["id"],
        "batch_definition": body["batch_definition"],
    }
    rejected = client.post("/api/screening", json=outside)
    assert rejected.status_code == 422
    assert "exact Control候補がDesign Spaceを満たしません" in rejected.json()["message"]

    stale_control = {
        **body,
        "batch_definition": {
            **body["batch_definition"],
            "controls": [
                {
                    "candidate_id": control_candidate["id"],
                    "candidate_revision": control_candidate["revision"] + 1,
                    "replicates": 1,
                }
            ],
        },
    }
    stale = client.post("/api/screening", json=stale_control)
    assert stale.status_code == 422
    assert "revisionが選択後に変わりました" in stale.json()["message"]

    greedy_exhaustion = {
        **body,
        "batch_definition": {
            **body["batch_definition"],
            "candidate_pool_size": 4,
            "near_duplicate_threshold": 1,
            "pending_candidate_ids": [],
            "controls": [],
        },
    }
    exhausted = client.post("/api/screening", json=greedy_exhaustion)
    assert exhausted.status_code == 422
    assert exhausted.json()["code"] == "batch_greedy_search_exhausted"
    assert "数学的な実行可能解なしを意味しません" in exhausted.json()["message"]


def test_batch_pool_deduplicates_canonical_conditions_and_pins_evidence() -> None:
    result = select_experiment_batch(
        [
            _point(0, 0.1, score=0),
            _point(1, 0.1, score=0.1),
            _point(2, 0.8, score=0.2),
        ],
        BatchProposalDefinition(
            batch_size=2,
            candidate_pool_size=3,
            selector_id="ranked_top_k_v1",
        ),
        _space(),
        seed=31,
        reference_candidates={},
    )

    assert result["schema_version"] == "batch-proposal-run/v2"
    assert result["candidate_pool"]["requested_acquisition_size"] == 3
    assert result["candidate_pool"]["unique_condition_count"] == 2
    assert result["candidate_pool"]["duplicate_condition_count"] == 1
    assert result["candidate_pool"]["pool_digest"].startswith("sha256:")
    assert any("canonical identity" in item["reason"] for item in result["excluded"])


def test_greedy_exhaustion_is_not_reported_as_mathematical_infeasibility() -> None:
    with pytest.raises(BatchSelectionError) as captured:
        select_experiment_batch(
            [_point(0, 0.0), _point(1, 0.01), _point(2, 0.02)],
            BatchProposalDefinition(
                batch_size=2,
                candidate_pool_size=3,
                near_duplicate_threshold=0.5,
            ),
            _space(),
            seed=37,
            reference_candidates={},
        )

    assert captured.value.failure_kind == "greedy_search_exhausted"
    assert "数学的な実行可能解なしを意味しません" in str(captured.value)


def test_quota_greedy_dead_end_is_not_reported_as_infeasible() -> None:
    space = _space(categorical=True)
    space = space.model_copy(
        update={
            "categorical_domains": (
                *space.categorical_domains,
                CategoricalDomain(
                    path="categorical.setup",
                    choices=("X", "Y"),
                ),
            )
        }
    )
    points = [
        _point(0, 0.1, category="A"),
        _point(1, 0.2, category="A"),
        _point(2, 0.3, category="B"),
    ]
    points[0]["inputs"]["categorical.setup"] = "X"
    points[1]["inputs"]["categorical.setup"] = "Y"
    points[2]["inputs"]["categorical.setup"] = "Y"

    with pytest.raises(BatchSelectionError) as captured:
        select_experiment_batch(
            points,
            BatchProposalDefinition(
                batch_size=2,
                candidate_pool_size=3,
                category_quotas=(
                    BatchCategoryQuota(
                        path="categorical.route",
                        value="A",
                        min_count=1,
                    ),
                    BatchCategoryQuota(
                        path="categorical.route",
                        value="B",
                        min_count=1,
                    ),
                ),
                resources=BatchResourceConstraint(
                    setup_group_path="categorical.setup",
                    max_setup_groups=1,
                ),
                near_duplicate_threshold=0,
            ),
            space,
            seed=41,
            reference_candidates={},
        )

    assert captured.value.failure_kind == "greedy_search_exhausted"
    assert "数学的な実行可能解なしを意味しません" in str(captured.value)
