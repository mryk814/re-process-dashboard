from __future__ import annotations

import pytest

from decision_workbench.application.records import (
    RecordValidationError,
    normalize_actual_measurement,
)
from decision_workbench.contracts.prediction_catalog_contracts import ActualMeasurementInput
from decision_workbench.contracts.task_contracts import (
    OutputDefinition,
    persisted_task_definition_payload,
)
from decision_workbench.contracts.objective_contracts import ObjectiveDefinition, ObjectiveTerm
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.tasks.task_registry import load_task_contracts


def _input(*, mean: float | None = None, value: float | str | bool | None = None) -> ActualMeasurementInput:
    return ActualMeasurementInput(
        property="output", mean=mean, value=value, std=0, replicates=1, unit="1"
    )


def test_binary_actual_normalizes_legacy_numeric_and_event_label_to_one_typed_value() -> None:
    output = load_task_contracts()["secom-yield-risk-v1"].task_definition.outputs[0]

    legacy = normalize_actual_measurement(_input(mean=1), output)
    labelled = normalize_actual_measurement(_input(value="異常"), output)

    assert (legacy.mean, legacy.value_label) == (1.0, "異常")
    assert (labelled.mean, labelled.value_label) == (1.0, "異常")


def test_count_and_ordinal_actuals_reject_regression_shaped_values() -> None:
    count = OutputDefinition(
        key="defects", label="欠陥数", unit="個", target_kind="count",
        count={"count_unit": "個"}, goal_direction="at_most",
        plausibility_range={"min": 0, "max": 100}, preferred_display_range={"min": 0, "max": 20},
    )
    ordinal = OutputDefinition(
        key="grade", label="等級", unit="1", target_kind="ordinal",
        ordinal={"categories": ["低", "中", "高"]}, goal_direction="at_least",
        plausibility_range={"min": 0, "max": 2}, preferred_display_range={"min": 0, "max": 2},
    )

    assert normalize_actual_measurement(_input(value=3), count).mean == 3
    assert normalize_actual_measurement(_input(value="高"), ordinal).value_label == "高"
    with pytest.raises(RecordValidationError, match="整数"):
        normalize_actual_measurement(_input(value=1.5), count)
    with pytest.raises(RecordValidationError, match="順序カテゴリ"):
        normalize_actual_measurement(_input(value="任意カテゴリ"), ordinal)


def test_value_label_is_server_owned_and_cleared_for_continuous_actuals() -> None:
    with pytest.raises(ValueError, match="Task契約"):
        ActualMeasurementInput(
            property="output", mean=1, value_label="偽のラベル", std=0, replicates=1, unit="MPa"
        )
    continuous = OutputDefinition(
        key="strength", label="強度", unit="MPa", goal_direction="at_least",
        plausibility_range={"min": 0, "max": 1000}, preferred_display_range={"min": 0, "max": 800},
    )
    internal = _input(mean=12).model_copy(update={"value_label": "内部値"})
    assert normalize_actual_measurement(internal, continuous).value_label is None


def test_existing_secom_binary_objective_keeps_legacy_runtime_capability_compatible() -> None:
    fixture = load_task_contracts()["secom-yield-risk-v1"]
    objective = ObjectiveDefinition(
        objective_id="secom-risk", name="異常確率を下げる", task_id=fixture.task_definition.id,
        task_contract_digest=semantic_digest(
            persisted_task_definition_payload(fixture.task_definition)
        ),
        optimization_kind="single_objective",
        terms=(ObjectiveTerm(output_key="fail_probability", unit="1", role="primary_objective", direction="at_most", upper=0.1),),
    )
    objective.validate_against(fixture.task_definition, fixture.runtime_capability)
