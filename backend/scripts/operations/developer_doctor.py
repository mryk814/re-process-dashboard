from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.developer_experience import run_developer_doctor  # noqa: E402


def _render_human(report: dict[str, object]) -> str:
    icons = {"ok": "OK", "warning": "WARN", "error": "ERROR"}
    lines = [
        f"Evidence Decision Workbench Developer Doctor: {str(report['status']).upper()}",
        "",
    ]
    for check in report["checks"]:
        item = dict(check)
        lines.append(f"[{icons[str(item['severity'])]}] {item['title']}: {item['summary']}")
        if item.get("cause"):
            lines.append(f"  原因: {item['cause']}")
        for command in item.get("commands", []):
            lines.append(f"  次: {command['display_text']}")
    inspection = report.get("source_inspection")
    if inspection:
        lines.extend(["", "Excel / Profile診断:", "  選択候補:"])
        for candidate in inspection["candidates"]:
            selected = " *" if candidate["profile_path"] == inspection["selected_profile"] else ""
            lines.append(f"    {candidate['score']:>3}%  {candidate['profile_id']}{selected}")
        selected_candidate = next(
            (
                candidate
                for candidate in inspection["candidates"]
                if candidate["profile_path"] == inspection["selected_profile"]
            ),
            None,
        )
        if selected_candidate:
            lines.append(f"  対応Task: {', '.join(selected_candidate['task_ids']) or 'なし'}")
            lines.append(f"  不足シート: {', '.join(selected_candidate['missing_sheets']) or 'なし'}")
            missing_columns = [
                f"{sheet}: {', '.join(columns)}"
                for sheet, columns in selected_candidate["missing_columns"].items()
            ]
            lines.append(f"  不足列: {' / '.join(missing_columns) or 'なし'}")
            lines.append(f"  単位差候補: {', '.join(selected_candidate['possible_unit_differences']) or 'なし'}")
            extra_columns = [
                f"{sheet}: {', '.join(columns)}"
                for sheet, columns in selected_candidate["extra_columns"].items()
            ]
            lines.append(f"  未知の追加列: {' / '.join(extra_columns) or 'なし'}")
        lines.extend([
            "  学習可能件数（eligibilityを満たす観測行）:",
            *(
                [f"    {task}: {count}" for task, count in inspection["learning_counts"].items()]
                or ["    なし"]
            ),
            "  出力別観測件数（値が存在する観測行）:",
            *(
                [f"    {output}: {count}" for output, count in inspection["output_counts"].items()]
                or ["    なし"]
            ),
            "  判定:",
        ])
        for name, decision in inspection["decisions"].items():
            lines.append(f"    {name}: {decision['decision']} — {decision['reason']}")
        lines.append("  データの用途:")
        for purpose in inspection["data_purposes"]:
            lines.append(
                f"    {purpose['label']}: Package再構築 {purpose['package_rebuild']} — {purpose['description']}"
            )
        lines.append("  次のコマンド:")
        for command in inspection["commands"]:
            lines.append(f"    {command['display_text']}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="開発環境・Task登録・データ/Profile差分を診断します。")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--skip-generated", action="store_true", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
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
