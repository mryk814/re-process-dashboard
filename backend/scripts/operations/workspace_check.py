from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from decision_workbench.developer_experience.workspace_preflight import (
    CurrentWorkspacePreflightRegistry,
    WorkspacePreflightReport,
    inspect_workspace_compatibility,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only preflight for repo/workspace compatibility."
    )
    parser.add_argument(
        "--database",
        default=os.getenv("WORKBENCH_DB_PATH", "data/workbench.db"),
    )
    parser.add_argument("--json", action="store_true")
    return parser


def _print_human(report: WorkspacePreflightReport) -> None:
    print(f"Workspace: {report.database}")
    if not report.database_exists:
        print("状態: 新しいworkspaceとして作成できます。")
        return
    if report.status == "ok":
        print("状態: repoとworkspaceの固定参照は整合しています。")
        return
    print(f"状態: 起動前に解決が必要な不整合が{len(report.findings)}件あります。")
    for finding in report.findings:
        print(f"- [{finding.stage}] {finding.resource_id}: {finding.cause}")
        print(f"  影響: {finding.impact}")
        print(f"  復旧: {finding.recovery_hint}")


def main() -> int:
    arguments = _parser().parse_args()
    database = Path(arguments.database)
    if database.exists():
        try:
            registry = CurrentWorkspacePreflightRegistry()
        except (OSError, ValueError, KeyError) as exc:
            report = WorkspacePreflightReport(
                status="error",
                code=1,
                database=str(database.expanduser().resolve()),
                database_exists=True,
                findings=[
                    {
                        "severity": "error",
                        "stage": "resource",
                        "resource_id": "active-task-resources",
                        "cause": str(exc),
                        "impact": (
                            "Task、データ、Model Packageの対応を確定できないため、"
                            "APIを安全に起動できません。"
                        ),
                        "recovery_hint": (
                            "active-packages.json、TaskDefinition、TaskModuleの"
                            "対応を確認してから再起動してください。"
                        ),
                    }
                ],
            )
        else:
            report = inspect_workspace_compatibility(database, registry)
    else:
        report = WorkspacePreflightReport(
            status="ok",
            code=0,
            database=str(database.expanduser().resolve()),
            database_exists=False,
            findings=[],
        )
    if arguments.json:
        print(
            json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        _print_human(report)
    return report.code


if __name__ == "__main__":
    sys.exit(main())
