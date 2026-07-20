from pathlib import Path

from material_workbench.importer import _detect_data_quality, load_workbook_data


SOURCE = Path(__file__).resolve().parents[2] / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"


def test_importer_preserves_relation_as_lineage_and_direct_observations() -> None:
    data = load_workbook_data(SOURCE)
    assert data.sheets["relation"]
    assert len(data.sheets["relation"]) == 1905
    assert len(data.observations) == 1212
    anneal = [row for row in data.observations if row["source"] == "焼鈍引張"]
    assert {row["parent_key"] for row in anneal} <= set(data.anneal_features)
    assert "溶製_key" in data.lineage["AN-00001"]
    assert any(issue["scenario_id"] == "dangling.hot_tensile.001" for issue in data.quality)


def test_hot_rolling_training_excludes_non_l_direction_and_physical_outliers() -> None:
    data = load_workbook_data(SOURCE)
    hot = {row["id"]: row for row in data.observations if row["source"] == "熱延引張"}
    assert hot["HT-00024"]["eligible"] is False
    assert "TS[MPa]が物理範囲外です" in hot["HT-00024"]["eligibility_reasons"]
    assert "EL[%]が物理範囲外です" in hot["HT-00024"]["eligibility_reasons"]
    assert hot["HT-00009"]["eligible"] is False
    assert "v1の推定対象はL方向です" in hot["HT-00009"]["eligibility_reasons"]
    assert hot["HT-00001"]["eligible"] is True
    assert hot["HT-00001"]["eligibility_reasons"] == []


def test_structural_quality_detector_covers_all_required_issue_types() -> None:
    sheets = {
        "relation": [{"溶製_key": "ME-001", "熱延_key": "HR-MISSING"}],
        "溶製": [{"溶製_key": "ME-001"}, {"溶製_key": "ME-001"}, {"溶製_key": None}, {"溶製_key": "ME-ORPHAN"}],
        "熱延": [], "冷延": [], "焼鈍": [], "熱延引張": [], "熱延組織": [],
        "焼鈍引張": [], "焼鈍穴広げ": [], "焼鈍組織": [],
    }
    entities = {"溶製_key": {"ME-001": sheets["溶製"][0], "ME-ORPHAN": sheets["溶製"][3]}, "熱延_key": {}}
    issues = _detect_data_quality(sheets, entities)
    assert {issue["issue_type"] for issue in issues} == {"missing_key", "duplicate_key", "invalid_reference", "orphan_entity"}
