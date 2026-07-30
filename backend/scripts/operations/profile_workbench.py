"""Validate or register a Dataset Profile for the Material Decision Workbench."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from material_workbench.data.dataset_profile import (
    DatasetProfileError,
    materialize_dataset_profile_document,
)
from material_workbench.data.dataset_registration import register_managed_dataset
from material_workbench.data.importer import detect_dataset_profile_path
from material_workbench.data.profile_workbench import inspect_workbook, validate_source_profile
from material_workbench.persistence.workspace_catalog import CatalogConflictError, CatalogReferenceError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dataset + Profile developer workbench.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="List sheets/headers and run Profile preflight.")
    inspect_parser.add_argument("source", type=Path)
    inspect_parser.add_argument("--profile", type=Path)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate the selected or auto-detected Profile and canonicalization.",
    )
    validate_parser.add_argument("source", type=Path)
    validate_parser.add_argument("--profile", type=Path)

    register_parser = subparsers.add_parser("register", help="Copy a validated Dataset into a workspace library.")
    register_parser.add_argument("source", type=Path)
    register_parser.add_argument("--profile", type=Path, required=True)
    register_parser.add_argument("--database", type=Path, required=True)
    register_parser.add_argument("--library", type=Path, required=True)
    register_parser.add_argument("--name")

    materialize_parser = subparsers.add_parser(
        "materialize",
        help="Resolve Profile inheritance into one standalone JSON document.",
    )
    materialize_parser.add_argument("profile", type=Path)
    materialize_parser.add_argument("output", type=Path)
    materialize_parser.add_argument("--replace", action="store_true")
    return parser


def _materialize_profile(profile: Path, output: Path, *, replace: bool) -> dict[str, object]:
    if output.exists() and not replace:
        raise FileExistsError(f"refusing to replace existing profile: {output}")
    document = materialize_dataset_profile_document(profile)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return {"profile": str(profile), "output": str(output)}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_workbook(args.source, args.profile)
        elif args.command == "validate":
            profile = args.profile or detect_dataset_profile_path(args.source)
            result = validate_source_profile(args.source, profile)
        elif args.command == "register":
            result = register_managed_dataset(
                database=args.database,
                source=args.source,
                library_root=args.library,
                profile_path=args.profile,
                name=args.name,
            ).as_dict()
        else:
            result = _materialize_profile(
                args.profile,
                args.output,
                replace=args.replace,
            )
    except (CatalogConflictError, CatalogReferenceError, DatasetProfileError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
