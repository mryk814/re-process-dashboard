from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import Workbook

from decision_workbench.developer_experience.readiness import (
    build_readiness_inventory,
    preflight_source,
    readiness_catalog,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "readiness"
SCRIPT_PATH = ROOT / "backend" / "scripts" / "operations" / "readiness_inventory.py"


def test_catalog_covers_the_supported_intake_shapes() -> None:
    catalog = readiness_catalog()

    assert {entry.source_shape for entry in catalog.entries} == {
        "independent_rows", "repeated_measurements", "longitudinal_curve",
        "wide_multi_target", "relational_workbook", "variable_length_series",
    }
    assert next(entry for entry in catalog.entries if entry.source_shape == "variable_length_series").state == "task_slice_needed"


def test_preflight_keeps_group_time_and_technical_roles_out_of_candidate_inputs() -> None:
    repeated = preflight_source(FIXTURES / "grouped-repeat.csv", target_columns=("strength_mpa",))
    curve = preflight_source(FIXTURES / "cell-curve.csv", target_columns=("capacity_percent",))

    assert repeated.source_shape == "repeated_measurements"
    assert repeated.route == "observation_authoring"
    assert any("観測IDの一意性" in item for item in repeated.ambiguities)
    assert {column.name for column in repeated.columns if column.suggested_role == "grouping"} == {"run_id"}
    assert curve.source_shape == "longitudinal_curve"
    assert curve.route == "profile_workbench"
    assert {column.name for column in curve.columns if column.suggested_role == "grouping"} == {"cell_id"}
    assert {column.name for column in curve.columns if column.suggested_role == "time_axis"} == {"cycle_index"}
    assert {column.name for column in curve.columns if column.suggested_role == "technical_metadata"} == {"instrument_version"}
    assert {column.name for column in curve.columns if column.suggested_role == "candidate_input"} == {"discharge_rate_c"}


def test_preflight_protects_shipped_observation_metadata(tmp_path: Path) -> None:
    source = tmp_path / "observation-export.csv"
    source.write_text(
        "composition.C,test_date,試験日,試験者,試験片番号,strength_mpa\n"
        "0.05,2026-01-01,2026-01-01,A,T-1,500\n",
        encoding="utf-8",
    )

    result = preflight_source(source, target_columns=("strength_mpa",))

    technical = {
        column.name
        for column in result.columns
        if column.suggested_role == "technical_metadata"
    }
    assert technical == {"test_date", "試験日", "試験者", "試験片番号"}
    assert {
        column.name
        for column in result.columns
        if column.suggested_role == "candidate_input"
    } == {"composition.C"}


def test_preflight_routes_partial_targets_and_missing_inputs_without_row_collapse() -> None:
    partial = preflight_source(FIXTURES / "partial-targets.csv", target_columns=("hardness_hv", "toughness_j"))
    same_count_different_rows = preflight_source(FIXTURES / "partial-targets-same-count.csv", target_columns=("hardness_hv", "toughness_j"))
    missing_input = preflight_source(FIXTURES / "category-missing-input.csv", target_columns=("strength_mpa",))

    assert partial.source_shape == "wide_multi_target"
    assert partial.target_availability == "partial_by_target"
    assert partial.state == "profile_needed"
    assert any("target別cohort" in reason for reason in partial.reasons)
    assert same_count_different_rows.target_availability == "partial_by_target"
    assert same_count_different_rows.state == "profile_needed"
    assert any("target別cohort" in reason for reason in same_count_different_rows.reasons)
    assert missing_input.state == "profile_needed"
    assert {column.name for column in missing_input.columns if column.suggested_role == "candidate_input"} == {"alloy", "temperature_c"}


def test_preflight_routes_variable_series_to_a_task_slice() -> None:
    preflight = preflight_source(FIXTURES / "variable-series-reference.csv", target_columns=("strength_mpa",))

    assert preflight.source_shape == "variable_length_series"
    assert preflight.route == "task_slice"
    assert preflight.standard_onboarding is False


def test_preflight_api_is_read_only_and_rejects_multiple_visible_tables(client: TestClient, tmp_path: Path) -> None:
    source = tmp_path / "relations.xlsx"
    workbook = Workbook()
    workbook.active.title = "samples"
    workbook.active.append(["sample_id", "strength"])
    workbook.active.append(["s-1", 300])
    workbook.create_sheet("measurements").append(["sample_id", "time", "value"])
    workbook.save(source)
    workbook.close()
    before = client.get("/api/data-library/datasets").json()

    with source.open("rb") as stream:
        response = client.post(
            "/api/developer/readiness/preflight",
            files={"file": (source.name, stream, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"target_columns_json": "[\"strength\"]"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_shape"] == "relational_workbook"
    assert body["route"] == "profile_workbench"
    assert client.get("/api/data-library/datasets").json() == before


def test_preflight_api_rejects_corrupt_xlsx_as_invalid_input(client: TestClient) -> None:
    response = client.post(
        "/api/developer/readiness/preflight",
        files={
            "file": (
                "corrupt.xlsx",
                b"this is not an Excel workbook",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
        data={"target_columns_json": "[]"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_readiness_inventory_is_current_and_describes_each_task() -> None:
    expected = build_readiness_inventory().model_dump(mode="json")
    spec = importlib.util.spec_from_file_location("readiness_inventory_under_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert module.DEFAULT_OUTPUT.is_file()
    assert json.loads(module.DEFAULT_OUTPUT.read_text(encoding="utf-8")) == expected
    assert module.main.__name__ == "main"
    assert {task["task_id"] for task in expected["tasks"]}
    assert all({"source_shape", "split_policy", "input_kinds", "target_kind", "standard_authoring"} <= set(task) for task in expected["tasks"])
    stage_c = next(task for task in expected["tasks"] if task["task_id"] == "welding-stage-c-properties-v1")
    assert stage_c["source_shape"] == "repeated_measurements"
    assert stage_c["profile_family"] == "observation-dataset-profile/v1"
    assert stage_c["split_policy"] == "grouped_weld_run"
    flank_wear = next(task for task in expected["tasks"] if task["task_id"] == "flank-wear-v1")
    assert flank_wear["profile_family"] == "dataset-input-profile/v2"
    stage_b = next(task for task in expected["tasks"] if task["task_id"] == "welding-consumable-stage-b-v1")
    assert stage_b["profile_family"] == "welding-stage-b-profile/v1"
