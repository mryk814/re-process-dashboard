from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from material_workbench.developer_experience.change_guide import change_guide_entries


ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = ROOT / "backend" / "scripts" / f"{name}.py"
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

            if "backend/scripts/profile_workbench.py" in command.arguments:
                script_index = command.arguments.index("backend/scripts/profile_workbench.py")
                profile_workbench.build_parser().parse_args(command.arguments[script_index + 1 :])
