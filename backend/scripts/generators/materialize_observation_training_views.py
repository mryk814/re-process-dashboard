from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from material_workbench.data.observation_profile import (  # noqa: E402
    build_observation_training_dataset,
    load_observation_profile,
    materialize_observation_training_dataset,
)


DEFAULT_SOURCE = ROOT / "data" / "source" / "welding_consumable_multistage_synthetic_dataset.xlsx"
DEFAULT_PROFILE = (
    ROOT
    / "backend"
    / "src"
    / "material_workbench"
    / "data"
    / "observation-profile-welding-consumable-stage-c-v1.json"
)
DEFAULT_DESTINATION = ROOT / "data" / "derived" / "welding-consumable-stage-c"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Profile駆動で観測family別のStage C学習viewを生成する。",
    )
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()

    profile = load_observation_profile(args.profile)
    dataset = build_observation_training_dataset(args.source, profile)
    destination = materialize_observation_training_dataset(dataset, args.destination)
    print(f"wrote {destination}")
    for family, view in dataset.views.items():
        summary = view.summary
        print(
            f"{family}: source={summary.source_rows}, "
            f"usable={summary.usable_input_rows}, groups={summary.split_groups}"
        )


if __name__ == "__main__":
    main()
