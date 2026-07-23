from __future__ import annotations

from pathlib import Path

from material_workbench.developer_experience.diagnostics import compare_task_sets, run_developer_doctor
from material_workbench.developer_experience.source_inspection import inspect_source_against_profiles


ROOT = Path(__file__).resolve().parents[2]


def test_compare_task_sets_reports_mismatch() -> None:
    check = compare_task_sets({"definition": {"a", "b"}, "module": {"a"}})
    assert check.severity == "error"
    assert check.details["definition"] == ["a", "b"]


def test_doctor_report_has_stable_json_contract() -> None:
    report = run_developer_doctor(root=ROOT, include_generated_checks=False)
    payload = report.model_dump(mode="json")
    assert payload["schema_version"] == "developer-doctor/v1"
    assert payload["code"] in {0, 1}
    assert set(payload["task_ids"]) == {
        "annealed-properties-v1",
        "flank-wear-v1",
        "hot-rolled-properties-v1",
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
    assert inspection.decisions["new_profile_required"] is False
    assert inspection.learning_counts
    assert inspection.output_counts
    assert inspection.commands
