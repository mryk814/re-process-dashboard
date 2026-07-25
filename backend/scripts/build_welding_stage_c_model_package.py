from __future__ import annotations

import argparse
from pathlib import Path

from material_workbench.modeling.stage_c_model_builder import build


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the allow-listed Stage C Model Package")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path("data/source/welding_consumable_multistage_synthetic_dataset.xlsx"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("models/packages/welding-stage-c-ridge-v1"),
    )
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    build(args.source, args.output, replace=args.replace)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
