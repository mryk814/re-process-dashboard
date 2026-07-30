from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.developer_experience.workspace_lifecycle import (
    WorkspacePruneRefused,
    list_branch_workspaces,
    prune_branch_workspace,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only branch Workspace inventory and explicit safe prune."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list")
    list_parser.add_argument("--json", action="store_true")
    prune_parser = commands.add_parser("prune")
    prune_parser.add_argument("--database", type=Path, required=True)
    prune_parser.add_argument("--json", action="store_true")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "list":
        workspaces = list_branch_workspaces(ROOT)
        if arguments.json:
            print(
                json.dumps(
                    {
                        "schema_version": "branch-workspace-inventory/v1",
                        "workspaces": workspaces,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        elif not workspaces:
            print("Branch Workspaceはありません。")
        else:
            for item in workspaces:
                branch = item["branch"] or "不明"
                state = "prunable" if item["prunable"] else "/".join(item["protection_reasons"])
                print(
                    f"{item['path']}\n"
                    f"  branch={branch} updated={item['updated_at']} "
                    f"size={item['size_bytes']} state={state}"
                )
        return 0

    try:
        result = prune_branch_workspace(
            ROOT,
            database=arguments.database,
        )
    except WorkspacePruneRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if arguments.json:
        print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    else:
        print(f"Pruned: {result['database']} ({result['branch']})")
        for path in result["removed"]:
            print(f"- {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
