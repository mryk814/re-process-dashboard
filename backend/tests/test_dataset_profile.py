from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from material_workbench.dataset_profile import (
    DatasetProfileError,
    canonicalize_workbook,
    load_dataset_profile,
    load_task_definitions,
    preflight_workbook,
)
from material_workbench.feature_pipeline import build_feature_bundle
from material_workbench.hot_rolling_feature_pipeline import build_hot_rolling_features
from material_workbench.importer import detect_dataset_profile_path, load_workbook_data
from material_workbench.app import create_app
from material_workbench.schemas import CandidateInput
from material_workbench.services import candidate_from_lineage
from material_workbench.task_contracts import TaskDefinition
from material_workbench.task_modules import registered_task_modules


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"
V3_SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v3.xlsx"
V5_SOURCE = ROOT / "data" / "source" / "process_dashboard_two_equipment_v5.xlsx"
V7_SOURCE = ROOT / "data" / "source" / "process_dashboard_two_equipment_v7.xlsx"
V8_SOURCE = ROOT / "data" / "source" / "process_dashboard_two_equipment_v8.xlsx"
PROFILE = ROOT / "backend" / "src" / "material_workbench" / "dataset-input-profile-v1.json"


def test_profile_is_driven_by_production_task_definitions() -> None:
    profile = load_dataset_profile()
    for task_id, task in profile.tasks.items():
        definition = profile.task_definitions[task_id]
        ordered_paths = tuple(
            field.path
            for group in sorted(definition.input_groups, key=lambda item: item.order)
            for field in sorted(group.fields, key=lambda item: item.order)
        )
        assert tuple(mapping.path for mapping in task.mappings) == ordered_paths


def test_preflight_aggregates_duplicate_and_missing_headers() -> None:
    workbook = load_workbook(SOURCE, read_only=False, data_only=True)
    melt = workbook["溶製"]
    melt.cell(1, 2).value = melt.cell(1, 1).value
    hot = workbook["熱延"]
    target = next(cell for cell in hot[1] if cell.value == "均熱温度[℃]")
    target.value = "renamed"

    with pytest.raises(DatasetProfileError) as caught:
        preflight_workbook(workbook, load_dataset_profile())

    assert any("duplicate header" in error for error in caught.value.errors)
    assert any("missing required column '均熱温度[℃]'" in error for error in caught.value.errors)


def test_reordered_columns_and_unmapped_metadata_do_not_change_canonical_values(tmp_path: Path) -> None:
    baseline = load_workbook_data(SOURCE)
    workbook = load_workbook(SOURCE, read_only=False, data_only=True)
    melt = workbook["溶製"]
    c_column = next(cell.column for cell in melt[1] if cell.value == "C[mass%]")
    si_column = next(cell.column for cell in melt[1] if cell.value == "Si[mass%]")
    for row in range(1, melt.max_row + 1):
        left, right = melt.cell(row, c_column).value, melt.cell(row, si_column).value
        melt.cell(row, c_column).value, melt.cell(row, si_column).value = right, left
    extra_column = melt.max_column + 1
    melt.cell(1, extra_column).value = "unused_metadata"
    for row in range(2, melt.max_row + 1):
        melt.cell(row, extra_column).value = float(10_000 + row)
    modified = tmp_path / "reordered.xlsx"
    workbook.save(modified)

    actual = load_workbook_data(modified)
    assert actual.composition == baseline.composition
    assert actual.hot_rolling_features == baseline.hot_rolling_features
    assert actual.anneal_features == baseline.anneal_features
    assert actual.observations == baseline.observations
    assert actual.medians == baseline.medians

    def representative_vector(data) -> np.ndarray:
        parent = "AN-00001"
        process = data.anneal_features[parent]
        observation = next(row for row in data.observations if row["parent_key"] == parent)
        candidate = CandidateInput(
            name="dataset-profile-golden",
            inputs={
                "composition": data.composition["ME-00001"],
                "process": {"ls_mpm": process["ls_mpm"]},
                "categorical": {},
                "heat_pattern": [
                    {"time_s": point["time_s"], "temperature_c": point["temperature_c"]}
                    for point in process["heat_pattern"]
                ],
            },
        )
        return build_feature_bundle(candidate).values

    expected = representative_vector(baseline)
    assert expected.shape == (30,)
    assert np.isfinite(expected).all()
    np.testing.assert_allclose(representative_vector(actual), expected, rtol=1e-12, atol=1e-12)

    def representative_hot_vector(data) -> np.ndarray:
        process = data.hot_rolling_features["HR-00001"]
        candidate = CandidateInput(
            name="hot-dataset-profile-golden",
            inputs={
                "composition": data.composition["ME-00001"],
                "process": {
                    key: process[key]
                    for key in (
                        "soaking_temperature_c", "finish_temperature_c", "entry_thickness_mm",
                        "exit_thickness_mm", "hold_temperature_c", "hold_time_min",
                    )
                },
                "categorical": {},
                "heat_pattern": None,
            },
        )
        return build_hot_rolling_features(candidate, data.medians).values

    hot_expected = representative_hot_vector(baseline)
    assert hot_expected.shape == (25,)
    assert np.isfinite(hot_expected).all()
    np.testing.assert_allclose(representative_hot_vector(actual), hot_expected, rtol=1e-12, atol=1e-12)


def test_unknown_unit_and_duplicate_mapping_are_rejected(tmp_path: Path) -> None:
    raw = json.loads(PROFILE.read_text(encoding="utf-8"))
    mappings = raw["tasks"]["hot-rolled-properties-v1"]["mappings"]
    mappings[14]["source_unit"] = "mystery-temperature"
    mappings.append(dict(mappings[0]))
    invalid = tmp_path / "invalid-profile.json"
    invalid.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DatasetProfileError) as caught:
        load_dataset_profile(invalid)

    assert any("unknown or implicit unit conversion" in error for error in caught.value.errors)
    assert any("multiple source mappings" in error for error in caught.value.errors)


@pytest.mark.parametrize(
    "mutation",
    [
        "nested_unknown_field",
        "duplicate_auxiliary_key",
        "unknown_range_key",
        "missing_relation_join",
        "missing_required_technical",
        "duplicate_entity_role",
        "empty_policy_values",
        "nested_task_id",
    ],
)
def test_profile_rejects_ambiguous_nested_measurement_contracts(tmp_path: Path, mutation: str) -> None:
    raw = json.loads(PROFILE.read_text(encoding="utf-8"))
    hot = raw["tasks"]["hot-rolled-properties-v1"]["observations"][0]
    if mutation == "nested_unknown_field":
        hot["auxiliary"][0]["guess_from_header"] = True
    elif mutation == "duplicate_auxiliary_key":
        hot["auxiliary"][1]["key"] = hot["auxiliary"][0]["key"]
    elif mutation == "unknown_range_key":
        raw["shared"]["physical_ranges"]["hot-rolled-properties-v1"]["invented"] = [0, 1]
    elif mutation == "missing_relation_join":
        raw["shared"]["relation"]["joins"].pop(0)
    elif mutation == "missing_required_technical":
        raw["shared"]["technical"] = [
            item for item in raw["shared"]["technical"]
            if not (item["role"] == "hot_rolling" and item["name"] == "equipment")
        ]
    elif mutation == "duplicate_entity_role":
        raw["shared"]["entities"][1]["role"] = raw["shared"]["entities"][0]["role"]
    elif mutation == "empty_policy_values":
        raw["shared"]["eligibility"][0]["accepted_values"] = []
    else:
        raw["tasks"]["annealed-properties-v1"]["task_id"] = "hot-rolled-properties-v1"
    invalid = tmp_path / f"{mutation}.json"
    invalid.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DatasetProfileError):
        load_dataset_profile(invalid)


@pytest.mark.parametrize("mutation", ["missing_task", "missing_output"])
def test_profile_requires_every_task_and_output_exactly_once(tmp_path: Path, mutation: str) -> None:
    raw = json.loads(PROFILE.read_text(encoding="utf-8"))
    if mutation == "missing_task":
        del raw["tasks"]["hot-rolled-properties-v1"]
    else:
        raw["tasks"]["annealed-properties-v1"]["observations"][1]["targets"] = []
    invalid = tmp_path / f"{mutation}.json"
    invalid.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DatasetProfileError):
        load_dataset_profile(invalid)


def test_preflight_rejects_empty_required_heat_series_and_policy_without_signal(tmp_path: Path) -> None:
    workbook = load_workbook(SOURCE, read_only=False, data_only=True)
    profile = load_dataset_profile()
    heat_mapping = next(
        mapping for task in profile.tasks.values() for mapping in task.mappings
        if mapping.kind == "ordered_heat_series"
    )
    assert heat_mapping.series_columns is not None
    heat_sheet = workbook[profile.sheet_for_role(heat_mapping.role)]
    headers = [cell.value for cell in heat_sheet[1]]
    time_column = headers.index(heat_mapping.series_columns.time) + 1
    value_column = headers.index(heat_mapping.series_columns.value) + 1
    for row in range(2, heat_sheet.max_row + 1):
        heat_sheet.cell(row, time_column).value = None
        heat_sheet.cell(row, value_column).value = None

    raw = json.loads(PROFILE.read_text(encoding="utf-8"))
    raw["shared"]["eligibility"][0]["accepted_values"] = ["TYPO"]
    invalid_profile = tmp_path / "policy-without-signal.json"
    invalid_profile.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(DatasetProfileError) as caught:
        preflight_workbook(workbook, load_dataset_profile(invalid_profile))

    assert any("at least two numeric points" in error for error in caught.value.errors)
    assert any("has no accepted source values" in error for error in caught.value.errors)


def test_preflight_reports_non_numeric_heat_series_order() -> None:
    workbook = load_workbook(SOURCE, read_only=False, data_only=True)
    profile = load_dataset_profile()
    heat_mapping = next(
        mapping for task in profile.tasks.values() for mapping in task.mappings
        if mapping.kind == "ordered_heat_series"
    )
    assert heat_mapping.series_columns is not None
    sheet = workbook[profile.sheet_for_role(heat_mapping.role)]
    order_column = next(
        cell.column for cell in sheet[1] if cell.value == heat_mapping.series_columns.order
    )
    sheet.cell(2, order_column).value = "bad-order"

    with pytest.raises(DatasetProfileError) as caught:
        preflight_workbook(workbook, profile)

    assert any("non-numeric order" in error for error in caught.value.errors)


def test_preflight_executes_declared_parent_consistency(tmp_path: Path) -> None:
    workbook = load_workbook(SOURCE, read_only=False, data_only=True)
    raw = json.loads(PROFILE.read_text(encoding="utf-8"))
    hot_join_document = next(
        join for join in raw["shared"]["relation"]["joins"] if join["entity_type"] == "hot_rolling"
    )
    hot_join_document["parent_consistency"] = "exactly_one"
    strict_profile_path = tmp_path / "strict-parent-profile.json"
    strict_profile_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    profile = load_dataset_profile(strict_profile_path)
    relation = workbook[profile.sheet_for_role(profile.shared.relation.role)]
    headers = [cell.value for cell in relation[1]]
    hot_join = next(join for join in profile.shared.relation.joins if join.entity_type == "hot_rolling")
    melt_join = next(join for join in profile.shared.relation.joins if join.entity_type == "melt")
    hot_column = headers.index(hot_join.column) + 1
    melt_column = headers.index(melt_join.column) + 1
    first_row_by_hot: dict[str, int] = {}
    changed = False
    for row in range(2, relation.max_row + 1):
        hot_key = relation.cell(row, hot_column).value
        if hot_key is None:
            continue
        first_row = first_row_by_hot.setdefault(str(hot_key), row)
        if first_row != row:
            original_melt = relation.cell(first_row, melt_column).value
            relation.cell(row, melt_column).value = f"{original_melt}-CONFLICT"
            changed = True
            break
    assert changed

    with pytest.raises(DatasetProfileError) as caught:
        preflight_workbook(workbook, profile)

    assert any("conflicting parents" in error for error in caught.value.errors)


def test_canonical_entities_separate_values_from_source_metadata() -> None:
    workbook = load_workbook(SOURCE, read_only=True, data_only=True)
    dataset = canonicalize_workbook(workbook, load_dataset_profile())
    entity = dataset.entities[("melt", "ME-00001")]
    anneal_values = entity.values["annealed-properties-v1"]

    assert set(anneal_values) == {f"composition.{name}" for name in ("C", "Si", "Mn", "P", "S", "Al", "Cu", "Ni", "Cr", "Mo", "Ti", "B", "O", "N")}
    assert "プロジェクト名" in entity.source_metadata
    assert entity.source_locator == {"sheet": "溶製", "row": 2}
    anneal = dataset.entities[("annealing", "AN-00001")]
    anneal_feature_row = next(
        row for row in dataset.rows("anneal_features")
        if dataset.technical_value(row, "anneal_features", "parent_key") == "AN-00001"
    )
    assert dataset.value(anneal_feature_row, "annealed-properties-v1", "process.ls_mpm") > 0
    assert len(anneal.values["annealed-properties-v1"]["heat_pattern"]) == 14


def test_canonical_entity_identity_does_not_merge_equal_keys_across_types() -> None:
    workbook = load_workbook(SOURCE, read_only=False, data_only=True)
    workbook["溶製"].cell(2, 1).value = "SAME-KEY"
    hot_key_column = next(cell.column for cell in workbook["熱延"][1] if cell.value == "熱延_key")
    workbook["熱延"].cell(2, hot_key_column).value = "SAME-KEY"
    relation = workbook["relation"]
    melt_relation_column = next(cell.column for cell in relation[1] if cell.value == "溶製_key")
    hot_relation_column = next(cell.column for cell in relation[1] if cell.value == "熱延_key")
    relation.cell(2, melt_relation_column).value = "SAME-KEY"
    relation.cell(2, hot_relation_column).value = "SAME-KEY"

    dataset = canonicalize_workbook(workbook, load_dataset_profile())

    assert dataset.entities[("melt", "SAME-KEY")].identity == ("melt", "SAME-KEY")
    assert dataset.entities[("hot_rolling", "SAME-KEY")].identity == ("hot_rolling", "SAME-KEY")
    assert dataset.relations[0]["melt"] == ("melt", "SAME-KEY")
    assert dataset.relations[0]["hot_rolling"] == ("hot_rolling", "SAME-KEY")


def test_lineage_candidate_uses_profile_key_mapping_not_external_header_names(tmp_path: Path) -> None:
    raw_profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    melt_entity = next(item for item in raw_profile["shared"]["entities"] if item["role"] == "melt")
    melt_join = next(item for item in raw_profile["shared"]["relation"]["joins"] if item["entity_type"] == "melt")
    old_key = melt_entity["key"]
    melt_entity["key"] = "melt_id"
    melt_join["column"] = "melt_id"
    profile_path = tmp_path / "renamed-key-profile.json"
    profile_path.write_text(json.dumps(raw_profile, ensure_ascii=False), encoding="utf-8")

    workbook = load_workbook(SOURCE, read_only=False, data_only=True)
    for sheet_name in (raw_profile["shared"]["sheets"]["melt"], raw_profile["shared"]["sheets"]["relation"]):
        header = next(cell for cell in workbook[sheet_name][1] if cell.value == old_key)
        header.value = "melt_id"
    source = tmp_path / "renamed-key.xlsx"
    workbook.save(source)
    workbook.close()

    data = load_workbook_data(source, profile_path)
    candidate = candidate_from_lineage(data, "AN-00001")

    assert len(data.composition) == 120
    assert candidate.inputs.composition == data.composition["ME-00001"]


def test_importer_accepts_task_and_profile_composition_addition_without_code_change(tmp_path: Path) -> None:
    definitions = load_task_definitions()
    task_id = "annealed-properties-v1"
    task_document = definitions[task_id].model_dump(mode="json")
    composition_group = next(group for group in task_document["input_groups"] if group["key"] == "composition")
    vanadium = dict(composition_group["fields"][-1])
    vanadium.update({"path": "composition.V", "order": len(composition_group["fields"]), "label": "V"})
    composition_group["fields"].append(vanadium)
    task_document["display_decimals"]["composition.V"] = 5
    definitions[task_id] = TaskDefinition.model_validate(task_document)

    raw_profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    mappings = raw_profile["tasks"][task_id]["mappings"]
    insertion_index = next(
        index for index, mapping in enumerate(mappings) if not mapping["path"].startswith("composition.")
    )
    mappings.insert(insertion_index, {
        "path": "composition.V",
        "role": "melt",
        "column": "V[mass%]",
        "kind": "entity_scalar",
        "source_unit": "mass%",
        "canonical_unit": "mass%",
    })
    profile_path = tmp_path / "with-vanadium-profile.json"
    profile_path.write_text(json.dumps(raw_profile, ensure_ascii=False), encoding="utf-8")

    workbook = load_workbook(SOURCE, read_only=False, data_only=True)
    melt = workbook[raw_profile["shared"]["sheets"]["melt"]]
    column = melt.max_column + 1
    melt.cell(1, column).value = "V[mass%]"
    for row in range(2, melt.max_row + 1):
        melt.cell(row, column).value = 0.0123
    source = tmp_path / "with-vanadium.xlsx"
    workbook.save(source)
    workbook.close()

    data = load_workbook_data(source, profile_path, definitions)

    assert data.composition["ME-00001"]["V"] == pytest.approx(0.0123)


def test_current_workbook_entity_and_eligibility_golden() -> None:
    data = load_workbook_data(SOURCE)
    assert {key: len(rows) for key, rows in data.entities.items()} == {
        "溶製_key": 120, "熱延_key": 225, "冷延_key": 178, "焼鈍_key": 196,
        "熱延引張_key": 388, "熱延組織_key": 259, "焼鈍引張_key": 292,
        "焼鈍穴広げ_key": 532, "焼鈍組織_key": 219,
    }
    eligible = {}
    for observation in data.observations:
        key = (observation["source"], observation["eligible"])
        eligible[key] = eligible.get(key, 0) + 1
    assert eligible == {
        ("熱延引張", True): 370, ("熱延引張", False): 18,
        ("焼鈍引張", True): 283, ("焼鈍引張", False): 9,
        ("焼鈍穴広げ", True): 512, ("焼鈍穴広げ", False): 20,
    }
    assert data.composition["ME-00001"] == {
        "C": 0.04232, "Si": 0.1678, "Mn": 0.80214, "P": 0.00973, "S": 0.0059,
        "Al": 0.03804, "Cu": 0.0, "Ni": 0.07294, "Cr": 0.01699, "Mo": 0.0,
        "Ti": 0.00592, "B": 0.0003, "O": 0.00256, "N": 0.00443,
    }
    workbook = load_workbook(SOURCE, read_only=True, data_only=True)
    canonical = canonicalize_workbook(workbook, load_dataset_profile())
    workbook.close()
    assert len(canonical.relations) == 1905
    assert canonical.relations[0] == {
        "melt": ("melt", "ME-00001"),
        "hot_rolling": ("hot_rolling", "HR-00001"),
        "hot_tensile": ("hot_tensile", "HT-00001"),
    }
    assert canonical.entities[("hot_rolling", "HR-00001")].values["hot-rolled-properties-v1"] == {
        "process.soaking_temperature_c": 1168.038,
        "process.finish_temperature_c": 904.873,
        "process.entry_thickness_mm": 33.318,
        "process.exit_thickness_mm": 3.459,
        "process.hold_temperature_c": 1168.038,
        "process.hold_time_min": 29.341,
    }


def test_v3_source_auto_selects_its_profile_and_preserves_canonical_flow() -> None:
    assert detect_dataset_profile_path(V3_SOURCE).name == "dataset-input-profile-v3.json"
    data = load_workbook_data(V3_SOURCE)

    assert data.profile_id == "process-dashboard-realistic-v3"
    assert data.relation_sheet == "relationEx"
    assert len(data.sheets[data.relation_sheet]) == 1905
    assert len(data.composition) == 120
    assert len(data.anneal_features) == 196
    assert len(data.observations) == 1212
    assert data.quality == []
    assert data.anneal_features["AN-00001"]["heat_pattern"][0] == {
        "time_s": 2.533,
        "temperature_c": 35.0,
        "stage_name": "入口",
    }
    assert data.observations[0]["source"] in {"熱延引張実績", "焼鈍引張実績", "焼鈍穴拡げ実績"}


def test_v5_source_auto_selects_additional_profile_and_maps_concrete_stages() -> None:
    assert detect_dataset_profile_path(V5_SOURCE).name == "dataset-input-profile-v5.json"
    data = load_workbook_data(V5_SOURCE)

    assert data.profile_id == "process-dashboard-two-equipment-v5"
    assert data.relation_sheet == "relationEx"
    assert len(data.composition) == 120
    assert len(data.anneal_features) == 196
    assert data.anneal_features["AN-00001"]["heat_pattern"][0] == {
        "time_s": 2.533,
        "temperature_c": 35.0,
        "stage_name": "入口",
        "stage_category": "ENTRY",
        "mapping_status": "工程辞書一致",
    }
    reheat = next(
        point for point in data.anneal_features["AN-00001"]["heat_pattern"]
        if point["stage_name"] == "加熱3"
    )
    assert reheat["stage_category"] == "REHEAT"
    assert reheat["mapping_status"] == "工程辞書一致"


def test_v7_source_resolves_relation_parents_coalesces_measurements_and_derives_history() -> None:
    assert detect_dataset_profile_path(V7_SOURCE).name == "dataset-input-profile-v7.json"
    data = load_workbook_data(V7_SOURCE)

    assert data.profile_id == "process-dashboard-two-equipment-v7"
    assert len(data.composition) == 120
    assert len(data.anneal_features) == 196
    assert len(data.sheets["焼鈍履歴"]) == 3507
    assert len(data.detected_quality) == 44

    first = data.anneal_features["AN-00001"]
    assert first["ls_mpm"] == pytest.approx(119.742)
    assert first["input_points"] == 18
    assert first["feature_eligible"] is True
    assert first["unmapped_stage_count"] == 0
    assert first["heat_pattern"][0] == {
        "time_s": 0.0,
        "temperature_c": 34.3,
        "stage_name": "開始",
        "stage_category": "ENTRY",
        "mapping_status": "工程辞書一致",
    }
    assert {point["stage_name"] for feature in data.anneal_features.values() for point in feature["heat_pattern"]} == {
        item.raw_name for item in load_dataset_profile(data.profile_path).stage_mappings
    }

    annealed = [row for row in data.observations if row["task_id"] == "annealed-properties-v1"]
    tensile = [row for row in annealed if row["source"] == "焼鈍引張実績"]
    assert len(tensile) == 292
    assert all(row["parent_key"].startswith("AN-") for row in tensile)
    assert sum("YS[MPa]" in row["outputs"] for row in tensile) == 292
    assert sum("TS[MPa]" in row["outputs"] for row in tensile) == 292
    assert sum("EL[%]" in row["outputs"] for row in tensile) == 292

    hot = [row for row in data.observations if row["task_id"] == "hot-rolled-properties-v1"]
    assert sum("TS[MPa]" in row["outputs"] for row in hot) == 348
    assert any("TS[MPa]" in row["outputs"] and "一様伸び[%]" not in row["outputs"] for row in hot)
    assert {row["test_direction"] for row in hot} == {"L", "C"}
    assert {row["test_direction"] for row in hot if row["eligible"]} == {"L", "C"}
    unresolved = next(row for row in hot if row["id"] == "HT-00388")
    assert unresolved["eligible"] is False
    assert unresolved["parent_key"] == ""


def test_v8_source_maps_renamed_prediction_fields_and_tolerates_optional_context() -> None:
    assert detect_dataset_profile_path(V8_SOURCE).name == "dataset-input-profile-v8.json"
    data = load_workbook_data(V8_SOURCE)

    assert data.profile_id == "process-dashboard-two-equipment-v8"
    assert len(data.composition) == 120
    assert len(data.anneal_features) == 196
    assert data.hot_rolling_features["HR-00001"]["equipment"] == ""
    assert data.anneal_features["AN-00001"]["heat_pattern"][0]["time_s"] == 0.0

    annealed = [row for row in data.observations if row["task_id"] == "annealed-properties-v1"]
    tensile = [row for row in annealed if row["source"] == "焼鈍引張実績"]
    holes = [row for row in annealed if row["source"] == "焼鈍穴拡げ実績"]
    assert len(tensile) == 292
    assert len(holes) == 518
    assert all({"TS[MPa]", "YS[MPa]", "EL[%]"} <= set(row["outputs"]) for row in tensile)
    assert all(row["date"] is None for row in holes)

    hot = [row for row in data.observations if row["task_id"] == "hot-rolled-properties-v1"]
    assert sum("TS[MPa]" in row["outputs"] for row in hot) == 348
    assert {row["test_direction"] for row in hot} == {None}


def test_v7_derives_heat_pattern_from_measurement_master_when_history_is_absent(
    tmp_path: Path,
) -> None:
    workbook = load_workbook(V7_SOURCE, read_only=False, data_only=True)
    workbook.remove(workbook["焼鈍履歴"])
    profile = load_dataset_profile(
        ROOT / "backend" / "src" / "material_workbench" / "dataset-input-profile-v7.json"
    )

    canonical = canonicalize_workbook(workbook, profile)
    points = canonical.entities[("annealing", "AN-00001")].values[
        "annealed-properties-v1"
    ]["heat_pattern"]

    assert points[0] == {
        "time_s": 0.0,
        "temperature_c": 34.3,
        "stage_name": "開始",
        "stage_category": "ENTRY",
        "mapping_status": "測定点マスタ補完",
    }
    phf = next(point for point in points if point["stage_name"] == "PHF")
    assert phf["time_s"] == pytest.approx(60 * 15 / 119.742)
    assert phf["temperature_c"] == pytest.approx(164.1)
    assert all(
        right["time_s"] > left["time_s"]
        for left, right in zip(points, points[1:])
    )
    source = tmp_path / "v7-without-heat-history.xlsx"
    workbook.save(source)
    workbook.close()
    data = load_workbook_data(source, profile=profile)
    candidate = candidate_from_lineage(data, "AN-00001")

    assert candidate.inputs.heat_time_basis == "line_speed"
    assert candidate.inputs.heat_pattern is not None
    assert candidate.inputs.heat_pattern[1].mapping_status == "測定点マスタ補完"


def test_v7_explicit_heat_history_takes_priority_over_measurement_master() -> None:
    workbook = load_workbook(V7_SOURCE, read_only=False, data_only=True)
    master = workbook["測定点マスタ"]
    position_column = next(
        cell.column for cell in master[1] if cell.value == "入口からの距離[m]"
    )
    phf_row = next(
        cell.row for cell in master["E"] if cell.value == "PHF"
    )
    master.cell(phf_row, position_column).value = 16.0
    profile = load_dataset_profile(
        ROOT / "backend" / "src" / "material_workbench" / "dataset-input-profile-v7.json"
    )

    canonical = canonicalize_workbook(workbook, profile)
    points = canonical.entities[("annealing", "AN-00001")].values[
        "annealed-properties-v1"
    ]["heat_pattern"]
    phf = next(point for point in points if point["stage_name"] == "PHF")

    assert phf["time_s"] == pytest.approx(60 * 15 / 119.742)
    assert phf["mapping_status"] == "工程辞書一致"


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    [
        ("decreasing_position", "positions must increase"),
        ("partial_history", "incomplete or non-numeric points"),
        ("missing_temperature_column", "missing temperature columns"),
        ("missing_history_time_header", "existing ordered heat series sheet"),
        ("missing_history_stage_header", "existing ordered heat series sheet"),
        ("missing_entry_temperature", "neither a complete heat history nor a derivable"),
    ],
)
def test_v7_rejects_heat_series_inputs_that_would_silently_change_the_pattern(
    mutation: str,
    expected_error: str,
) -> None:
    workbook = load_workbook(V7_SOURCE, read_only=False, data_only=True)
    if mutation in {
        "partial_history",
        "missing_history_time_header",
        "missing_history_stage_header",
    }:
        history = workbook["焼鈍履歴"]
        if mutation == "partial_history":
            time_column = next(
                cell.column for cell in history[1] if cell.value == "到達時間[秒]"
            )
            history.cell(2, time_column).value = None
        else:
            target = (
                "到達時間[秒]"
                if mutation == "missing_history_time_header"
                else "工程"
            )
            column = next(cell.column for cell in history[1] if cell.value == target)
            history.cell(1, column).value = f"broken-{target}"
    else:
        workbook.remove(workbook["焼鈍履歴"])
        if mutation == "decreasing_position":
            master = workbook["測定点マスタ"]
            position_column = next(
                cell.column for cell in master[1] if cell.value == "入口からの距離[m]"
            )
            phf_row = next(cell.row for cell in master["E"] if cell.value == "PHF")
            master.cell(phf_row, position_column).value = 999.0
        elif mutation == "missing_temperature_column":
            annealing = workbook["焼鈍条件-3CGL"]
            phf_column = next(cell.column for cell in annealing[1] if cell.value == "PHF[℃]")
            annealing.cell(1, phf_column).value = "PHF temperature"
        else:
            annealing = workbook["焼鈍条件-3CGL"]
            key_column = next(
                cell.column for cell in annealing[1]
                if cell.value == "焼鈍条件-3CGL_key**"
            )
            entry_column = next(cell.column for cell in annealing[1] if cell.value == "開始[℃]")
            row = next(
                cells[0].row
                for cells in annealing.iter_rows(min_col=key_column, max_col=key_column)
                if cells[0].value == "AN-00001"
            )
            annealing.cell(row, entry_column).value = None
    profile = load_dataset_profile(
        ROOT / "backend" / "src" / "material_workbench" / "dataset-input-profile-v7.json"
    )

    with pytest.raises(DatasetProfileError) as caught:
        canonicalize_workbook(workbook, profile)

    assert any(expected_error in error for error in caught.value.errors)


def test_invalid_workbook_stops_before_runtime_and_database_initialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workbook = Workbook()
    source = tmp_path / "invalid.xlsx"
    workbook.save(source)
    database = tmp_path / "must-not-exist.db"
    runtime_called = False

    def forbidden_runtime(*args, **kwargs):
        nonlocal runtime_called
        runtime_called = True
        raise AssertionError("runtime must not initialize before dataset preflight")

    guarded_modules = {
        task_id: replace(module, runtime_factory=forbidden_runtime)
        for task_id, module in registered_task_modules().items()
    }
    monkeypatch.setattr("material_workbench.app.registered_task_modules", lambda: guarded_modules)
    app = create_app(source, database)
    with pytest.raises(DatasetProfileError):
        with TestClient(app):
            pass

    assert runtime_called is False
    assert not database.exists()
