from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from decision_workbench.contracts.candidate_project_contracts import Candidate, CandidateInput
from decision_workbench.contracts.design_space_contracts import DesignSpaceDefinition, NumericDomain, default_design_space
from decision_workbench.contracts.task_contracts import (
    InputFieldDefinition,
    NumericRange,
    persisted_task_definition_payload,
)
from decision_workbench.domain.design_space_validation import validate_candidate_in_design_space
from decision_workbench.domain.proposal_generation import generate_candidates
from decision_workbench.modeling.curve_grid import numeric_domain_grid
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.application.workspace_catalog_bootstrap import task_definition_digest
from decision_workbench.tasks.task_registry import load_task_contracts


def _field(**updates: object) -> InputFieldDefinition:
    payload = {
        "path": "process.cycles",
        "kind": "number",
        "order": 0,
        "label": "Cycle",
        "default_range": {"min": 0.0, "max": 10.0},
        "allowed_range": {"min": 0.0, "max": 20.0},
        "training_range": {"min": 0.0, "max": 10.0},
    }
    payload.update(updates)
    return InputFieldDefinition.model_validate(payload)


def _candidate() -> Candidate:
    now = datetime.now(UTC)
    return Candidate(
        id="numeric-domain-base",
        project_id="numeric-domain-project",
        revision=1,
        created_at=now,
        updated_at=now,
        name="base",
        inputs={"composition": {}, "process": {"cycles": 4.0}, "categorical": {}, "heat_pattern": None},
    )


def test_task_numeric_domain_rejects_misaligned_step_integer_and_log_ranges() -> None:
    step = _field(numeric_domain_kind="step", step=0.5)
    step.validate_numeric_value(1.5)
    with pytest.raises(ValueError, match="align to step"):
        step.validate_numeric_value(1.3)

    with pytest.raises(ValidationError, match="allowed_range bounds must align to step"):
        _field(
            numeric_domain_kind="step",
            step=0.5,
            allowed_range={"min": 0.0, "max": 19.9},
        )
    with pytest.raises(ValidationError, match="default_range bounds must be integers"):
        _field(
            numeric_domain_kind="integer",
            default_range={"min": 0.5, "max": 10.0},
        )
    with pytest.raises(ValidationError, match="log scale number fields require positive ranges"):
        _field(search_scale="log")


def test_candidate_and_proposal_share_step_lattice_with_narrowed_range() -> None:
    domain = NumericDomain(
        path="process.cycles",
        mode="range",
        range=NumericRange(min=2.0, max=8.0),
        numeric_domain_kind="step",
        step=0.5,
        step_origin=0.0,
    )
    space = DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="step-space",
        name="step",
        task_id="test",
        task_contract_digest="sha256:test",
        numeric_domains=(domain,),
    )
    valid = CandidateInput.model_validate(_candidate().model_dump(mode="json"))
    validate_candidate_in_design_space(valid, space)
    invalid = valid.model_copy(deep=True)
    invalid.inputs.process["cycles"] = 2.2
    with pytest.raises(ValueError, match="align to step"):
        validate_candidate_in_design_space(invalid, space)

    generated = generate_candidates("latin_hypercube", _candidate(), space, count=24, seed=671)
    values = [candidate.inputs.process["cycles"] for candidate, _ in generated]
    assert all(2.0 <= value <= 8.0 and value * 2 == pytest.approx(round(value * 2)) for value in values)


def test_narrowing_cannot_move_a_step_lattice_origin() -> None:
    parent = DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="parent-step-space",
        name="parent",
        task_id="test",
        task_contract_digest="sha256:test",
        numeric_domains=(NumericDomain(
            path="process.cycles", mode="range", range=NumericRange(min=0.0, max=10.0),
            numeric_domain_kind="step", step=0.5, step_origin=0.0,
        ),),
    )
    child = parent.model_copy(update={"numeric_domains": (NumericDomain(
        path="process.cycles", mode="range", range=NumericRange(min=2.0, max=8.0),
        numeric_domain_kind="step", step=0.5, step_origin=0.0,
    ),)})
    child.validate_narrows(parent)
    moved_origin = child.model_copy(update={"numeric_domains": (NumericDomain(
        path="process.cycles", mode="range", range=NumericRange(min=2.0, max=8.0),
        numeric_domain_kind="step", step=0.5, step_origin=2.0,
    ),)})
    with pytest.raises(ValueError, match="数値domainを変更"):
        moved_origin.validate_narrows(parent)


def test_default_design_space_copies_task_integer_and_log_semantics() -> None:
    task = load_task_contracts()["battery-degradation-v1"].task_definition
    design_space = default_design_space(
        task,
        task_contract_digest=semantic_digest(persisted_task_definition_payload(task)),
    )
    domains = {domain.path: domain for domain in design_space.numeric_domains}
    assert domains["process.cycle_index"].numeric_domain_kind == "integer"
    assert domains["process.discharge_rate_c"].search_scale == "log"


def test_default_numeric_semantics_preserve_the_legacy_project_task_digest() -> None:
    contracts = load_task_contracts()
    task_id = "concrete-strength-v1"
    definition = contracts[task_id].task_definition
    legacy_payload = definition.model_dump(mode="json")
    for group in legacy_payload["input_groups"]:
        for field in group["fields"]:
            field.pop("numeric_domain_kind")
            field.pop("step")
            field.pop("search_scale")
    for output in legacy_payload["outputs"]:
        for key in ("target_kind", "binary", "count", "ordinal"):
            output.pop(key, None)

    class Registry:
        def contract_for(self, requested_task_id: str):
            return contracts[requested_task_id]

    registry = Registry()
    assert task_definition_digest(registry, task_id) == semantic_digest(legacy_payload)
    battery_legacy_payload = contracts[
        "battery-degradation-v1"
    ].task_definition.model_dump(mode="json")
    for group in battery_legacy_payload["input_groups"]:
        for field in group["fields"]:
            field.pop("numeric_domain_kind")
            field.pop("step")
            field.pop("search_scale")
    for output in battery_legacy_payload["outputs"]:
        for key in ("target_kind", "binary", "count", "ordinal"):
            output.pop(key, None)
    assert task_definition_digest(
        registry, "battery-degradation-v1"
    ) != semantic_digest(battery_legacy_payload)


def test_response_sampling_snaps_and_deduplicates_integer_step_and_log_domains() -> None:
    integer = _field(
        numeric_domain_kind="integer",
        default_range={"min": 1.0, "max": 10.0},
        allowed_range={"min": 1.0, "max": 20.0},
        training_range={"min": 1.0, "max": 10.0},
    )
    assert numeric_domain_grid(1.0, 3.0, 15, field=integer) == [1.0, 2.0, 3.0]

    stepped = _field(numeric_domain_kind="step", step=0.5)
    assert numeric_domain_grid(1.0, 2.0, 15, field=stepped) == [1.0, 1.5, 2.0]

    logarithmic = _field(
        search_scale="log",
        default_range={"min": 1.0, "max": 100.0},
        allowed_range={"min": 1.0, "max": 1000.0},
        training_range={"min": 1.0, "max": 100.0},
    )
    values = numeric_domain_grid(1.0, 100.0, 3, field=logarithmic)
    assert values == pytest.approx([1.0, 10.0, 100.0])


def test_response_sampling_never_clamps_a_lattice_point_to_an_invalid_requested_bound() -> None:
    integer = _field(
        numeric_domain_kind="integer",
        default_range={"min": 1.0, "max": 10.0},
        allowed_range={"min": 1.0, "max": 20.0},
        training_range={"min": 1.0, "max": 10.0},
    )
    values = numeric_domain_grid(1.0000000005, 3.8, 15, field=integer)
    assert values == [2.0, 3.0]
    assert all(value.is_integer() for value in values)
