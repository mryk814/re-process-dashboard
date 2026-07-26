from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.developer_experience.data_lifecycle_benchmark import (
    FixtureShape,
    run_benchmark_case,
    run_concurrency_probe,
    run_history_probe,
    summarize_report,
    synthetic_payload,
)


def _run(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def _worker_command(*args: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), *args]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure the production Data Lifecycle service and SQLite repository "
            "without changing the production schema."
        )
    )
    parser.add_argument(
        "--scales",
        nargs="+",
        type=int,
        default=[1_000, 10_000, 100_000],
    )
    parser.add_argument(
        "--shapes",
        nargs="+",
        choices=("narrow", "representative"),
        default=["narrow", "representative"],
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT / "artifacts" / "data-lifecycle-benchmark",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "artifacts"
        / "data-lifecycle-benchmark"
        / "report.json",
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        help="Write a compact, repository-friendly evidence report.",
    )
    parser.add_argument("--history-rows", type=int, default=250)
    parser.add_argument("--history-depth", type=int, default=10)
    parser.add_argument("--unrelated-connectors", type=int, default=10)
    parser.add_argument("--concurrency-rows", type=int, default=250)
    parser.add_argument("--concurrency-iterations", type=int, default=10)
    parser.add_argument(
        "--packaged-results",
        type=Path,
        action="append",
        default=[],
        help="Packaged probe JSON. Repeat for portable and installed modes.",
    )
    parser.add_argument(
        "--reuse-core-report",
        type=Path,
        help="Reuse raw case/history/concurrency results and only rebuild decisions.",
    )
    parser.add_argument("--worker", choices=("case", "history", "concurrency"))
    parser.add_argument("--rows", type=int)
    parser.add_argument("--shape", choices=("narrow", "representative"))
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--case-workspace", type=Path)
    parser.add_argument("--environment-label", default="development")
    parser.add_argument("--no-forced-lock", action="store_true")
    return parser


def _run_worker(args: argparse.Namespace) -> int:
    if args.case_workspace is None:
        raise SystemExit("--worker requires --case-workspace")
    if args.worker == "case":
        if args.rows is None or args.shape is None:
            raise SystemExit("case worker requires --rows and --shape")
        result = run_benchmark_case(
            row_count=args.rows,
            shape=args.shape,
            workspace=args.case_workspace,
            environment_label=args.environment_label,
            fixture_path=args.fixture,
        )
    elif args.worker == "history":
        result = run_history_probe(
            workspace=args.case_workspace,
            row_count=args.history_rows,
            history_depth=args.history_depth,
            unrelated_connectors=args.unrelated_connectors,
        )
    else:
        result = run_concurrency_probe(
            workspace=args.case_workspace,
            row_count=args.concurrency_rows,
            iterations=args.concurrency_iterations,
            include_forced_lock=not args.no_forced_lock,
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    args = _parser().parse_args()
    if args.worker:
        return _run_worker(args)
    if (
        not args.scales
        or any(scale < 1 for scale in args.scales)
        or args.repeats < 1
    ):
        raise SystemExit("scales and repeats must be positive")

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_root = args.workspace.resolve() / run_id
    if args.reuse_core_report is not None:
        existing = json.loads(
            args.reuse_core_report.read_text(encoding="utf-8")
        )
        results = existing["results"]
        history_probe = existing["history_probe"]
        concurrency_probe = existing["concurrency_probe"]
    else:
        fixtures = run_root / "fixtures"
        fixtures.mkdir(parents=True, exist_ok=True)
        results = []
        for shape_value in args.shapes:
            shape: FixtureShape = shape_value
            for scale in args.scales:
                fixture = fixtures / f"{shape}-{scale}.json"
                fixture.write_text(
                    synthetic_payload(scale, shape=shape),
                    encoding="utf-8",
                )
                for repeat in range(1, args.repeats + 1):
                    result = _run(
                        _worker_command(
                            "--worker",
                            "case",
                            "--rows",
                            str(scale),
                            "--shape",
                            shape,
                            "--fixture",
                            str(fixture),
                            "--case-workspace",
                            str(
                                run_root
                                / "cases"
                                / shape
                                / str(scale)
                                / str(repeat)
                            ),
                            "--environment-label",
                            f"development-{shape}-repeat-{repeat}",
                        )
                    )
                    result["repeat"] = repeat
                    results.append(result)

        history_probe = _run(
            _worker_command(
                "--worker",
                "history",
                "--case-workspace",
                str(run_root / "history"),
                "--history-rows",
                str(args.history_rows),
                "--history-depth",
                str(args.history_depth),
                "--unrelated-connectors",
                str(args.unrelated_connectors),
            )
        )
        concurrency_probe = _run(
            _worker_command(
                "--worker",
                "concurrency",
                "--case-workspace",
                str(run_root / "concurrency"),
                "--concurrency-rows",
                str(args.concurrency_rows),
                "--concurrency-iterations",
                str(args.concurrency_iterations),
            )
        )
    packaged_results = None
    if args.packaged_results:
        packaged_results = {}
        for path in args.packaged_results:
            payload = json.loads(path.read_text(encoding="utf-8"))
            packaged_results[payload["mode"]] = payload
    report = summarize_report(
        results=results,
        history_probe=history_probe,
        concurrency_probe=concurrency_probe,
        packaged_results=packaged_results,
    )
    report.update(
        {
            "generated_at": datetime.now(UTC).isoformat(),
            "run_id": run_id,
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "python_executable": sys.executable,
                "frozen": bool(getattr(sys, "frozen", False)),
                "process_architecture": platform.machine(),
                "working_directory": os.getcwd(),
            },
            "workspace": str(run_root),
        }
    )
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    output.write_text(raw_text, encoding="utf-8")
    if args.summary_output is not None:
        packaged_summary = None
        if packaged_results is not None:
            packaged_summary = {
                mode: {
                    "mode": payload["mode"],
                    "row_count": payload["rowCount"],
                    "column_count": payload["columnCount"],
                    "source_characters": payload["sourceCharacters"],
                    "source_utf8_bytes": payload["sourceUtf8Bytes"],
                    "request_body_bytes": payload["requestBodyBytes"],
                    "launch_to_first_usable_ms": payload[
                        "launchToFirstUsableMs"
                    ],
                    "restart_to_first_usable_ms": payload.get(
                        "restartToFirstUsableMs"
                    ),
                    "database_increment_bytes": payload[
                        "databaseIncrementBytes"
                    ],
                    "process_tree_before_lifecycle": {
                        key: payload["processTreeBeforeLifecycle"][key]
                        for key in (
                            "processCount",
                            "workingSetBytes",
                            "summedPeakWorkingSetBytes",
                        )
                    },
                    "process_tree_after_lifecycle": {
                        key: payload["processTreeAfterLifecycle"][key]
                        for key in (
                            "processCount",
                            "workingSetBytes",
                            "summedPeakWorkingSetBytes",
                        )
                    },
                    "operations": {
                        operation: {
                            key: metrics[key]
                            for key in (
                                "status",
                                "headersMs",
                                "bodyReceivedMs",
                                "parsedMs",
                                "responseBytes",
                            )
                        }
                        for operation, metrics in payload["operations"].items()
                    },
                }
                for mode, payload in packaged_results.items()
            }
        concurrency_summary = (
            None
            if concurrency_probe is None
            else {
                key: value
                for key, value in concurrency_probe.items()
                if key != "operations"
            }
        )
        summary = {
            "schema_version": "data-lifecycle-benchmark-evidence/v1",
            "generated_at": report["generated_at"],
            "raw_report_sha256": hashlib.sha256(
                raw_text.encode("utf-8")
            ).hexdigest(),
            "environment": {
                key: report["environment"][key]
                for key in (
                    "platform",
                    "python",
                    "frozen",
                    "process_architecture",
                )
            },
            "thresholds_fixed_before_measurement": report[
                "thresholds_fixed_before_measurement"
            ],
            "decision_rule": report["decision_rule"],
            "aggregated_results": report["aggregated_results"],
            "history_probe": history_probe,
            "concurrency_probe": concurrency_summary,
            "packaged_results": packaged_summary,
            "million_row_feasibility": report[
                "million_row_feasibility"
            ],
            "trigger_evaluation": report["trigger_evaluation"],
            "recommended_decisions": report["recommended_decisions"],
        }
        summary_path = args.summary_output.resolve()
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
