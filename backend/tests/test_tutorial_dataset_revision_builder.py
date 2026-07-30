from __future__ import annotations

from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "scripts" / "generators"))

import build_tutorial_dataset_revision as builder  # noqa: E402


def test_defaults_generate_outside_the_read_only_source_tree() -> None:
    assert builder.SOURCE_ROOT not in builder.DEFAULT_GENERATED_ROOT.resolve().parents


@pytest.mark.parametrize("protected_argument", ["workbook", "images"])
def test_builder_rejects_every_destination_inside_source_tree(
    tmp_path: Path,
    protected_argument: str,
) -> None:
    protected = builder.SOURCE_ROOT / "must-not-be-created"
    workbook = protected / "tutorial.xlsx" if protected_argument == "workbook" else tmp_path / "tutorial.xlsx"
    images = protected / "images" if protected_argument == "images" else tmp_path / "images"

    with pytest.raises(ValueError, match="read-only source of truth"):
        builder.build(tmp_path / "unused-source.xlsx", workbook, images)

    assert not protected.exists()
