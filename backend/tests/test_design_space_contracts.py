from __future__ import annotations

import pytest
from datetime import UTC, datetime

from decision_workbench.contracts.design_space_contracts import (
    CategoricalDomain,
    CompositionTotalConstraint,
    DesignSpaceDefinition,
    NumericDomain,
)
from decision_workbench.contracts.task_contracts import (
    NumericRange,
    persisted_task_definition_payload,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.tasks.task_registry import load_task_contracts
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    CandidateInput,
)
from decision_workbench.application.proposal_service import (
    _validate_screening_pool,
    generate_from_design_space,
)


def _battery_space(**updates: object) -> DesignSpaceDefinition:
    task = load_task_contracts()["battery-degradation-v1"].task_definition
    payload = {
        "schema_version": "design-space-definition/v1",
        "design_space_id": "battery-safe-space",
        "name": "電池の安全な検討範囲",
        "task_id": "battery-degradation-v1",
        "task_contract_digest": semantic_digest(
            persisted_task_definition_payload(task)
        ),
        "numeric_domains": (
            NumericDomain(
                path="process.discharge_rate_c",
                mode="range",
                range=NumericRange(min=0.5, max=1.0),
                search_scale="log",
            ),
        ),
        "categorical_domains": (),
    }
    payload.update(updates)
    return DesignSpaceDefinition.model_validate(payload)


def test_design_space_can_only_narrow_task_definition() -> None:
    task = load_task_contracts()["battery-degradation-v1"].task_definition
    _battery_space().validate_against(task)

    wider = _battery_space(numeric_domains=(
        NumericDomain(
            path="process.discharge_rate_c",
            mode="range",
            range=NumericRange(min=0.01, max=8),
            search_scale="log",
        ),
    ))
    with pytest.raises(ValueError, match="許容範囲"):
        wider.validate_against(task)


def test_design_space_categories_are_task_subset() -> None:
    task = load_task_contracts()["heat-treatment-tradeoff-v1"].task_definition
    invalid = DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="invalid-alloy-family",
        name="不正な合金区分",
        task_id=task.id,
        task_contract_digest=semantic_digest(persisted_task_definition_payload(task)),
        categorical_domains=(CategoricalDomain(
            path="categorical.alloy_family",
            choices=("unknown-alloy",),
        ),),
    )
    with pytest.raises(ValueError, match="選択肢"):
        invalid.validate_against(task)


def test_composition_constraint_requires_declared_components_and_balance() -> None:
    task = load_task_contracts()["annealed-properties-v1"].task_definition
    invalid = DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="bad-simplex",
        name="不正な組成",
        task_id=task.id,
        task_contract_digest=semantic_digest(persisted_task_definition_payload(task)),
        composition_constraints=(
            CompositionTotalConstraint(
                component_paths=("composition.C", "composition.missing"),
                total=100,
                tolerance=0.1,
                unit="at%",
                balance_path="composition.C",
            ),
        ),
    )
    with pytest.raises(ValueError, match="宣言済み組成"):
        invalid.validate_against(task)


def test_design_space_generator_applies_simplex_balance() -> None:
    task_id = "annealed-properties-v1"
    fixture = load_task_contracts()[task_id]
    canonical = fixture.canonical_candidate
    now = datetime.now(UTC)
    base = Candidate(
        id="design-space-base", project_id="test", revision=1, created_at=now, updated_at=now,
        name="base", inputs={
            "composition": canonical.composition,
            "process": canonical.process,
            "categorical": canonical.categorical,
            "heat_pattern": [point.model_dump() for point in canonical.heat_pattern or ()],
        }, provenance=canonical.provenance,
    )
    space = DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="simplex-space", name="Simplex", task_id=task_id,
        task_contract_digest=semantic_digest(
            persisted_task_definition_payload(fixture.task_definition)
        ),
        numeric_domains=(NumericDomain(
            path="composition.C", mode="range", range=NumericRange(min=1, max=2),
        ),),
        composition_constraints=(CompositionTotalConstraint(
            component_paths=("composition.C", "composition.Si"), total=100, unit="%",
            balance_path="composition.Si",
        ),),
    )
    space.validate_against(fixture.task_definition)
    generated = generate_from_design_space(base, space, count=12)
    assert len(generated) == 12
    assert all(
        candidate.inputs.composition["C"] + candidate.inputs.composition["Si"] == pytest.approx(100)
        and applied["composition.Si"] == candidate.inputs.composition["Si"]
        for candidate, applied in generated
    )


def test_screening_pool_diagnostics_validate_every_generated_candidate() -> None:
    task_id = "annealed-properties-v1"
    fixture = load_task_contracts()[task_id]
    canonical = fixture.canonical_candidate
    now = datetime.now(UTC)
    base = Candidate(
        id="screening-pool-base", project_id="test", revision=1, created_at=now, updated_at=now,
        name="base", inputs={
            "composition": canonical.composition,
            "process": canonical.process,
            "categorical": canonical.categorical,
            "heat_pattern": [point.model_dump() for point in canonical.heat_pattern or ()],
        }, provenance=canonical.provenance,
    )
    space = DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="screening-pool", name="Pool", task_id=task_id,
        task_contract_digest=semantic_digest(
            persisted_task_definition_payload(fixture.task_definition)
        ),
        numeric_domains=(NumericDomain(
            path="composition.C", mode="range", range=NumericRange(min=0.01, max=0.2),
        ),),
    )
    generated = generate_from_design_space(base, space, count=12)
    calls = 0

    def reject_after_first_three(_: CandidateInput) -> None:
        nonlocal calls
        calls += 1
        if calls > 3:
            raise ValueError("outside_demo_constraint")

    valid, rejected, rejection_rows = _validate_screening_pool(
        generated, reject_after_first_three
    )

    assert calls == 12
    assert len(valid) == 3
    assert rejected == {"outside_demo_constraint": 9}
    assert len(rejection_rows) == 9
    assert {item["pool_index"] for item in rejection_rows} == set(range(3, 12))
