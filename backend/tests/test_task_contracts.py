from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from material_workbench.data.importer import load_workbook_data
from material_workbench.contracts.task_contracts import (
    CanonicalCandidate,
    RuntimeCapability,
    TaskContractFixture,
    TaskDefinition,
)


FIXTURE_ROOT = Path(__file__).parents[1] / "src" / "material_workbench" / "tasks" / "task_definitions"
SOURCE_WORKBOOK = Path(__file__).parents[2] / "data" / "source" / "material_workbench_tutorial_v1.xlsx"


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("name", "expected_task", "expected_outputs", "has_heat_pattern"),
    [
        ("annealed-properties-v1.json", "annealed-properties-v1", {"TS", "YS", "EL", "lambda"}, True),
        ("hot-rolled-properties-v1.json", "hot-rolled-properties-v1", {"TS"}, False),
    ],
)
def test_task_fixtures_share_one_contract(
    name: str,
    expected_task: str,
    expected_outputs: set[str],
    has_heat_pattern: bool,
) -> None:
    fixture = TaskContractFixture.model_validate(load_fixture(name))

    assert fixture.task_definition.id == expected_task
    assert {output.key for output in fixture.task_definition.outputs} == expected_outputs
    assert all(output.measurement_keys for output in fixture.task_definition.outputs)
    assert all(output.plausibility_range is not None for output in fixture.task_definition.outputs)
    assert all(output.preferred_display_range is not None for output in fixture.task_definition.outputs)
    assert (fixture.canonical_candidate.heat_pattern is not None) is has_heat_pattern
    assert fixture.runtime_capability.task_id == expected_task
    expected_display_keys = {
        *(field.path for group in fixture.task_definition.input_groups for field in group.fields if field.kind == "number"),
        *(f"output.{output.key}" for output in fixture.task_definition.outputs),
    }
    assert set(fixture.task_definition.display_decimals) == expected_display_keys


def test_fixtures_freeze_complete_task_specific_input_sets() -> None:
    annealed = TaskContractFixture.model_validate(load_fixture("annealed-properties-v1.json"))
    hot_rolled = TaskContractFixture.model_validate(load_fixture("hot-rolled-properties-v1.json"))

    assert len(next(group for group in annealed.task_definition.input_groups if group.key == "composition").fields) == 14
    assert len(next(group for group in hot_rolled.task_definition.input_groups if group.key == "composition").fields) == 14
    assert {
        field.path
        for field in next(group for group in hot_rolled.task_definition.input_groups if group.key == "process").fields
    } == {
        "process.soaking_temperature_c",
        "process.finish_temperature_c",
        "process.entry_thickness_mm",
        "process.exit_thickness_mm",
        "process.hold_temperature_c",
        "process.hold_time_min",
    }
    assert hot_rolled.task_definition.fixed_context == ()
    response_variables = annealed.task_definition.response_curve_variables
    assert [item.path for item in response_variables[:14]] == [f"composition.{name}" for name in ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")]
    assert (response_variables[14].path, response_variables[14].time_transform) == ("process.ls_mpm", "inverse_heat_time")
    assert response_variables[15].kind == "heat_stage_temperature"
    assert [item.path for item in hot_rolled.task_definition.response_curve_variables] == [
        *(f"composition.{name}" for name in ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")),
        "process.soaking_temperature_c",
        "process.finish_temperature_c",
        "process.entry_thickness_mm",
        "process.exit_thickness_mm",
        "process.hold_temperature_c",
        "process.hold_time_min",
    ]
    assert hot_rolled.runtime_capability.operations.response_curve is True


@pytest.mark.parametrize(
    ("fixture_name", "source_name", "expected_parent_count"),
    [
        ("annealed-properties-v1.json", "焼鈍引張", 6),
        ("hot-rolled-properties-v1.json", "熱延引張", 6),
    ],
)
def test_fixture_reference_source_has_expected_parent_conditions(
    fixture_name: str, source_name: str, expected_parent_count: int
) -> None:
    fixture = TaskContractFixture.model_validate(load_fixture(fixture_name))
    data = load_workbook_data(SOURCE_WORKBOOK)
    rows = [
        row
        for row in data.observations
        if row["source"] == source_name
        and row["eligible"]
        and row["features"]
        and row["composition"]
    ]
    parent_rows = {str(row["parent_key"]): row for row in rows}
    assert len(parent_rows) == expected_parent_count



def test_contract_models_publish_machine_readable_json_schemas() -> None:
    for model in (TaskDefinition, CanonicalCandidate, RuntimeCapability, TaskContractFixture):
        schema = model.model_json_schema()
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_duplicate_field_path_is_rejected() -> None:
    raw = load_fixture("annealed-properties-v1.json")
    task = raw["task_definition"]
    composition = task["input_groups"][0]
    composition["fields"].append(copy.deepcopy(composition["fields"][0]))
    composition["fields"][-1]["order"] = 99

    with pytest.raises(ValidationError, match="field paths must be unique"):
        TaskDefinition.model_validate(task)


def test_display_decimals_require_every_numeric_variable_and_valid_range() -> None:
    raw = load_fixture("annealed-properties-v1.json")
    task = raw["task_definition"]
    task["display_decimals"].pop("composition.C")
    with pytest.raises(ValidationError, match="display_decimals must define"):
        TaskDefinition.model_validate(task)

    task = load_fixture("annealed-properties-v1.json")["task_definition"]
    task["display_decimals"]["composition.C"] = 9
    with pytest.raises(ValidationError, match="less than or equal to 8"):
        TaskDefinition.model_validate(task)


def test_unknown_group_is_rejected() -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    task = raw["task_definition"]
    task["input_groups"][1]["key"] = "rolling"

    with pytest.raises(ValidationError, match="Input should be"):
        TaskDefinition.model_validate(task)


@pytest.mark.parametrize(
    "range_name",
    ["default_range", "allowed_range", "training_range"],
)
def test_non_ascending_numeric_ranges_are_rejected(range_name: str) -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    task = raw["task_definition"]
    field = task["input_groups"][1]["fields"][0]
    field[range_name] = {"min": 100.0, "max": 100.0}

    with pytest.raises(ValidationError, match="range min must be less than max"):
        TaskDefinition.model_validate(task)


def test_default_and_training_ranges_must_be_inside_allowed_range() -> None:
    raw = load_fixture("annealed-properties-v1.json")
    task = raw["task_definition"]
    field = task["input_groups"][0]["fields"][0]
    field["training_range"] = {"min": -1.0, "max": 0.5}

    with pytest.raises(ValidationError, match="training_range must be contained"):
        TaskDefinition.model_validate(task)


def test_output_ranges_must_be_explicit_and_preferred_must_be_plausible() -> None:
    task = load_fixture("annealed-properties-v1.json")["task_definition"]
    task["outputs"][0].pop("plausibility_range")
    with pytest.raises(ValidationError, match="plausibility_range"):
        TaskDefinition.model_validate(task)

    task = load_fixture("annealed-properties-v1.json")["task_definition"]
    task["outputs"][0]["preferred_display_range"] = {"min": 0, "max": 3000}
    with pytest.raises(ValidationError, match="preferred_display_range must be contained"):
        TaskDefinition.model_validate(task)


def test_relational_constraints_must_reference_required_fields() -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    task = raw["task_definition"]
    task["input_groups"][1]["fields"][0]["required"] = False

    with pytest.raises(ValidationError, match="must reference required fields"):
        TaskDefinition.model_validate(task)


@pytest.mark.parametrize("value", [True, "0.12"])
def test_canonical_numeric_values_do_not_coerce_non_numbers(value: object) -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    raw["canonical_candidate"]["composition"]["C"] = value

    with pytest.raises(ValidationError, match="valid number"):
        CanonicalCandidate.model_validate(raw["canonical_candidate"])


@pytest.mark.parametrize("field", ["time_s", "temperature_c"])
@pytest.mark.parametrize("value", [True, "10.0"])
def test_heat_pattern_values_do_not_coerce_non_numbers(field: str, value: object) -> None:
    raw = load_fixture("annealed-properties-v1.json")
    raw["canonical_candidate"]["heat_pattern"][0][field] = value

    with pytest.raises(ValidationError, match="valid number"):
        CanonicalCandidate.model_validate(raw["canonical_candidate"])


def test_fixture_rejects_runtime_targets_not_declared_by_task() -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    raw["runtime_capability"]["targets"].append(
        {
            "target": "YS",
            "point_statistics": ["mean"],
            "standard_deviation": True,
            "quantiles": True,
            "samples": False,
            "parametric_distribution": True,
            "uncertainty_components": False,
            "support": True,
            "warnings": True,
            "goal_probability": "distribution",
        }
    )

    with pytest.raises(ValidationError, match="must exactly match task outputs"):
        TaskContractFixture.model_validate(raw)


def test_fixture_rejects_candidate_group_not_declared_by_task() -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    raw["canonical_candidate"]["heat_pattern"] = [
        {"time_s": 0.0, "temperature_c": 25.0},
        {"time_s": 10.0, "temperature_c": 800.0},
    ]

    with pytest.raises(ValidationError, match="candidate provides undeclared group"):
        TaskContractFixture.model_validate(raw)


def test_provenance_source_kind_controls_source_ref_shape() -> None:
    raw = load_fixture("annealed-properties-v1.json")
    raw["canonical_candidate"]["provenance"] = {
        "source_kind": "lineage",
        "source_ref": {"run_id": "wrong-shape", "point_id": "point-1"},
    }

    with pytest.raises(ValidationError):
        TaskContractFixture.model_validate(raw)


@pytest.mark.parametrize(
    "provenance",
    [
        {"source_kind": "direct", "source_ref": None},
        {"source_kind": "copy", "source_ref": {"project_id": "default", "candidate_id": "candidate-1", "candidate_revision": 1}},
    ],
)
def test_direct_and_copy_provenance_are_typed(provenance: dict[str, object]) -> None:
    raw = load_fixture("annealed-properties-v1.json")
    raw["canonical_candidate"]["provenance"] = provenance

    fixture = TaskContractFixture.model_validate(raw)

    assert fixture.canonical_candidate.provenance.source_kind == provenance["source_kind"]


def test_runtime_goal_probability_requires_its_declared_representation() -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    target = raw["runtime_capability"]["targets"][0]
    target["goal_probability"] = "samples"
    target["samples"] = False

    with pytest.raises(ValidationError, match="requires samples"):
        TaskContractFixture.model_validate(raw)


def test_normal_approximation_requires_mean_and_standard_deviation() -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    target = raw["runtime_capability"]["targets"][0]
    target["goal_probability"] = "normal_approximation"
    target["standard_deviation"] = False

    with pytest.raises(ValidationError, match="requires mean and standard deviation"):
        TaskContractFixture.model_validate(raw)


def test_ui_layout_fields_are_not_part_of_the_task_contract() -> None:
    raw = load_fixture("annealed-properties-v1.json")
    raw["task_definition"]["layout"] = {"left_panel_width": 320}

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskDefinition.model_validate(raw["task_definition"])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("finish_temperature_c", 1250.0, "仕上げ温度は均熱温度以下"),
        ("exit_thickness_mm", 40.0, "出側板厚は入側板厚より小さく"),
    ],
)
def test_hot_rolling_relational_constraints_are_enforced(
    field: str, value: float, message: str
) -> None:
    raw = load_fixture("hot-rolled-properties-v1.json")
    raw["canonical_candidate"]["process"][field] = value

    with pytest.raises(ValidationError, match=message):
        TaskContractFixture.model_validate(raw)
