from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_developer_start_here_links_to_contracts_and_recipes() -> None:
    source = (ROOT / "docs" / "developer-start-here.md").read_text(encoding="utf-8")
    for relative in (
        "docs/dataset-input-profile.md",
        "docs/feature-engineering.md",
        "docs/model-package-contract.md",
        "docs/recipes/add-similar-workbook.md",
        "docs/recipes/add-more-rows.md",
        "docs/recipes/add-input-field.md",
    ):
        assert (ROOT / relative).exists()
        assert Path(relative).name in source
    assert "npm run dev:doctor" in source
    assert "npm run api:generate" in source


def test_prediction_task_skill_uses_task_module_as_the_single_registration_point() -> None:
    source = (ROOT / ".claude" / "skills" / "add-prediction-task" / "SKILL.md").read_text(encoding="utf-8")
    assert "`TaskModule` entry" in source
    assert "`app.py`へTask固有Runtimeを直接追加しない" in source
    assert "workflow、verifier、lifecycleへ同じtask dispatchを個別追加しない" in source
    assert "app.pyへ直接Runtimeを追加する" not in source
