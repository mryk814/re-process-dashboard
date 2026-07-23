from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_windows_bundle_declares_active_model_configuration_and_packages() -> None:
    active_packages = json.loads((ROOT / "models" / "active-packages.json").read_text(encoding="utf-8"))
    available_packages = json.loads((ROOT / "models" / "available-packages.json").read_text(encoding="utf-8"))
    builder_config = (ROOT / "packaging" / "electron-builder.yml").read_text(encoding="utf-8")
    packaged_resources = set(re.findall(
        r"(?m)^  - from: ([^\r\n]+)\r?\n    to: ([^\r\n]+)$",
        builder_config,
    ))

    required_resources = {"models/active-packages.json", "models/available-packages.json"}
    required_resources.update(
        f"models/{selection['active']}"
        for selection in active_packages["tasks"].values()
    )
    required_resources.update(
        f"models/{package}"
        for package in available_packages["packages"]
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


def test_application_icon_is_configured_for_windows_and_web() -> None:
    builder_config = (ROOT / "packaging" / "electron-builder.yml").read_text(encoding="utf-8")
    web_document = (ROOT / "apps" / "web" / "index.html").read_text(encoding="utf-8")
    web_build = (ROOT / "apps" / "web" / "build.mjs").read_text(encoding="utf-8")
    windows_icon = (ROOT / "packaging" / "icons" / "icon.ico").read_bytes()
    web_icon = (ROOT / "apps" / "web" / "public" / "app-icon.png").read_bytes()

    assert "icon: packaging/icons/icon.ico" in builder_config
    assert "installerIcon: packaging/icons/icon.ico" in builder_config
    assert "uninstallerIcon: packaging/icons/icon.ico" in builder_config
    assert windows_icon[:4] == b"\x00\x00\x01\x00"
    assert int.from_bytes(windows_icon[4:6], "little") >= 7
    assert web_icon.startswith(b"\x89PNG\r\n\x1a\n")
    assert 'rel="icon" type="image/png" href="/app-icon.png"' in web_document
    assert 'rel="apple-touch-icon" href="/app-icon.png"' in web_document
    assert 'copyFile(resolve("public/app-icon.png"), resolve(outdir, "app-icon.png"))' in web_build
    assert 'rel="icon" type="image/png" href="./app-icon.png"' in web_build
    assert 'rel="apple-touch-icon" href="./app-icon.png"' in web_build
