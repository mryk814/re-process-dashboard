"""Inspect source columns and create a data-only external Task scaffold."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from material_workbench.developer_experience.task_scaffolding import (  # noqa: E402
    ScaffoldField,
    create_task_scaffold,
    inspect_task_source,
)


def _field(raw: str, *, output: bool) -> ScaffoldField:
    parts = raw.split(":")
    expected = 5 if output else 5
    if len(parts) != expected:
        shape = (
            "column:key:label:unit:goal"
            if output
            else "column:role:key:label:unit"
        )
        raise argparse.ArgumentTypeError(f"expected {shape}")
    if output:
        column, key, label, unit, goal = parts
        return ScaffoldField(
            column=column,
            role="output",
            key=key,
            label=label,
            unit=unit,
            goal_direction=goal,  # type: ignore[arg-type]
        )
    column, role, key, label, unit = parts
    return ScaffoldField(
        column=column,
        role=role,  # type: ignore[arg-type]
        key=key,
        label=label,
        unit=None if role == "categorical" else unit,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create external data-only Task scaffolds without arbitrary code."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("source", type=Path)
    inspect_parser.add_argument("--sheet")

    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("source", type=Path)
    create_parser.add_argument("--task-id", required=True)
    create_parser.add_argument("--label", required=True)
    create_parser.add_argument("--sheet")
    create_parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="column:role:key:label:unit (role=composition|process|categorical)",
    )
    create_parser.add_argument(
        "--output",
        action="append",
        default=[],
        help="column:key:label:unit:goal (goal=at_least|at_most|target)",
    )
    create_parser.add_argument(
        "--estimator",
        choices=("ridge.v1", "lightgbm-regression.v1"),
        default="ridge.v1",
    )
    create_parser.add_argument("--store", type=Path)
    args = parser.parse_args(argv)

    if args.command == "inspect":
        report = inspect_task_source(args.source, sheet=args.sheet)
        print(json.dumps({
            "source": str(report.source),
            "source_sha256": report.source_sha256,
            "selected_sheet": report.selected_sheet,
            "row_count": report.row_count,
            "columns": [asdict(column) for column in report.columns],
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        fields = [
            *(_field(item, output=False) for item in args.input),
            *(_field(item, output=True) for item in args.output),
        ]
        result = create_task_scaffold(
            source=args.source,
            task_id=args.task_id,
            label=args.label,
            fields=fields,
            estimator_id=args.estimator,
            sheet=args.sheet,
            store=args.store,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "task_id": result.task_id,
        "state": result.state,
        "root": str(result.root),
        "source": str(result.source_path),
        "profile": str(result.profile_path) if result.profile_path else None,
        "task_definition": (
            str(result.task_definition_path)
            if result.task_definition_path
            else None
        ),
        "training_recipe": (
            str(result.training_recipe_path)
            if result.training_recipe_path
            else None
        ),
        "unresolved": list(result.unresolved),
        "next": (
            f'npm run model:build -- --task {result.task_id} '
            f'--source "{result.source_path}" '
            f'--profile "{result.profile_path}" '
            f'--package-id {result.task_id}-personal-1 --package-version 1.0.0'
            if result.state == "ready"
            else "未解決項目を明示してscaffoldを作り直してください。"
        ),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
