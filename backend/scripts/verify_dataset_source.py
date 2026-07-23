"""Preflight one Excel source and report the selected data-flow contract."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from material_workbench.data.dataset_profile import DatasetProfileError
from material_workbench.data.importer import load_workbook_data


def verify(source: Path, profile: Path | None = None) -> dict[str, object]:
    data = load_workbook_data(source, profile_path=profile)
    observations_by_task: dict[str, dict[str, int]] = {}
    for row in data.observations:
        bucket = observations_by_task.setdefault(row["task_id"], {"total": 0, "eligible": 0})
        bucket["total"] += 1
        bucket["eligible"] += int(row["eligible"])
    return {
        "ok": True,
        "source": str(source),
        "source_sha256": data.source_sha256,
        "profile": data.profile_path,
        "profile_id": data.profile_id,
        "sheets": {name: len(rows) for name, rows in data.sheets.items()},
        "relation_sheet": data.relation_sheet,
        "relation_rows": len(data.sheets[data.relation_sheet]),
        "observations": observations_by_task,
        "quality_rows": len(data.quality),
        "detected_quality_issues": len(data.detected_quality),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preflight a Material Decision Workbench Excel source.")
    parser.add_argument("source", type=Path)
    parser.add_argument("--profile", type=Path, default=None, help="Explicit profile; omit to auto-detect by sheet contract.")
    parser.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        report = verify(args.source, args.profile)
    except (DatasetProfileError, OSError, ValueError) as exc:
        report = {"ok": False, "source": str(args.source), "error": str(exc)}
        if args.json_output:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"Dataset source preflight: FAIL\n{exc}", file=sys.stderr)
        return 1
    if args.json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("Dataset source preflight: PASS")
        print(f"Profile: {report['profile_id']} ({report['profile']})")
        print(f"Source SHA-256: {report['source_sha256']}")
        print(f"Relation: {report['relation_sheet']} ({report['relation_rows']} rows)")
        print(f"Detected quality issues: {report['detected_quality_issues']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
