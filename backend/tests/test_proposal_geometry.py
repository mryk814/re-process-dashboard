from datetime import UTC, datetime

import pytest

from material_workbench.application.proposal_strategy_registry import (
    strategy_availability,
)
from material_workbench.contracts.design_space_contracts import (
    CompositionTotalConstraint,
    ConditionalActivation,
    DesignSpaceDefinition,
    NumericDomain,
    default_design_space,
)
from material_workbench.contracts.objective_contracts import objective_from_screening
from material_workbench.contracts.schemas import Candidate, CandidateInput, ScreeningGoal
from material_workbench.contracts.task_contracts import NumericRange
from material_workbench.domain.proposal_generation import generate_candidates
from material_workbench.domain.proposal_geometry import proposal_distance
from material_workbench.domain.services import (
    _proposal_coverage,
    _validate_screening_pool,
)
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.tasks.task_registry import load_task_contracts


def _mpea_candidate(candidate_id: str = "mpea-base") -> Candidate:
    fixture = load_task_contracts()["mpea-hardness-process-v1"]
    canonical = fixture.canonical_candidate
    composition = {key: 0.0 for key in canonical.composition}
    composition["Fe"] = 100.0
    now = datetime.now(UTC)
    return Candidate(
        id=candidate_id,
        project_id="geometry-test",
        revision=1,
        created_at=now,
        updated_at=now,
        name=candidate_id,
        inputs={
            "composition": composition,
            "process": canonical.process,
            "categorical": canonical.categorical,
            "heat_pattern": None,
        },
        provenance=canonical.provenance,
    )


def _simplex_space(*, include_process: bool = False) -> DesignSpaceDefinition:
    fixture = load_task_contracts()["mpea-hardness-process-v1"]
    numeric = [
        NumericDomain(
            path="composition.Ni",
            mode="range",
            range=NumericRange(min=40, max=80),
        ),
        NumericDomain(
            path="composition.Co",
            mode="range",
            range=NumericRange(min=40, max=80),
        ),
    ]
    if include_process:
        numeric.extend(
            [
                NumericDomain(
                    path=f"process.axis_{index}",
                    mode="range",
                    range=NumericRange(min=0, max=1),
                )
                for index in range(8)
            ]
        )
    return DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="bounded-simplex-test",
        name="Bounded simplex test",
        task_id="mpea-hardness-process-v1",
        task_contract_digest=semantic_digest(
            fixture.task_definition.model_dump(mode="json")
        ),
        fixed_values={
            f"composition.{key}": 0.0
            for key in fixture.canonical_candidate.composition
            if key not in {"Ni", "Co", "Fe"}
        },
        numeric_domains=tuple(numeric),
        composition_constraints=(
            CompositionTotalConstraint(
                component_paths=tuple(
                    f"composition.{key}"
                    for key in fixture.canonical_candidate.composition
                ),
                total=100,
                tolerance=1e-9,
                unit="at%",
                balance_path="composition.Fe",
            ),
        ),
    )


def test_bounded_simplex_is_reproducible_and_avoids_negative_balance() -> None:
    base = _mpea_candidate()
    space = _simplex_space()
    generic = generate_candidates(
        "latin_hypercube",
        base,
        space,
        count=128,
        seed=213,
    )
    bounded = generate_candidates(
        "bounded_simplex_hit_and_run",
        base,
        space,
        count=128,
        seed=213,
        parameters={"minimum_balance": 0.0},
    )
    repeated = generate_candidates(
        "bounded_simplex_hit_and_run",
        base,
        space,
        count=128,
        seed=213,
        parameters={"minimum_balance": 0.0},
    )

    def nonnegative_balance(candidate: CandidateInput) -> None:
        if candidate.inputs.composition["Fe"] < 0:
            raise ValueError("negative_balance")

    generic_valid, generic_rejected, _ = _validate_screening_pool(
        generic, nonnegative_balance
    )
    bounded_valid, bounded_rejected, _ = _validate_screening_pool(
        bounded, nonnegative_balance
    )
    assert len(generic_valid) < 128
    assert generic_rejected == {"negative_balance": 113}
    assert len(bounded_valid) == 128
    assert bounded_rejected == {}
    assert [
        candidate.inputs.composition for candidate, _ in bounded
    ] == [
        candidate.inputs.composition for candidate, _ in repeated
    ]
    assert all(
        sum(candidate.inputs.composition.values()) == pytest.approx(100)
        and candidate.inputs.composition["Fe"] >= 0
        for candidate, _ in bounded
    )
    coverage = _proposal_coverage(bounded, space)
    assert set(coverage) == {"composition.Ni", "composition.Co"}
    assert all(item["normalized_span"] > 0.4 for item in coverage.values())


def test_bounded_simplex_is_symmetric_and_rejects_unknown_versions() -> None:
    base = _mpea_candidate()
    space = _simplex_space().model_copy(
        update={
            "numeric_domains": tuple(
                NumericDomain(
                    path=f"composition.{key}",
                    mode="range",
                    range=NumericRange(min=0, max=100),
                )
                for key in ("Ni", "Co", "Mn")
            ),
            "fixed_values": {
                f"composition.{key}": 0.0
                for key in base.inputs.composition
                if key not in {"Ni", "Co", "Mn", "Fe"}
            },
        }
    )
    generated = generate_candidates(
        "bounded_simplex_hit_and_run",
        base,
        space,
        count=4096,
        seed=213,
    )
    means = {
        key: sum(item.inputs.composition[key] for item, _ in generated)
        / len(generated)
        for key in ("Ni", "Co", "Mn", "Fe")
    }
    assert all(value == pytest.approx(25.0, abs=1.5) for value in means.values())
    with pytest.raises(ValueError, match="@99.0.0"):
        generate_candidates(
            "bounded_simplex_hit_and_run",
            base,
            space,
            count=1,
            seed=213,
            generator_version="99.0.0",
        )


def test_bounded_simplex_runs_on_thin_feasible_polytope_without_rejection() -> None:
    base = _mpea_candidate()
    keys = tuple(base.inputs.composition)
    space = _simplex_space().model_copy(
        update={
            "numeric_domains": tuple(
                NumericDomain(
                    path=f"composition.{key}",
                    mode="range",
                    range=NumericRange(min=0, max=7.15),
                )
                for key in keys
            ),
            "fixed_values": {},
        }
    )
    generated = generate_candidates(
        "bounded_simplex_hit_and_run",
        base,
        space,
        count=4,
        seed=213,
    )
    assert len(generated) == 4
    for candidate, _ in generated:
        values = list(candidate.inputs.composition.values())
        assert sum(values) == pytest.approx(100, abs=1e-8)
        assert all(0 <= value <= 7.15 + 1e-9 for value in values)


def test_bounded_simplex_uses_design_space_fixed_composition_not_base_value() -> None:
    base = _mpea_candidate()
    fixed_values = dict(_simplex_space().fixed_values)
    fixed_values["composition.Cr"] = 10.0
    space = _simplex_space().model_copy(
        update={"fixed_values": fixed_values}
    )
    generated = generate_candidates(
        "bounded_simplex_hit_and_run",
        base,
        space,
        count=8,
        seed=213,
    )
    for candidate, _ in generated:
        assert candidate.inputs.composition["Cr"] == 10.0
        assert sum(candidate.inputs.composition.values()) == pytest.approx(
            100, abs=1e-8
        )


def test_group_weighted_bounded_clr_does_not_dilute_composition_by_axis_count() -> None:
    base = _mpea_candidate("left")
    changed = base.model_copy(deep=True)
    changed.inputs.composition["Fe"] = 60
    changed.inputs.composition["Ni"] = 20
    changed.inputs.composition["Co"] = 20
    space = _simplex_space(include_process=True)
    for index in range(8):
        base.inputs.process[f"axis_{index}"] = 0.5
        changed.inputs.process[f"axis_{index}"] = 0.5

    generic = proposal_distance("scalar_axis_rms", base, changed, space)
    grouped = proposal_distance(
        "group_weighted_bounded_clr_rms",
        base,
        changed,
        space,
        parameters={"zero_replacement": 1e-6},
    )
    assert 0 < generic < grouped < 1
    with pytest.raises(ValueError, match="@99.0.0"):
        proposal_distance(
            "group_weighted_bounded_clr_rms",
            base,
            changed,
            space,
            distance_version="99.0.0",
        )


def test_bounded_simplex_strategy_is_capability_gated_by_design_space() -> None:
    fixture = load_task_contracts()["mpea-hardness-process-v1"]
    objective = objective_from_screening(
        task=fixture.task_definition,
        task_contract_digest=semantic_digest(
            fixture.task_definition.model_dump(mode="json")
        ),
        target="HV",
        target_goal=ScreeningGoal(direction="at_least", lower=300),
        secondary_goals={},
    )
    compatible = {
        item.definition.strategy_id: item
        for item in strategy_availability(
            fixture.runtime_capability,
            target="HV",
            target_kind="continuous",
            objective=objective,
            design_space=_simplex_space(),
        )
    }
    incompatible_space = _simplex_space().model_copy(
        update={
            "numeric_domains": (_simplex_space().numeric_domains[0],),
        }
    )
    incompatible = {
        item.definition.strategy_id: item
        for item in strategy_availability(
            fixture.runtime_capability,
            target="HV",
            target_kind="continuous",
            objective=objective,
            design_space=incompatible_space,
        )
    }
    assert compatible["bounded_simplex_goal_v1"].available
    assert (
        compatible["bounded_simplex_goal_v1"].definition.distance_id
        == "group_weighted_bounded_clr_rms"
    )
    assert not incompatible["bounded_simplex_goal_v1"].available
    assert "2項目以上" in incompatible["bounded_simplex_goal_v1"].reasons[0]

    impossible_space = _simplex_space().model_copy(
        update={
            "numeric_domains": tuple(
                domain.model_copy(
                    update={"range": NumericRange(min=60, max=80)}
                )
                for domain in _simplex_space().numeric_domains
            )
        }
    )
    conditional_space = _simplex_space().model_copy(
        update={
            "conditional_constraints": (
                ConditionalActivation(
                    controller_path="categorical.route",
                    active_choices=("A",),
                    inactive_values={"composition.Ni": 0.0},
                ),
            )
        }
    )
    for space, expected in (
        (impossible_space, "構成できません"),
        (conditional_space, "条件付き"),
    ):
        availability = {
            item.definition.strategy_id: item
            for item in strategy_availability(
                fixture.runtime_capability,
                target="HV",
                target_kind="continuous",
                objective=objective,
                design_space=space,
            )
        }
        assert not availability["bounded_simplex_goal_v1"].available
        assert expected in availability["bounded_simplex_goal_v1"].reasons[0]


def test_bounded_simplex_strategy_runs_through_api_and_persists_geometry(
    client,
) -> None:
    task_id = "mpea-hardness-process-v1"
    fixture = load_task_contracts()[task_id]
    options = client.get("/api/project-creation-options").json()
    dataset = next(
        item
        for item in options["datasets"]
        if task_id in item["supported_task_ids"]
    )
    dataset_view = dataset["dataset_views"][0]
    package = next(
        item for item in options["model_packages"] if item["task_id"] == task_id
    )
    task_digest = options["task_contract_digests"][task_id]
    created = client.post(
        "/api/projects",
        json={
            "name": "Bounded simplex API test",
            "task_id": task_id,
            "target_values": {"HV": 300},
            "dataset_view_revision_id": dataset_view["id"],
            "task_contract_digest": task_digest,
            "model_package_ref_id": package["id"],
            "model_package_manifest_digest": package["manifest_digest"],
            "design_space": default_design_space(
                fixture.task_definition,
                task_contract_digest=task_digest,
            ).model_dump(mode="json"),
            "design_space_binding_provenance": "explicit",
        },
    )
    assert created.status_code == 201, created.text
    project = created.json()
    canonical = fixture.canonical_candidate
    candidate_response = client.post(
        f"/api/projects/{project['id']}/candidates",
        json={
            "name": "MPEA基準",
            "inputs": {
                "composition": canonical.composition,
                "process": canonical.process,
                "categorical": canonical.categorical,
                "heat_pattern": None,
            },
            "provenance": canonical.provenance.model_dump(mode="json"),
        },
    )
    assert candidate_response.status_code == 201, candidate_response.text
    candidate = candidate_response.json()
    availability = client.get(
        f"/api/projects/{project['id']}/proposal-strategies",
        params={"target": "HV"},
    )
    assert availability.status_code == 200
    strategy = next(
        item
        for item in availability.json()
        if item["definition"]["strategy_id"] == "bounded_simplex_goal_v1"
    )
    assert strategy["available"]

    goal_body = {
        "purpose": "goal_search",
        "base_candidate_id": candidate["id"],
        "base_inputs": candidate["inputs"],
        "samples": 48,
        "seed": 213,
        "target": "HV",
        "target_goal": {"direction": "at_least", "lower": 300},
        "variables": {
            "composition.Ni": {
                "mode": "range",
                "min": 20,
                "max": 50,
            },
            "composition.Co": {
                "mode": "range",
                "min": 20,
                "max": 50,
            },
        },
        "proposal": {
            "strategy_id": "bounded_simplex_goal_v1",
            "pool_multiplier": 2,
            "support_policy": "supported_first",
            "fallback_policy": "reject",
        },
    }
    goal_response = client.post(
        "/api/screening",
        params={"project_id": project["id"]},
        json=goal_body,
    )
    assert goal_response.status_code == 201, goal_response.text
    response = client.post(
        "/api/screening",
        params={"project_id": project["id"]},
        json={
            **goal_body,
            "purpose": "experiment_batch",
            "source_run_id": goal_response.json()["id"],
            "batch_definition": {
                "batch_size": 4,
                "candidate_pool_size": 16,
                "near_duplicate_threshold": 0,
            },
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["proposal_strategy"]["generator_id"] == "bounded_simplex_hit_and_run"
    assert run["proposal_strategy"]["generator_version"] == "1.0.0"
    assert run["proposal_strategy"]["generator_parameters"]["minimum_balance"] == 0
    assert (
        run["proposal_strategy"]["distance_id"]
        == "group_weighted_bounded_clr_rms"
    )
    assert run["proposal_strategy"]["distance_version"] == "1.0.0"
    assert run["proposal_strategy"]["distance_parameters"]["zero_replacement"] == 1e-6
    assert run["proposal_diagnostics"]["rejected_count"] == 0
    assert set(run["proposal_diagnostics"]["coverage_by_path"]) >= {
        "composition.Ni",
        "composition.Co",
    }
    assert (
        run["batch_proposal"]["distance_id"]
        == "group_weighted_bounded_clr_rms"
    )
    assert (
        run["batch_proposal"]["distance_parameters"]
        == run["proposal_strategy"]["distance_parameters"]
    )
    restored = client.get(
        f"/api/screening/{run['id']}",
        params={"project_id": project["id"]},
    )
    assert restored.status_code == 200
    assert restored.json()["proposal_strategy"] == run["proposal_strategy"]
    assert restored.json()["batch_proposal"] == run["batch_proposal"]
