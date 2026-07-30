from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from material_workbench.modeling.chain_evaluation_builder import (
    build_chain_evaluation,
)
from material_workbench.modeling.model_lifecycle import resolve_configured_package


ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build leakage-safe evaluation evidence for the welding A→B→C Chain"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT
        / "data"
        / "source"
        / "welding_consumable_multistage_synthetic_dataset.xlsx",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT
        / "models"
        / "evaluations"
        / "welding-consumable-a-b-c-v1.json",
    )
    args = parser.parse_args()
    report = build_chain_evaluation(
        source=args.source,
        stage_b_profile=ROOT
        / "backend"
        / "src"
        / "material_workbench"
        / "data"
        / "welding-stage-b-profile-v1.json",
        # Stage AはTaskを持たない決定論的transformのPackageなので、パスで指す。
        # Stage B・Cは使用中Packageから解決する。パスを直接書くと、切替のたびに
        # 評価成果物だけが古いPackageを指したままになる。
        stage_a_package=ROOT
        / "models"
        / "packages"
        / "welding-stage-a-deterministic-v1",
        stage_b_package=resolve_configured_package("welding-consumable-stage-b-v1"),
        stage_c_package=resolve_configured_package("welding-stage-c-properties-v1"),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        report.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
