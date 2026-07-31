from __future__ import annotations

from pathlib import Path
from shutil import copyfile

from openpyxl import load_workbook

from decision_workbench.developer_experience.diagnostics import (
    active_package_history_check,
    compare_task_sets,
    run_developer_doctor,
)
from decision_workbench.developer_experience.source_inspection import inspect_source_against_profiles
from decision_workbench.modeling.active_package_history import (
    ActivePackageChange,
    ActivePackageHistoryReport,
)


ROOT = Path(__file__).resolve().parents[2]


def test_compare_task_sets_reports_mismatch() -> None:
    check = compare_task_sets({"definition": {"a", "b"}, "module": {"a"}})
    assert check.severity == "error"
    assert check.details["definition"] == ["a", "b"]


def test_active_package_history_success_does_not_claim_every_switch_used_activate() -> None:
    check = active_package_history_check(ActivePackageHistoryReport(available=True))

    assert check.severity == "ok"
    assert check.summary == "rollback可能な直前Packageとpreviousの履歴が一致しています。"
    assert "すべて model:activate" not in check.summary


def test_active_package_history_explains_accepted_null_previous() -> None:
    accepted = ActivePackageChange(
        task_id="example-task",
        revision="abc123",
        replaced="packages/old",
        active="packages/current",
        recorded_previous=None,
        rollback_target="contract-mismatch",
        reason="入力契約が現在のTaskDefinitionと異なる版です。",
    )

    check = active_package_history_check(
        ActivePackageHistoryReport(available=True, accepted=(accepted,)),
    )

    assert check.severity == "ok"
    assert check.summary == (
        "rollback可能な直前Packageとpreviousは一致しています。"
        "rollback不能な旧版1件のprevious=nullは理由付きで受理しました。"
    )
    assert check.details["accepted"][0]["rollback_target"] == "contract-mismatch"


def test_doctor_report_has_stable_json_contract() -> None:
    report = run_developer_doctor(root=ROOT, include_generated_checks=False)
    payload = report.model_dump(mode="json")
    assert payload["schema_version"] == "developer-doctor/v1"
    assert payload["code"] in {0, 1}
    assert set(payload["task_ids"]) == {
        "annealed-properties-v1",
        "flank-wear-v1",
        "hot-rolled-properties-v1",
        "heat-treatment-tradeoff-v1",
        "concrete-strength-v1",
        "wear-curve-v1",
        "battery-degradation-v1",
        "secom-yield-risk-v1",
        "mpea-literature-tys-v1",
        "mpea-room-tensile-v1",
        "mpea-hardness-process-v1",
        "welding-stage-c-properties-v1",
        "welding-consumable-stage-b-v1",
    }
    assert any(check["id"] == "task-sets" for check in payload["checks"])


def test_missing_source_uses_invalid_input_exit_code(tmp_path: Path) -> None:
    report = run_developer_doctor(
        root=ROOT,
        source=tmp_path / "missing.xlsx",
        include_generated_checks=False,
    )
    assert report.code == 2
    assert report.status == "error"


def test_known_source_reports_profile_and_learning_counts() -> None:
    inspection = inspect_source_against_profiles(
        ROOT / "data" / "source" / "material_workbench_process_v1.xlsx",
    )
    assert inspection.selected_profile
    assert inspection.candidates[0].score == 100
    assert inspection.decisions["new_profile_required"].decision == "no"
    assert inspection.decisions["feature_pipeline_change_required"].decision == "no"
    assert inspection.decisions["model_package_rebuild_required"].decision == "review_required"
    assert {
        purpose.id: purpose.package_rebuild
        for purpose in inspection.data_purposes
    } == {
        "application": "no",
        "training": "yes",
        "candidate_input": "review_required",
    }
    assert inspection.learning_counts
    assert inspection.output_counts
    assert inspection.commands


def test_missing_mapped_column_requires_semantic_review(tmp_path: Path) -> None:
    source = tmp_path / "changed.xlsx"
    copyfile(
        ROOT / "data" / "source" / "material_workbench_process_v1.xlsx",
        source,
    )
    workbook = load_workbook(source)
    sheet = workbook["溶製成績書"]
    for cell in sheet[1]:
        if cell.value == "溶製成績書_key**":
            cell.value = "意味未確認のキー"
            break
    else:
        raise AssertionError("fixtureに必須キー列がありません")
    workbook.save(source)

    inspection = inspect_source_against_profiles(source)

    assert inspection.decisions["feature_pipeline_change_required"].decision == "review_required"
    assert inspection.decisions["new_task_may_be_required"].decision == "review_required"
    assert inspection.decisions["feature_pipeline_change_required"].evidence
