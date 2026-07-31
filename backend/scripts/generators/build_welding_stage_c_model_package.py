from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from material_workbench.modeling.observation_model_builder import build
from material_workbench.task_composition.builtin.welding import observation_declaration


TASK_ID = "welding-stage-c-properties-v1"


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
    build(
        args.source,
        args.output,
        declaration=observation_declaration(TASK_ID),
        replace=args.replace,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
