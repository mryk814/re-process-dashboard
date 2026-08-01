from __future__ import annotations

import argparse
import json
from pathlib import Path

from decision_workbench.research.generative_design_lab import (
    build_report,
    render_adoption_memo,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the bounded offline Generative Design Lab protocol."
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("docs/research/generative-design-lab-report.json"),
    )
    parser.add_argument(
        "--memo",
        type=Path,
        default=Path("docs/research/generative-design-lab-adoption-memo.md"),
    )
    parser.add_argument("--budget", type=int, default=128)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--seeds", type=int, nargs="+", default=[17, 41, 83])
    args = parser.parse_args()

    report = build_report(
        seeds=tuple(args.seeds),
        budget=args.budget,
        batch_size=args.batch_size,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.memo.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.memo.write_text(render_adoption_memo(report), encoding="utf-8")
    print(f"Wrote {args.report} ({report['result_digest']})")
    print(f"Wrote {args.memo}")


if __name__ == "__main__":
    main()
