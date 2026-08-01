from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = REPOSITORY_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from decision_workbench.modeling.training.readiness import (  # noqa: E402
    standard_estimator_catalog,
)


DEFAULT_OUTPUT = (
    REPOSITORY_ROOT
    / "docs"
    / "contracts"
    / "standard-estimator-readiness.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate or verify the bounded standard estimator catalog."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = (
        json.dumps(
            standard_estimator_catalog().model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    if args.check:
        if (
            not args.output.is_file()
            or args.output.read_text(encoding="utf-8") != expected
        ):
            print(
                f"Standard estimator readiness catalog is stale: {args.output}",
                file=sys.stderr,
            )
            return 1
        print(f"Standard estimator readiness catalog is current: {args.output}")
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
