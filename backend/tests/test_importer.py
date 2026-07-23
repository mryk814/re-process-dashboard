from pathlib import Path

from material_workbench.data.importer import _attach_quality_navigation, _detect_data_quality, load_workbook_data


SOURCE = Path(__file__).resolve().parents[2] / "data" / "source" / "material_workbench_tutorial_v1.xlsx"


def test_importer_preserves_relation_as_lineage_and_direct_observations() -> None:
    data = load_workbook_data(SOURCE)
    assert data.sheets["relation"]
    assert len(data.sheets["relation"]) == 26
    assert len(data.observations) == 26
    anneal = [row for row in data.observations if row["source"] == "焼鈍引張"]
    assert {row["parent_key"] for row in anneal} <= set(data.anneal_features)
    assert "溶製_key" in data.lineage["AN-01"]
    assert data.quality == []


def test_hot_rolling_training_preserves_partial_eligible_observations() -> None:
    data = load_workbook_data(SOURCE)
    hot = {row["id"]: row for row in data.observations if row["source"] == "熱延引張"}
    assert hot["HT-03"]["eligible"] is True
    assert hot["HT-03"]["outputs"] == {"TS[MPa]": 472.0, "YS[MPa]": 316.0}
    assert hot["HT-07"]["eligible"] is True
    assert "YS[MPa]" not in hot["HT-07"]["outputs"]
    assert all(row["test_direction"] == "L" for row in hot.values())
    assert all(row["eligibility_reasons"] == [] for row in hot.values())


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


def test_quality_findings_describe_lineage_investigation_destinations() -> None:
    issues = [
        {"issue_id": "duplicate", "issue_type": "duplicate_key", "source_sheet": "溶製", "entity_key": "ME-1", "detail": "duplicate"},
        {"issue_id": "orphan", "issue_type": "orphan_entity", "source_sheet": "冷延", "entity_key": "CR-1", "detail": "orphan"},
        {"issue_id": "invalid", "issue_type": "invalid_reference", "source_sheet": "relation", "entity_key": "HT-MISSING", "detail": "invalid"},
        {"issue_id": "missing", "issue_type": "missing_key", "source_sheet": "焼鈍", "entity_key": "", "detail": "missing"},
    ]
    lineage = {
        "ME-1": {"溶製_key": ["ME-1"], "熱延_key": ["HR-1"]},
        "CR-1": {"冷延_key": ["CR-1"]},
        "HT-MISSING": {"熱延引張_key": ["HT-MISSING"], "熱延_key": ["HR-1"]},
    }

    enriched = {item["issue_id"]: item for item in _attach_quality_navigation(issues, lineage)}

    assert enriched["duplicate"]["focus_entity_key"] == "ME-1"
    assert enriched["duplicate"]["related_entity_keys"] == ["HR-1"]
    assert enriched["orphan"]["focus_entity_key"] == "CR-1"
    assert enriched["invalid"]["missing_reference_key"] == "HT-MISSING"
    assert enriched["invalid"]["suggested_view"] == "lineage"
    assert enriched["missing"]["focus_entity_key"] is None
    assert enriched["missing"]["suggested_view"] == "source_sheet"
