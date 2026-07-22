"""Inspect or register a workbook Dataset for the Material Decision Workbench."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.dataset_profile import DatasetProfileError
from material_workbench.dataset_registration import register_managed_dataset
from material_workbench.profile_workbench import inspect_workbook, validate_workbook_profile
from material_workbench.workspace_catalog import CatalogConflictError, CatalogReferenceError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Excel + Profile Dataset developer workbench.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="List sheets/headers and run Profile preflight.")
    inspect_parser.add_argument("source", type=Path)
    inspect_parser.add_argument("--profile", type=Path)

    validate_parser = subparsers.add_parser("validate", help="Validate one explicit Profile and canonicalization.")
    validate_parser.add_argument("source", type=Path)
    validate_parser.add_argument("--profile", type=Path, required=True)

    register_parser = subparsers.add_parser("register", help="Copy a validated Dataset into a workspace library.")
    register_parser.add_argument("source", type=Path)
    register_parser.add_argument("--profile", type=Path, required=True)
    register_parser.add_argument("--database", type=Path, required=True)
    register_parser.add_argument("--library", type=Path, required=True)
    register_parser.add_argument("--name")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_workbook(args.source, args.profile)
        elif args.command == "validate":
            result = validate_workbook_profile(args.source, args.profile)
        else:
            result = register_managed_dataset(
                database=args.database,
                source=args.source,
                library_root=args.library,
                profile_path=args.profile,
                name=args.name,
            ).as_dict()
    except (CatalogConflictError, CatalogReferenceError, DatasetProfileError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
