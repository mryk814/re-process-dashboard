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
from material_workbench.contracts.schemas import Candidate
from material_workbench.contracts.task_contracts import NumericRange
from material_workbench.domain.batch_selector import select_experiment_batch
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
    assert [
        (item["point"]["pool_index"], item["role"])
        for item in first["selected"]
    ] == [
        (item["point"]["pool_index"], item["role"])
        for item in second["selected"]
    ]


def test_resource_constraint_rejects_an_impossible_quota_combination() -> None:
    points = [
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

    candidate = client.get("/api/projects/default/candidates").json()[0]
    body = {
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
        "batch_definition": {
            "batch_size": 4,
            "selector_id": "greedy_value_diversity_v1",
            "diversity_weight": 1.5,
            "near_duplicate_threshold": 0.01,
            "pending_candidate_ids": [candidate["id"]],
            "pending_policy": "penalize",
            "pending_penalty": 1,
        },
    }
    response = client.post("/api/screening", json=body)
    assert response.status_code == 201, response.text
    run = response.json()
    batch = run["batch_proposal"]
    assert batch["selector_id"] == "greedy_value_diversity_v1"
    assert len(batch["selected"]) == 4
    assert batch["summary"]["pending_reference_count"] == 1
    assert all(item["reason"] for item in batch["selected"])
    point_indices = list(
        dict.fromkeys(item["point_index"] for item in batch["selected"])
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
