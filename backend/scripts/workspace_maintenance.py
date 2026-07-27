from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.developer_experience.workspace_maintenance import (
    deactivate_package_registration,
    inspect_package_registrations,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explicit, audited maintenance for a stopped workspace."
    )
    parser.add_argument(
        "--database",
        default=os.getenv("WORKBENCH_DB_PATH", "data/workbench.db"),
    )
    parser.add_argument("--json", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("inspect")
    deactivate = commands.add_parser("deactivate")
    deactivate.add_argument("--package-ref", required=True)
    deactivate.add_argument("--reason", required=True)
    return parser


def _human_inspection(database: Path, registrations: list[dict[str, object]]) -> None:
    print(f"Workspace: {database.expanduser().resolve()}")
    if not registrations:
        print("Model Package登録はありません。")
        return
    for item in registrations:
        state = "利用中" if item["active"] else "利用停止"
        references = item["referenced_by"]
        reference_label = (
            f"参照中: {', '.join(str(value) for value in references)}"
            if references
            else "未参照"
        )
        print(
            f"- {item['id']} [{state}] {item['package_id']} "
            f"task={item['task_id']} / {reference_label}"
        )
        print(f"  contract={item['task_contract_digest']}")
        print(f"  manifest={item['manifest_digest']}")


def main() -> int:
    arguments = _parser().parse_args()
    database = Path(arguments.database)
    if arguments.command == "inspect":
        registrations = inspect_package_registrations(database)
        if arguments.json:
            print(
                json.dumps(
                    {
                        "schema_version": "workspace-maintenance-inspection/v1",
                        "database": str(database.expanduser().resolve()),
                        "registrations": registrations,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        else:
            _human_inspection(database, registrations)
        return 0
    result = deactivate_package_registration(
        database,
        reference_id=arguments.package_ref,
        reason=arguments.reason,
    )
    if arguments.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        package = result["package_ref"]
        print(f"利用停止: {package['id']} ({package['package_id']})")
        print(f"理由: {result['audit_event']['reason']}")
        print("次回起動時、未参照であることを再確認して現行contractを登録します。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
