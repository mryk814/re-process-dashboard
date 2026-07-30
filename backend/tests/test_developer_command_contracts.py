from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re

import pytest

from material_workbench.developer_experience.change_guide import change_guide_entries


ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    categories = {
        "profile_workbench": "operations",
        "developer_doctor": "operations",
        "build_welding_consumable_sample_dataset": "generators",
        "prepare_secom_stress_dataset": "generators",
    }
    path = (
        ROOT
        / "backend"
        / "scripts"
        / categories[name]
        / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_change_guide_commands_have_an_executable_contract() -> None:
    package_scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    profile_workbench = _load_script("profile_workbench")
    developer_doctor = _load_script("developer_doctor")

    for entry in change_guide_entries():
        for command in entry.commands:
            assert command.executable
            assert command.display_text
            assert command.platform in {"cross-platform", "windows", "powershell"}

            if command.executable == "npm" and command.arguments[:1] == ["run"]:
                assert command.arguments[1] in package_scripts
                if command.arguments[1] == "dev:doctor" and "--" in command.arguments:
                    separator = command.arguments.index("--")
                    developer_doctor.build_parser().parse_args(command.arguments[separator + 1 :])

            if "backend/scripts/operations/profile_workbench.py" in command.arguments:
                script_index = command.arguments.index("backend/scripts/operations/profile_workbench.py")
                profile_workbench.build_parser().parse_args(command.arguments[script_index + 1 :])


def test_backend_script_inventory_covers_every_command() -> None:
    inventory = (ROOT / "backend/scripts/README.md").read_text(encoding="utf-8")
    scripts = {
        path.relative_to(ROOT / "backend/scripts").as_posix()
        for path in (ROOT / "backend/scripts").rglob("*.py")
    }
    documented = set(re.findall(r"`([a-z0-9_/]+\.py)`", inventory))
    assert scripts <= documented, (
        f"undocumented backend scripts: {sorted(scripts - documented)}"
    )


def test_repo_skills_reference_current_commands_and_paths() -> None:
    package_scripts = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["scripts"]
    prediction_skill = (
        ROOT / ".claude/skills/add-prediction-task/SKILL.md"
    ).read_text(encoding="utf-8")
    runtime_skill = (
        ROOT / ".claude/skills/add-model-runtime/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "verify:edit" in package_scripts
    assert "verify:focused" not in prediction_skill
    assert "npm.cmd run verify:edit" in prediction_skill
    assert "backend/src/material_workbench/modeling/model_packages.py" in runtime_skill
    assert (ROOT / "backend/src/material_workbench/modeling/model_packages.py").is_file()


def test_dataset_authoring_commands_refuse_source_root() -> None:
    welding_builder = _load_script("build_welding_consumable_sample_dataset")
    secom_builder = _load_script("prepare_secom_stress_dataset")
    protected_welding = ROOT / "data/source/should-not-be-written.xlsx"
    protected_secom = ROOT / "data/source/should-not-be-written.csv"
    protected_report = ROOT / "data/source/should-not-be-written.json"

    with pytest.raises(ValueError, match="data/source is read-only"):
        welding_builder.write_workbook({}, protected_welding)
    with pytest.raises(ValueError, match="data/source is read-only"):
        secom_builder.build(ROOT / "missing-raw-input", protected_secom, ROOT / "unused.json")
    with pytest.raises(ValueError, match="data/source is read-only"):
        secom_builder.build(
            ROOT / "missing-raw-input",
            ROOT / "artifacts/derived-data/unused.csv",
            protected_report,
        )


def test_profile_materialize_is_part_of_profile_workbench(tmp_path: Path) -> None:
    profile_workbench = _load_script("profile_workbench")
    base = tmp_path / "base.json"
    child = tmp_path / "child.json"
    output = tmp_path / "materialized.json"
    base.write_text(
        json.dumps({"id": "base", "shared": {"sheets": {"melt": "成分"}}}),
        encoding="utf-8",
    )
    child.write_text(
        json.dumps({"extends": "base.json", "id": "child"}),
        encoding="utf-8",
    )

    assert profile_workbench.main(["materialize", str(child), str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8")) == {
        "id": "child",
        "shared": {"sheets": {"melt": "成分"}},
    }
    assert profile_workbench.main(["materialize", str(child), str(output)]) == 1
    assert profile_workbench.main(
        ["materialize", str(child), str(output), "--replace"]
    ) == 0


def test_profile_validate_auto_detects_the_profile(capsys) -> None:
    profile_workbench = _load_script("profile_workbench")
    source = ROOT / "data/source/material_workbench_tutorial_v2.xlsx"

    assert profile_workbench.main(["validate", str(source)]) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True
    assert result["profile"]
    assert result["source_sha256"]
