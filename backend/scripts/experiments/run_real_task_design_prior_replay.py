from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from decision_workbench.research.real_task_design_prior_replay import (  # noqa: E402
    build_report,
    render_memo,
)


REPORT = ROOT / "docs/research/real-task-design-prior-replay-report.json"
MEMO = ROOT / "docs/research/real-task-design-prior-replay.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    report = build_report()
    report_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    memo_text = render_memo(report)
    if args.check:
        current_report = json.loads(REPORT.read_text(encoding="utf-8"))
        expected_stable = json.loads(json.dumps({
            **report,
            "runs": [
                {
                    **run,
                    "operation": {
                        key: value
                        for key, value in run["operation"].items()
                        if key != "observed_runtime_ms"
                    },
                }
                for run in report["runs"]
            ],
            "summaries": [
                {
                    key: value
                    for key, value in summary.items()
                    if key != "runtime_ms_range"
                }
                for summary in report["summaries"]
            ],
        }, ensure_ascii=False))
        current_stable = {
            **current_report,
            "runs": [
                {
                    **run,
                    "operation": {
                        key: value
                        for key, value in run["operation"].items()
                        if key != "observed_runtime_ms"
                    },
                }
                for run in current_report["runs"]
            ],
            "summaries": [
                {
                    key: value
                    for key, value in summary.items()
                    if key != "runtime_ms_range"
                }
                for summary in current_report["summaries"]
            ],
        }
        if current_stable != expected_stable:
            print("real-task Design Prior replay report is stale", file=sys.stderr)
            return 1
        if MEMO.read_text(encoding="utf-8") != render_memo(current_report):
            print("real-task Design Prior replay memo is stale", file=sys.stderr)
            return 1
        return 0
    REPORT.write_text(report_text, encoding="utf-8", newline="\n")
    MEMO.write_text(memo_text, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
