from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from fastapi.testclient import TestClient

from decision_workbench.app import create_app


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List or install bundled Sample Gallery projects."
    )
    parser.add_argument("action", choices=("list", "add", "add-all"))
    parser.add_argument("project_id", nargs="?")
    parser.add_argument("--database", required=True)
    parser.add_argument("--data-library", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.action == "add" and not arguments.project_id:
        raise SystemExit("add requires PROJECT_ID")
    if arguments.action != "add" and arguments.project_id:
        raise SystemExit(f"{arguments.action} does not accept PROJECT_ID")

    with TestClient(
        create_app(
            db_path=Path(arguments.database),
            data_library_path=Path(arguments.data_library),
        )
    ) as client:
        if arguments.action == "list":
            response = client.get("/api/sample-gallery")
        else:
            response = client.post(
                "/api/sample-gallery",
                json={
                    "project_ids": (
                        [arguments.project_id]
                        if arguments.action == "add"
                        else []
                    )
                },
            )
        if not response.is_success:
            print(response.text, file=sys.stderr)
            return 1
        payload = response.json()
        if arguments.action == "list":
            output = payload
        else:
            output = {
                "installed": [
                    {"project_id": item["id"], "name": item["name"]}
                    for item in payload
                ],
                "count": len(payload),
            }
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
