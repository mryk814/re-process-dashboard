import pytest

from material_workbench.contracts.design_space_contracts import (
    CategoricalDomain,
    CompositionTotalConstraint,
    ConditionalActivation,
    DesignSpaceDefinition,
    NumericDomain,
)
from material_workbench.contracts.candidate_project_contracts import CandidateInput
from material_workbench.contracts.task_contracts import NumericRange, RelationalConstraint
from material_workbench.domain.design_space_validation import (
    validate_candidate_in_design_space,
)


def _candidate() -> CandidateInput:
    return CandidateInput.model_validate(
        {
            "name": "Design Space検証",
            "inputs": {
                "composition": {"C": 0.08, "Mn": 1.50},
                "process": {"hold_s": 60.0},
                "categorical": {"route": "direct"},
                "heat_pattern": [
                    {"time_s": 0, "temperature_c": 25},
                    {"time_s": 60, "temperature_c": 800},
                ],
            },
        }
    )


def _space() -> DesignSpaceDefinition:
    return DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="validation-test",
        name="検証用",
        task_id="test-task",
        task_contract_digest="sha256:test",
        numeric_domains=(
            NumericDomain(
                path="composition.C",
                mode="range",
                range=NumericRange(min=0.07, max=0.09),
            ),
            NumericDomain(
                path="composition.Mn",
                mode="range",
                range=NumericRange(min=1.4, max=1.6),
            ),
            NumericDomain(
                path="process.hold_s",
                mode="range",
                range=NumericRange(min=30, max=90),
            ),
        ),
        categorical_domains=(
            CategoricalDomain(path="categorical.route", choices=("direct", "reheat")),
        ),
        conditional_constraints=(
            ConditionalActivation(
                controller_path="categorical.route",
                active_choices=("reheat",),
                inactive_values={"process.hold_s": 60.0},
            ),
        ),
        relational_constraints=(
            RelationalConstraint(
                left_path="composition.C",
                operator="lt",
                right_path="composition.Mn",
                message="CはMnより小さくしてください",
            ),
        ),
        composition_constraints=(
            CompositionTotalConstraint(
                component_paths=("composition.C", "composition.Mn"),
                total=1.58,
                tolerance=1e-9,
                unit="mass%",
            ),
        ),
    )


def test_project_design_space_validates_all_constraint_families() -> None:
    validate_candidate_in_design_space(_candidate(), _space())

    outside = _candidate()
    outside.inputs.composition["C"] = 0.10
    with pytest.raises(ValueError, match="範囲外"):
        validate_candidate_in_design_space(outside, _space())

    conditional = _candidate()
    conditional.inputs.process["hold_s"] = 61.0
    with pytest.raises(ValueError, match="条件付き固定値"):
        validate_candidate_in_design_space(conditional, _space())

    relational = _candidate()
    relational.inputs.composition = {"C": 1.50, "Mn": 0.08}
    relational_space = _space().model_copy(
        update={
            "numeric_domains": (),
            "composition_constraints": (),
        }
    )
    with pytest.raises(ValueError, match="CはMnより小さく"):
        validate_candidate_in_design_space(relational, relational_space)

    composition = _candidate()
    composition.inputs.composition["Mn"] = 1.49
    with pytest.raises(ValueError, match="組成合計"):
        validate_candidate_in_design_space(composition, _space())
