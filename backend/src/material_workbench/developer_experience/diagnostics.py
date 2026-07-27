from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from material_workbench.developer_experience.commands import developer_command as command
from material_workbench.developer_experience.schemas import (
    DeveloperCheck,
    DeveloperDoctorReport,
)
from material_workbench.developer_experience.source_inspection import inspect_source_against_profiles
from material_workbench.modeling.active_package_history import (
    ActivePackageHistoryReport,
    check_active_package_history,
)
from material_workbench.modeling.model_lifecycle import (
    ACTIVE_PACKAGES_PATH,
    load_active_packages,
    resolve_configured_package,
)
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.task_modules import registered_task_modules
from material_workbench.tasks.task_registry import load_task_contracts


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def compare_task_sets(task_sets: Mapping[str, Iterable[str]]) -> DeveloperCheck:
    normalized = {name: sorted(set(values)) for name, values in task_sets.items()}
    unique = {tuple(values) for values in normalized.values()}
    severity = "ok" if len(unique) <= 1 else "error"
    return DeveloperCheck(
        id="task-sets",
        section="registry",
        title="Task登録の整合",
        severity=severity,
        summary="TaskDefinition・TaskModule・active packageが一致しています。"
        if severity == "ok"
        else "Task登録集合にずれがあります。",
        cause=None if severity == "ok" else "Task追加時にTaskModuleまたはactive package更新が漏れています。",
        impact=None if severity == "ok" else "起動時検証・学習・推論で異なるTask集合を参照します。",
        commands=[
            command("npm", ["run", "task:inventory"]),
            command("npm", ["run", "dev:doctor", "--", "--json"]),
        ],
        details=normalized,
    )


def active_package_history_check(history: ActivePackageHistoryReport) -> DeveloperCheck:
    if not history.available:
        severity = "warning"
        summary = "git履歴を読めないため、直接編集を検査していません。"
        cause = history.unavailable_reason
        impact = "active Packageの切替履歴とpreviousの整合を判定できません。"
    elif not history.ok:
        severity = "error"
        summary = "active Package履歴とpreviousにrollbackを妨げる不整合があります。"
        cause = json.dumps(
            [item.model_dump(mode="json") for item in (*history.drift, *history.broken_previous)],
            ensure_ascii=False,
        )
        impact = "npm run model:rollback で直前のPackageへ戻せません。"
    elif history.accepted:
        severity = "ok"
        summary = (
            "rollback可能な直前Packageとpreviousは一致しています。"
            f"rollback不能な旧版{len(history.accepted)}件のprevious=nullは理由付きで受理しました。"
        )
        cause = None
        impact = None
    else:
        severity = "ok"
        summary = "rollback可能な直前Packageとpreviousの履歴が一致しています。"
        cause = None
        impact = None
    return DeveloperCheck(
        id="active-package-history",
        section="packages",
        title="active切替の記録",
        severity=severity,
        summary=summary,
        cause=cause,
        impact=impact,
        commands=[command("npm", ["run", "model:activate", "--", "--task", "<task-id>", "--package", "<path>"])],
        details=history.model_dump(mode="json"),
    )


def _command_check(root: Path, identifier: str, title: str, command: list[str]) -> DeveloperCheck:
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DeveloperCheck(
            id=identifier,
            section="generated",
            title=title,
            severity="error",
            summary="診断コマンドを実行できませんでした。",
            cause=str(exc),
            commands=[command_from_argv(command)],
        )
    output = "\n".join(value.strip() for value in (completed.stdout, completed.stderr) if value.strip())
    return DeveloperCheck(
        id=identifier,
        section="generated",
        title=title,
        severity="ok" if completed.returncode == 0 else "error",
        summary="生成物は現在の定義と一致しています。" if completed.returncode == 0 else "生成物が定義からずれています。",
        cause=None if completed.returncode == 0 else output[-2000:],
        impact=None if completed.returncode == 0 else "API型または開発者向け一覧が古い可能性があります。",
        commands=[command_from_argv(command, root=root)],
    )


def command_from_argv(arguments: list[str], *, root: Path | None = None):
    display = " ".join(arguments)
    if root is not None:
        display = display.replace(str(root) + os.sep, "")
    return command(arguments[0], arguments[1:], display_text=display, platform="windows" if arguments[0].lower().endswith(".cmd") else "cross-platform")


def _environment_checks(root: Path) -> list[DeveloperCheck]:
    required = {
        "uv": shutil.which("uv"),
        "node": shutil.which("node"),
        "npm": shutil.which("npm.cmd") or shutil.which("npm"),
    }
    missing = sorted(name for name, value in required.items() if not value)
    checks = [DeveloperCheck(
        id="toolchain",
        section="environment",
        title="開発ツール",
        severity="ok" if not missing else "error",
        summary="uv・Node.js・npmを利用できます。" if not missing else f"不足: {', '.join(missing)}",
        commands=[command("uv", ["sync", "--extra", "dev"]), command("npm", ["install"])],
        details={name: value for name, value in required.items()},
    )]
    for name in (".venv", "node_modules"):
        exists = (root / name).exists()
        checks.append(DeveloperCheck(
            id=f"resource-{name.lstrip('.')}",
            section="environment",
            title=name,
            severity="ok" if exists else "warning",
            summary="存在します。" if exists else "未作成です。",
            commands=[
                command("uv", ["sync", "--extra", "dev"])
                if name == ".venv"
                else command("npm", ["install"])
            ],
        ))
    return checks


def run_developer_doctor(
    *,
    root: Path = REPOSITORY_ROOT,
    source: Path | None = None,
    profile: Path | None = None,
    include_generated_checks: bool = True,
) -> DeveloperDoctorReport:
    root = root.resolve()
    checks = _environment_checks(root)
    modules = registered_task_modules()
    contracts = load_task_contracts()
    active = load_active_packages(ACTIVE_PACKAGES_PATH)
    inventory_path = root / "docs" / "task-inventory.json"
    try:
        inventory_document = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory_tasks = {
            str(item["task_id"])
            for item in inventory_document.get("tasks", [])
        }
    except (OSError, ValueError, TypeError, KeyError):
        inventory_tasks = set()
    checks.append(compare_task_sets({
        "TaskDefinition": contracts,
        "TaskModule": modules,
        "active-packages": active.tasks,
        "task-inventory": inventory_tasks,
        "workflow": modules,
        "verifier": modules,
    }))

    missing_sources = sorted({
        str(module.default_source)
        for module in modules.values()
        if not (root / module.default_source).exists()
    })
    checks.append(DeveloperCheck(
        id="dataset-sources",
        section="datasets",
        title="登録済みデータソース",
        severity="ok" if not missing_sources else "error",
        summary="TaskModuleのデータソースが存在します。" if not missing_sources else "存在しないデータソースがあります。",
        details={"missing": missing_sources},
    ))

    active_path = root / "models" / "active-packages.json"
    checks.append(DeveloperCheck(
        id="active-packages",
        section="packages",
        title="active-packages.json",
        severity="ok" if active_path.exists() else "error",
        summary="active package設定を読み込めます。" if active_path.exists() else "active package設定がありません。",
        commands=[command("npm", ["run", "model:status"])],
        details={"path": str(active_path)},
    ))
    history = check_active_package_history(
        config_path=active_path if active_path.exists() else ACTIVE_PACKAGES_PATH,
        repository_root=root,
    )
    checks.append(active_package_history_check(history))

    package_errors: dict[str, str] = {}
    package_details: dict[str, object] = {}
    for task_id in sorted(modules):
        try:
            package_root = resolve_configured_package(task_id)
            verified = verify_model_package(package_root, task_id=task_id)
            package_details[task_id] = verified.model_dump()
        except Exception as exc:  # report every package instead of stopping at the first one
            package_errors[task_id] = str(exc)
    checks.append(DeveloperCheck(
        id="package-contracts",
        section="packages",
        title="Model Package契約",
        severity="ok" if not package_errors else "error",
        summary="active packageのmanifest・Task・Profile・provenanceが一致しています。"
        if not package_errors
        else "active packageの契約違反があります。",
        cause=None if not package_errors else json.dumps(package_errors, ensure_ascii=False),
        impact=None if not package_errors else "起動または推論が失敗します。",
        commands=[command("npm", ["run", "model:verify", "--", "--task", "<task-id>"])],
        details=package_details,
    ))

    packaging = root / "packaging" / "electron-builder.yml"
    packaging_text = packaging.read_text(encoding="utf-8") if packaging.exists() else ""
    missing_packaged_resources = [
        value
        for value in ("models/active-packages.json", "models/packages", "data/source")
        if value not in packaging_text.replace("\\", "/")
    ]
    checks.append(DeveloperCheck(
        id="packaged-resources",
        section="packaging",
        title="Desktop同梱リソース",
        severity="ok" if packaging.exists() and not missing_packaged_resources else "error",
        summary="active設定・Model Package・データソースの同梱定義があります。"
        if packaging.exists() and not missing_packaged_resources
        else "Desktop同梱定義に不足があります。",
        details={"missing": missing_packaged_resources, "config": str(packaging)},
        commands=[command("npm", ["run", "package:windows"]), command("npm", ["run", "smoke:packaged"])],
    ))

    if include_generated_checks:
        npm_executable = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
        checks.extend([
            _command_check(
                root, "task-inventory", "Task inventory",
                ["uv", "run", "python", "backend/scripts/task_inventory.py", "--check"],
            ),
            _command_check(
                root, "openapi", "OpenAPI schema",
                ["uv", "run", "python", "backend/scripts/export_openapi.py", "--check"],
            ),
            _command_check(
                root, "typescript-api", "TypeScript API types",
                [npm_executable, "run", "api:check", "-w", "apps/web"],
            ),
        ])

    inspection = None
    source_error = False
    if source is not None:
        try:
            inspection = inspect_source_against_profiles(source, profile_path=profile)
        except (OSError, ValueError) as exc:
            source_error = True
            checks.append(DeveloperCheck(
                id="source-inspection",
                section="source",
                title="指定Excel",
                severity="error",
                summary="指定Excelを診断できませんでした。",
                cause=str(exc),
            ))
        else:
            profile_decision = inspection.decisions["new_profile_required"].decision
            checks.append(DeveloperCheck(
                id="source-inspection",
                section="source",
                title="指定Excel",
                severity="warning" if profile_decision != "no" else "ok",
                summary="Profile差分の確認が必要です。" if profile_decision != "no" else "既存Profileを再利用できます。",
                details={"selected_profile": inspection.selected_profile},
            ))

    errors = [check for check in checks if check.severity == "error"]
    warnings = [check for check in checks if check.severity == "warning"]
    if source_error:
        code = 2
    elif inspection is not None and inspection.ambiguous:
        code = 3
    elif errors:
        code = 1
    else:
        code = 0
    status = "error" if errors or code in (2, 3) else "warning" if warnings else "ok"
    recommendations = [
        next_command.display_text
        for check in checks
        if check.severity != "ok"
        for next_command in check.commands
    ]
    if inspection:
        recommendations.extend(inspection.recommendations)
    return DeveloperDoctorReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        code=code,
        checks=checks,
        task_ids=sorted(modules),
        recommendations=list(dict.fromkeys(recommendations)),
        source_inspection=inspection,
    )
