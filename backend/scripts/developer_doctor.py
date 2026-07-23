from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.developer_experience import run_developer_doctor  # noqa: E402


def _render_human(report: dict[str, object]) -> str:
    icons = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
    lines = [
        f"Material Decision Workbench Developer Doctor: {str(report['status']).upper()}",
        "",
    ]
    for check in report["checks"]:
        item = dict(check)
        lines.append(f"[{icons[str(item['severity'])]}] {item['title']}: {item['summary']}")
        if item.get("cause"):
            lines.append(f"  原因: {item['cause']}")
        for command in item.get("commands", []):
            lines.append(f"  次: {command}")
    inspection = report.get("source_inspection")
    if inspection:
        lines.extend(["", "Profile候補:"])
        for candidate in inspection["candidates"]:
            lines.append(f"  {candidate['score']:>3}%  {candidate['profile_id']}")
    return "\n".join(lines)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="開発環境・Task登録・データ/Profile差分を診断します。")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-generated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    report = run_developer_doctor(
        root=ROOT,
        source=args.source,
        profile=args.profile,
        include_generated_checks=not args.skip_generated,
    )
    payload = report.model_dump(mode="json")
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else _render_human(payload))
    return report.code


if __name__ == "__main__":
    raise SystemExit(main())
