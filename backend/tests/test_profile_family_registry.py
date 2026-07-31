from __future__ import annotations

import json
from pathlib import Path

import pytest

from material_workbench.application.dataset_registration import register_dataset_records
from material_workbench.data.file_integrity import file_sha256
from material_workbench.data.profile_family_registry import (
    ProfileFamilyUnavailableError,
    load_inspection_descriptor,
    load_profile_document,
    load_training_descriptor,
    profile_registration_metadata,
    restore_profile_document,
)
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "backend" / "src" / "material_workbench" / "data"
WELDING_SOURCE = ROOT / "data" / "source" / "welding_consumable_multistage_synthetic_dataset.xlsx"


@pytest.mark.parametrize(
    ("profile_name", "source_name", "task_id", "profile_id"),
    [
        (
            "dataset-input-profile-tutorial.json",
            "material_workbench_tutorial_v2.xlsx",
            "annealed-properties-v1",
            "thin-sheet-tutorial-v1",
        ),
        (
            "tabular-profile-heat-treatment-v1.json",
            "external/heat_treatment_tradeoff_samples.csv",
            "heat-treatment-tradeoff-v1",
            "external-heat-treatment-v1",
        ),
        (
            "observation-profile-welding-consumable-stage-c-v1.json",
            "welding_consumable_multistage_synthetic_dataset.xlsx",
            "welding-stage-c-properties-v1",
            "welding-consumable-stage-c-observations-v1",
        ),
        (
            "welding-stage-b-profile-v1.json",
            "welding_consumable_multistage_synthetic_dataset.xlsx",
            "welding-consumable-stage-b-v1",
            "welding-consumable-stage-b-v1",
        ),
    ],
)
def test_registry_loads_registers_and_selects_training_descriptor_for_each_family(
    tmp_path: Path,
    profile_name: str,
    source_name: str,
    task_id: str,
    profile_id: str,
) -> None:
    profile_path = DATA / profile_name
    source = ROOT / "data" / "source" / source_name
    profile = load_profile_document(profile_path)
    metadata = profile_registration_metadata(profile)

    assert metadata.profile_id == profile_id
    assert metadata.task_ids == (task_id,) or task_id in metadata.task_ids
    assert load_training_descriptor(source, profile, task_id).profile_id == profile_id

    result = register_dataset_records(
        catalog=WorkspaceCatalog(tmp_path / "workspace.db"),
        source_path=source,
        source_sha256=file_sha256(source),
        profile_path=profile_path,
        locator_kind="bundled",
        locator=source,
        name="registry smoke",
    )
    assert result.profile_id == metadata.profile_id
    assert result.task_ids == metadata.task_ids


def test_registry_restores_dataset_input_profile_without_changing_stored_payload() -> None:
    profile_path = DATA / "dataset-input-profile-tutorial.json"
    profile = load_profile_document(profile_path)
    metadata = profile_registration_metadata(profile)

    restored = restore_profile_document(metadata.effective_profile)

    assert profile_registration_metadata(restored) == metadata


@pytest.mark.parametrize("profile_name", [
    "observation-profile-welding-consumable-stage-c-v1.json",
    "welding-stage-b-profile-v1.json",
])
def test_registry_selects_the_two_supported_training_inspectors(profile_name: str) -> None:
    dataset = load_inspection_descriptor(WELDING_SOURCE, DATA / profile_name)

    assert dataset.profile_id
    assert dataset.views


def test_registry_rejects_unknown_schema_without_dataset_input_fallback(tmp_path: Path) -> None:
    profile = tmp_path / "unknown-profile.json"
    profile.write_text(json.dumps({"schema_version": "unknown/v1"}), encoding="utf-8")

    with pytest.raises(ProfileFamilyUnavailableError, match="未対応"):
        load_profile_document(profile)
