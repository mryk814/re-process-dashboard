from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from material_workbench.task_composition.catalog import registered_task_modules
from sidecar import configure_standard_streams

ROOT = Path(__file__).parents[2]


def test_user_facing_product_name_changes_without_moving_stable_identity() -> None:
    builder_config = (
        ROOT / "packaging" / "electron-builder.yml"
    ).read_text(encoding="utf-8")
    desktop_launcher = (
        ROOT / "apps" / "desktop" / "src" / "main.ts"
    ).read_text(encoding="utf-8")
    packaged_smoke = (
        ROOT / "scripts" / "smoke-packaged.mjs"
    ).read_text(encoding="utf-8")
    portable_readme = (
        ROOT / "packaging" / "PORTABLE-README.txt"
    ).read_text(encoding="utf-8")

    assert "appId: jp.local.material-decision-workbench" in builder_config
    assert "productName: Evidence Decision Workbench" in builder_config
    assert "executableName: Evidence Decision Workbench" in builder_config
    assert (
        "artifactName: Evidence-Decision-Workbench-Setup-${version}.${ext}"
        in builder_config
    )
    assert '"Evidence Decision Workbench.exe"' in packaged_smoke
    assert "Evidence Decision Workbench - フォルダ版" in portable_readme
    assert (
        'join(dirname(app.getPath("appData")), "Local"), '
        '"Material Decision Workbench")'
        in desktop_launcher
    )
    assert 'extensions: ["mdwb"]' in desktop_launcher


def test_windows_bundle_declares_active_model_configuration_and_packages() -> None:
    active_packages = json.loads((ROOT / "models" / "active-packages.json").read_text(encoding="utf-8"))
    available_packages = json.loads((ROOT / "models" / "available-packages.json").read_text(encoding="utf-8"))
    active_transforms = json.loads((ROOT / "models" / "active-transforms.json").read_text(encoding="utf-8"))
    builder_config = (ROOT / "packaging" / "electron-builder.yml").read_text(encoding="utf-8")
    packaged_resources = set(re.findall(
        r"(?m)^  - from: ([^\r\n]+)\r?\n    to: ([^\r\n]+)$",
        builder_config,
    ))

    required_resources = {
        "models/active-packages.json",
        "models/available-packages.json",
        "models/active-transforms.json",
    }
    required_resources.update(
        f"models/{selection['active']}"
        for selection in active_packages["tasks"].values()
    )
    required_resources.update(
        f"models/{package}"
        for package in available_packages["packages"]
    )
    for selection in active_transforms["transforms"].values():
        required_resources.add(f"models/{selection['active']}")
        required_resources.update(
            f"models/{package}"
            for package in selection["available"]
        )
        required_resources.add(f"models/{selection['commercial_catalog']}")
        required_resources.add(f"models/{selection['design_space']}")

    for resource in required_resources:
        assert (resource, resource) in packaged_resources
    welding_source = "data/source/welding_consumable_multistage_synthetic_dataset.xlsx"
    assert (welding_source, welding_source) in packaged_resources


def test_windows_packaging_checks_every_registered_default_source(tmp_path: Path) -> None:
    packaging_script = (ROOT / "scripts" / "package-windows.ps1").read_text(encoding="utf-8")
    builder_config = (ROOT / "packaging" / "electron-builder.yml").read_text(encoding="utf-8")
    source_paths = {
        module.default_source.as_posix()
        for module in registered_task_modules().values()
    }
    packaged_roots = [
        ROOT / value.strip()
        for value in re.findall(r"(?m)^  - from: ([^\r\n]+)$", builder_config)
    ]
    completed = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "backend"
                / "scripts"
                / "operations"
                / "task_inventory.py"
            ),
            "--print-source-paths",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )

    assert source_paths
    assert set(json.loads(completed.stdout)) == source_paths
    assert "task_inventory.py --print-source-paths" in packaging_script
    assert 'models/active-transforms.json' in packaging_script
    assert '$activeTransforms.transforms.PSObject.Properties.Value' in packaging_script
    assert "ConvertFrom-Json" in packaging_script
    for source in source_paths:
        source_path = ROOT / source
        assert any(
            packaged_root == source_path
            or packaged_root.is_dir() and packaged_root in source_path.parents
            for packaged_root in packaged_roots
        ), source


def test_sidecar_diagnostics_are_utf8_across_python_and_electron() -> None:
    desktop_launcher = (ROOT / "apps" / "desktop" / "src" / "main.ts").read_text(encoding="utf-8")
    sidecar_spec = (ROOT / "packaging" / "sidecar.spec").read_text(encoding="utf-8")
    environment = {
        **os.environ,
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    completed = subprocess.run(
        [sys.executable, "-c", "import sys; sys.stderr.write('データ診断')"],
        env=environment,
        capture_output=True,
        check=True,
    )

    assert completed.stderr.decode("utf-8") == "データ診断"
    assert desktop_launcher.count('PYTHONUTF8: "1"') == 1
    assert desktop_launcher.count('PYTHONIOENCODING: "utf-8"') == 1
    assert desktop_launcher.count("sidecarEnvironment(port)") == 2
    assert desktop_launcher.count("sidecarEnvironment()") == 2
    assert 'chunk.toString("utf8")' in desktop_launcher
    assert "console=True" in sidecar_spec
    assert "windowsHide: true" in desktop_launcher


def test_sidecar_stream_configuration_handles_console_and_windowed_streams(monkeypatch) -> None:
    class RecordingStream:
        encoding = "cp932"
        errors = "strict"

        def reconfigure(self, *, encoding: str, errors: str) -> None:
            self.encoding = encoding
            self.errors = errors

    stdout = RecordingStream()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", None)

    configure_standard_streams()

    assert stdout.encoding == "utf-8"
    assert stdout.errors == "backslashreplace"


def test_packaged_launcher_uses_active_model_configuration_as_single_source() -> None:
    desktop_launcher = (ROOT / "apps" / "desktop" / "src" / "main.ts").read_text(encoding="utf-8")

    for override in (
        "MATERIAL_WORKBENCH_MODEL_PACKAGE:",
        "MATERIAL_WORKBENCH_HOT_ROLLING_MODEL_PACKAGE:",
        "MATERIAL_WORKBENCH_FLANK_WEAR_MODEL_PACKAGE:",
    ):
        assert override not in desktop_launcher
    assert "WORKBENCH_RESOURCE_ROOT: resources" in desktop_launcher


def test_packaged_launcher_source_is_in_extra_resources() -> None:
    desktop_launcher = (ROOT / "apps" / "desktop" / "src" / "main.ts").read_text(
        encoding="utf-8"
    )
    builder_config = (ROOT / "packaging" / "electron-builder.yml").read_text(
        encoding="utf-8"
    )
    source_match = re.search(
        r'WORKBENCH_SOURCE_PATH: join\(resources, "data", "source", "([^"]+)"\)',
        desktop_launcher,
    )

    assert source_match is not None
    source = f"data/source/{source_match.group(1)}"
    assert f"  - from: {source}\n    to: {source}" in builder_config.replace(
        "\r\n", "\n"
    )


def test_packaged_resources_include_chain_evaluation_artifact() -> None:
    builder_config = (ROOT / "packaging" / "electron-builder.yml").read_text(
        encoding="utf-8"
    )
    package_script = (ROOT / "scripts" / "package-windows.ps1").read_text(
        encoding="utf-8"
    )
    artifact = "models/evaluations/welding-consumable-a-b-c-v1.json"

    assert builder_config.count(artifact) == 2
    assert artifact in package_script


def test_packaging_cleanup_is_bounded_and_previous_outputs_are_opt_in() -> None:
    package_script = (ROOT / "scripts" / "package-windows.ps1").read_text(
        encoding="utf-8"
    )
    clean_script = (ROOT / "scripts" / "clean-generated.ps1").read_text(
        encoding="utf-8"
    )
    evidence_script = (ROOT / "scripts" / "clean-evidence.ps1").read_text(
        encoding="utf-8"
    )
    root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
    package_json = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    assert "[switch]$KeepPrevious" in package_script
    assert 'clean-generated.ps1") -ReleaseOnly' in package_script
    assert "if (-not $KeepPrevious)" in package_script
    assert '"release", "dist", "build"' in clean_script
    assert '"data", "models"' in clean_script
    assert "StartsWith($repositoryPrefix" in clean_script
    assert "win-unpacked" in package_script
    assert "$portableRoot" in package_script
    assert package_json["scripts"]["clean"].endswith("scripts/clean-generated.ps1")
    assert package_json["scripts"]["clean:dry-run"].endswith(
        "scripts/clean-generated.ps1 -DryRun"
    )
    assert '"test-results"' not in clean_script
    assert '"test-results"' in evidence_script
    assert '".dev-workspaces"' in evidence_script
    assert '"data/"' in evidence_script
    assert '"models/"' in evidence_script
    assert package_json["scripts"]["clean:evidence"].endswith(
        "scripts/clean-evidence.ps1"
    )
    assert "`npm run clean`" in root_readme
    assert "`npm run clean:evidence`" in root_readme
    assert "Workspace、`data/source/`、`models/packages/`" in root_readme


@pytest.mark.skipif(
    shutil.which("powershell") is None,
    reason="PowerShell cleanup execution is covered on Windows",
)
def test_generated_cleanup_dry_run_and_apply_only_remove_bounded_targets(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    cache = repository / "backend/scripts/experiments/spikes/__pycache__"
    source = repository / "data/source/source.xlsx"
    package = repository / "models/packages/package/manifest.json"
    tracked_script = repository / "backend/scripts/experiments/spikes/spike.py"
    generated = repository / "build/generated.txt"
    copied_script = repository / "scripts/clean-generated.ps1"
    for path in (cache / "spike.pyc", source, package, tracked_script, generated):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep" if path != generated else "generated", encoding="utf-8")

    script = ROOT / "scripts/clean-generated.ps1"
    copied_script.parent.mkdir(parents=True)
    shutil.copyfile(script, copied_script)
    dry_run = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(copied_script),
            "-DryRun",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Would remove Python cache:" in dry_run.stdout
    assert cache.is_dir()
    assert generated.is_file()

    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(copied_script),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert not cache.exists()
    assert not generated.exists()
    assert source.read_text(encoding="utf-8") == "keep"
    assert package.read_text(encoding="utf-8") == "keep"
    assert tracked_script.read_text(encoding="utf-8") == "keep"


@pytest.mark.skipif(
    shutil.which("powershell") is None,
    reason="PowerShell cleanup execution is covered on Windows",
)
def test_generated_cleanup_refuses_intermediate_reparse_points(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    copied_script = repository / "scripts/clean-generated.ps1"
    linked_directory = repository / "backend/scripts/linked"
    outside = tmp_path / "outside"
    outside_cache = outside / "__pycache__/outside.pyc"
    copied_script.parent.mkdir(parents=True)
    linked_directory.parent.mkdir(parents=True)
    outside_cache.parent.mkdir(parents=True)
    outside_cache.write_text("keep", encoding="utf-8")
    shutil.copyfile(ROOT / "scripts/clean-generated.ps1", copied_script)
    environment = {
        **os.environ,
        "CLEANUP_TEST_LINK": str(linked_directory),
        "CLEANUP_TEST_TARGET": str(outside),
    }
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "New-Item -ItemType Junction "
                "-Path $env:CLEANUP_TEST_LINK "
                "-Target $env:CLEANUP_TEST_TARGET | Out-Null"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(copied_script),
                "-DryRun",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "reparse point" in f"{result.stdout}\n{result.stderr}"
        assert outside_cache.read_text(encoding="utf-8") == "keep"
    finally:
        linked_directory.rmdir()


def test_packaged_smoke_executes_the_stage_a_transform_api() -> None:
    packaged_smoke = (ROOT / "scripts" / "smoke-packaged.mjs").read_text(
        encoding="utf-8"
    )

    assert 'authenticatedFetch("/api/transforms")' in packaged_smoke
    assert '"/api/transforms/welding-stage-a-v1/execute"' in packaged_smoke
    assert 'method: "POST"' in packaged_smoke
    assert "stageA.outputs.length, 31" in packaged_smoke
    assert "powder_blend_cost_yen_per_kg_core > 0" in packaged_smoke


def test_packaged_smoke_uses_the_server_owned_lifecycle_actor() -> None:
    packaged_smoke = (ROOT / "scripts" / "smoke-packaged.mjs").read_text(
        encoding="utf-8"
    )

    assert "packaged lifecycle probe" in packaged_smoke
    assert 'actor: "packaged-smoke"' not in packaged_smoke


def test_packaged_smoke_covers_workspace_backup_restore_and_tamper_rejection() -> None:
    packaged_smoke = (ROOT / "scripts" / "smoke-packaged.mjs").read_text(
        encoding="utf-8"
    )

    assert "dialog.showSaveDialog" in packaged_smoke
    assert "dialog.showOpenDialog" in packaged_smoke
    assert "workspace-tampered.mdwb" in packaged_smoke
    assert 'getByRole("alert")' in packaged_smoke
    assert "packaged-portable-workspace.mdwb" in packaged_smoke
    assert "この内容へ復元" in packaged_smoke
    assert "PACKAGED_STARTUP_TIMEOUT_MS" in packaged_smoke
    assert "packaged-smoke-${mode}-before-backup" in packaged_smoke
    assert "packaged-smoke-${mode}-after-backup" in packaged_smoke
    assert "Workspaceを復元し、APIの起動確認まで完了しました。" in packaged_smoke
    assert "readSmokeProject()).notes, restoredMarker" in packaged_smoke
    assert packaged_smoke.index(
        'getByText("Workspaceを復元し、APIの起動確認まで完了しました。")'
    ) < packaged_smoke.index(
        '"heading",\n    { name: "ワークスペース", exact: true }'
    )


def test_packaged_smoke_covers_a_non_material_decision_journey() -> None:
    packaged_smoke = (ROOT / "scripts" / "smoke-packaged.mjs").read_text(
        encoding="utf-8"
    )
    upgrade_smoke = (
        ROOT / "scripts" / "smoke-packaged-upgrade.mjs"
    ).read_text(encoding="utf-8")
    for marker in (
        "flank-wear-v1",
        "cutting-flank-wear-v1",
        "/api/screening?project_id=",
        "robustness-analysis-v1",
        "/snapshots",
        "/actuals?expected_revision=",
        "/decision",
        "/history",
        "工具摩耗の実測と予測を確認",
    ):
        assert marker in packaged_smoke
    assert "Domain-neutral acceptance: 工具摩耗" in upgrade_smoke
    assert "工具摩耗の実測と予測を確認" in upgrade_smoke


def test_windows_delivery_upgrades_without_moving_the_legacy_workspace() -> None:
    delivery_smoke = (
        ROOT / "scripts" / "smoke-windows-delivery.ps1"
    ).read_text(encoding="utf-8")
    windows_upgrade_smoke = (
        ROOT / "scripts" / "smoke-windows-upgrade.ps1"
    ).read_text(encoding="utf-8")
    upgrade_smoke = (
        ROOT / "scripts" / "smoke-packaged-upgrade.mjs"
    ).read_text(encoding="utf-8")
    packaged_smoke = (ROOT / "scripts" / "smoke-packaged.mjs").read_text(
        encoding="utf-8"
    )

    assert "smoke-windows-upgrade.ps1" in delivery_smoke
    assignment = (
        '$workspaceDatabasePath = Join-Path $smokeRoot '
        '"local-app-data/Material Decision Workbench/workbench.db"'
    )
    assert assignment in delivery_smoke
    assert delivery_smoke.index(assignment) < delivery_smoke.index(
        "scripts/smoke-packaged.mjs"
    )
    assert "-WorkspaceDatabasePath $workspaceDatabasePath" in delivery_smoke
    assert "Test-Path -LiteralPath $workspaceDatabasePath" in delivery_smoke
    assert "KeepSmokeOnFailure" in delivery_smoke
    assert "PreviousInstallerPath" in delivery_smoke
    assert "AllowUserInstallerState" in delivery_smoke
    assert "function Assert-NoNonSmokeInstallerState" in delivery_smoke
    assert "refusing to replace a non-smoke shortcut" in delivery_smoke
    assert "refusing to replace a non-smoke installer registration" in delivery_smoke
    assert delivery_smoke.index(
        "Assert-NoNonSmokeInstallerState -OwnedRoot $installedRoot"
    ) < delivery_smoke.index("if (Test-Path -LiteralPath $smokeRoot)")
    assert '"Material Decision Workbench.exe"' in delivery_smoke
    assert '"Uninstall Material Decision Workbench.exe"' in delivery_smoke
    assert "legacy installed artifact remained after upgrade" in delivery_smoke
    assert "legacy shortcut remained after upgrade" in delivery_smoke
    assert "function Test-SmokeShortcutOwned" in delivery_smoke
    assert "function Remove-SmokeInstallation" in delivery_smoke
    assert "Test-SmokeShortcutOwned -ShortcutPath" in delivery_smoke
    assert "Remove-SmokeInstallation $installedRoot" in delivery_smoke
    assert 'process.argv[4] ?? "Evidence Decision Workbench.exe"' in packaged_smoke
    assert "$databaseBeforeUpgrade" in windows_upgrade_smoke
    assert "$databaseAfterUpgrade" in windows_upgrade_smoke
    assert "function Get-Sha256Hex" in windows_upgrade_smoke
    assert "[Security.Cryptography.SHA256]::Create()" in windows_upgrade_smoke
    assert "Get-FileHash" not in windows_upgrade_smoke
    assert '-ArgumentList "/S"' in windows_upgrade_smoke
    assert '"/D=$resolvedInstalledRoot"' not in windows_upgrade_smoke
    assert "did not inherit the existing install location" in windows_upgrade_smoke
    assert "smoke-packaged-upgrade.mjs" in windows_upgrade_smoke
    assert "Evidence Decision Workbench.exe" in upgrade_smoke
    assert "packaged-smoke-portable-before-backup" in upgrade_smoke


def test_desktop_startup_recovery_distinguishes_workspace_and_runtime_failures() -> None:
    desktop_main = (ROOT / "apps" / "desktop" / "src" / "main.ts").read_text(
        encoding="utf-8"
    )

    assert 'readonly stage?: StartupFailureStage' in desktop_main
    assert 'error.stage === "database" || error.stage === "catalog"' in desktop_main
    assert (
        '["バックアップから復元", "再試行", "診断ログを開く", "終了"]'
        in desktop_main
    )
    assert (
        '["再試行", "診断ログを開く", "バックアップから復元", "終了"]'
        in desktop_main
    )
    assert "ローカルAPIを起動できませんでした。" in desktop_main


def test_workspace_ipc_trusts_the_packaged_document_path_not_its_navigation_query() -> None:
    desktop_main = (ROOT / "apps" / "desktop" / "src" / "main.ts").read_text(
        encoding="utf-8"
    )

    assert 'actual.protocol === "file:"' in desktop_main
    assert "resolve(fileURLToPath(actual))" in desktop_main
    assert "actual.href === pathToFileURL(rendererPath()).href" not in desktop_main


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
