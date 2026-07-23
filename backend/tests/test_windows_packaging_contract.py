from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_windows_bundle_declares_active_model_configuration_and_packages() -> None:
    active_packages = json.loads((ROOT / "models" / "active-packages.json").read_text(encoding="utf-8"))
    builder_config = (ROOT / "packaging" / "electron-builder.yml").read_text(encoding="utf-8")
    packaged_resources = set(re.findall(
        r"(?m)^  - from: ([^\r\n]+)\r?\n    to: ([^\r\n]+)$",
        builder_config,
    ))

    required_resources = {"models/active-packages.json"}
    required_resources.update(
        f"models/{selection['active']}"
        for selection in active_packages["tasks"].values()
    )

    for resource in required_resources:
        assert (resource, resource) in packaged_resources


def test_packaged_launcher_uses_active_model_configuration_as_single_source() -> None:
    desktop_launcher = (ROOT / "apps" / "desktop" / "src" / "main.ts").read_text(encoding="utf-8")

    for override in (
        "MATERIAL_WORKBENCH_MODEL_PACKAGE:",
        "MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE:",
        "MATERIAL_WORKBENCH_FLANK_WEAR_MODEL_PACKAGE:",
    ):
        assert override not in desktop_launcher
