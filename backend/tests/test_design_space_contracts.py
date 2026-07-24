from __future__ import annotations

import pytest

from material_workbench.contracts.design_space_contracts import (
    CategoricalDomain,
    CompositionTotalConstraint,
    DesignSpaceDefinition,
    NumericDomain,
)
from material_workbench.contracts.task_contracts import NumericRange
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.tasks.task_registry import load_task_contracts


def _battery_space(**updates: object) -> DesignSpaceDefinition:
    task = load_task_contracts()["battery-degradation-v1"].task_definition
    payload = {
        "schema_version": "design-space-definition/v1",
        "design_space_id": "battery-safe-space",
        "name": "電池の安全な検討範囲",
        "task_id": "battery-degradation-v1",
        "task_contract_digest": semantic_digest(task.model_dump(mode="json")),
        "numeric_domains": (
            NumericDomain(
                path="process.ambient_temp_c",
                mode="range",
                range=NumericRange(min=20, max=40),
            ),
        ),
        "categorical_domains": (
            CategoricalDomain(
                path="categorical.cell_type",
                choices=("LFP_graphite", "NMC_graphite"),
            ),
        ),
    }
    payload.update(updates)
    return DesignSpaceDefinition.model_validate(payload)


def test_design_space_can_only_narrow_task_definition() -> None:
    task = load_task_contracts()["battery-degradation-v1"].task_definition
    _battery_space().validate_against(task)

    wider = _battery_space(numeric_domains=(
        NumericDomain(
            path="process.ambient_temp_c",
            mode="range",
            range=NumericRange(min=-100, max=120),
        ),
    ))
    with pytest.raises(ValueError, match="許容範囲"):
        wider.validate_against(task)


def test_design_space_categories_are_task_subset() -> None:
    task = load_task_contracts()["battery-degradation-v1"].task_definition
    invalid = _battery_space(categorical_domains=(
        CategoricalDomain(
            path="categorical.cell_type",
            choices=("unknown-cell",),
        ),
    ))
    with pytest.raises(ValueError, match="選択肢"):
        invalid.validate_against(task)


def test_composition_constraint_requires_declared_components_and_balance() -> None:
    task = load_task_contracts()["annealed-properties-v1"].task_definition
    invalid = DesignSpaceDefinition(
        schema_version="design-space-definition/v1",
        design_space_id="bad-simplex",
        name="不正な組成",
        task_id=task.id,
        task_contract_digest=semantic_digest(task.model_dump(mode="json")),
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
