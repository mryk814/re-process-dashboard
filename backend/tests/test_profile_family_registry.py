from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    validate_profile_source,
)
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.model_lifecycle import dataset_profile_digest
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "backend" / "src" / "material_workbench" / "data"
WELDING_SOURCE = ROOT / "data" / "source" / "welding_consumable_multistage_synthetic_dataset.xlsx"

FAMILY_CASES: tuple[dict[str, Any], ...] = (
    {
        "profile_name": "dataset-input-profile-tutorial.json",
        "source_name": "material_workbench_tutorial_v2.xlsx",
        "profile_id": "thin-sheet-tutorial-v1",
        "task_ids": ("annealed-properties-v1", "hot-rolled-properties-v1"),
        "profile_digest": "sha256:5e52335ef97ace6e2bf2fc5342c7f470352fb2e41a82ee6a1b7da822d13ec5fb",
        "effective_profile_digest": "sha256:f2db3bf6f888d59f0f6978a34184c5485f5f5369361169c06a80686517e8e532",
        "profile_revision_id": "profile-revision-e132b89ea493ba649c67145d",
        "dataset_revision_id": "dataset-revision-8ea8455d15340b7caf057340",
        "dataset_view_revision_id": "dataset-view-revision-4dc565c00df7d72b3ecae506",
    },
    {
        "profile_name": "tabular-profile-heat-treatment-v1.json",
        "source_name": "external/heat_treatment_tradeoff_samples.csv",
        "profile_id": "external-heat-treatment-v1",
        "task_ids": ("heat-treatment-tradeoff-v1",),
        "profile_digest": "sha256:0d0a123ce03bc7a7e339b3e3e13b7d241d89dfa71fe518dcd9c65bb62d7a6424",
        "effective_profile_digest": "sha256:c1a6f589d0f374a88c2b9fc4a77c6bd0280efb73434714c5394c1f1c561c01ac",
        "profile_revision_id": "profile-revision-233521a8bcae40a8538e6284",
        "dataset_revision_id": "dataset-revision-8e55e6374c62853e46cf0e24",
        "dataset_view_revision_id": "dataset-view-revision-45065c068d1b37331200c004",
    },
    {
        "profile_name": "observation-profile-welding-consumable-stage-c-v1.json",
        "source_name": "welding_consumable_multistage_synthetic_dataset.xlsx",
        "profile_id": "welding-consumable-stage-c-observations-v1",
        "task_ids": ("welding-stage-c-properties-v1",),
        "profile_digest": "sha256:e58004fc974f4e2a1fe0f64be055f09eaa9fedf7e9efc26a809533b3a93e0145",
        "effective_profile_digest": "sha256:e58004fc974f4e2a1fe0f64be055f09eaa9fedf7e9efc26a809533b3a93e0145",
        "profile_revision_id": "profile-revision-0bd9a3681fdf18f0150c865c",
        "dataset_revision_id": "dataset-revision-15dd04609fb2fc1905fa3514",
        "dataset_view_revision_id": "dataset-view-revision-4715daab84a67fe4be49d7d4",
    },
    {
        "profile_name": "welding-stage-b-profile-v1.json",
        "source_name": "welding_consumable_multistage_synthetic_dataset.xlsx",
        "profile_id": "welding-consumable-stage-b-v1",
        "task_ids": ("welding-consumable-stage-b-v1",),
        "profile_digest": "sha256:a0d1e21b600a1b6172176c89e7fe9d77c09f6f16edba8a44ba41fb2a97fc7822",
        "effective_profile_digest": "sha256:a0d1e21b600a1b6172176c89e7fe9d77c09f6f16edba8a44ba41fb2a97fc7822",
        "profile_revision_id": "profile-revision-ccea95d9938031e303eb0218",
        "dataset_revision_id": "dataset-revision-1d4367678c21ad508bfb2c58",
        "dataset_view_revision_id": "dataset-view-revision-85c2e297f8df39cd8797276b",
    },
)


@pytest.mark.parametrize("case", FAMILY_CASES, ids=lambda case: case["profile_id"])
def test_registry_loads_registers_and_selects_training_descriptor_for_each_family(
    tmp_path: Path,
    case: dict[str, Any],
) -> None:
    profile_path = DATA / case["profile_name"]
    source = ROOT / "data" / "source" / case["source_name"]
    profile = load_profile_document(profile_path)
    metadata = profile_registration_metadata(profile)

    assert metadata.profile_id == case["profile_id"]
    assert metadata.task_ids == case["task_ids"]
    assert dataset_profile_digest(profile_path) == case["profile_digest"]
    assert semantic_digest(metadata.effective_profile) == case["effective_profile_digest"]
    for task_id in metadata.task_ids:
        assert load_training_descriptor(source, profile, task_id).profile_id == case["profile_id"]

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
    assert result.profile_revision_id == case["profile_revision_id"]
    assert result.dataset_revision_id == case["dataset_revision_id"]
    assert result.dataset_view_revision_id == case["dataset_view_revision_id"]


@pytest.mark.parametrize("case", FAMILY_CASES, ids=lambda case: case["profile_id"])
def test_registry_validates_each_profile_family(case: dict[str, Any]) -> None:
    report = validate_profile_source(
        ROOT / "data" / "source" / case["source_name"],
        DATA / case["profile_name"],
    )

    assert report["ok"] is True
    assert report["registration_ready"] is True
    assert report["profile_id"] == case["profile_id"]
    assert tuple(report["task_ids"]) == case["task_ids"]


def test_registry_restores_dataset_input_profile_without_changing_stored_payload() -> None:
    profile_path = DATA / "dataset-input-profile-tutorial.json"
    profile = load_profile_document(profile_path)
    metadata = profile_registration_metadata(profile)

    restored = restore_profile_document(metadata.effective_profile)

    assert "schema_version" not in metadata.effective_profile
    assert profile_registration_metadata(restored) == metadata
    assert dataset_profile_digest(restored) == dataset_profile_digest(profile)


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

    with pytest.raises(ProfileFamilyUnavailableError, match="未対応"):
        dataset_profile_digest(profile)


def test_registry_rejects_unknown_stored_schema_without_legacy_fallback() -> None:
    with pytest.raises(ProfileFamilyUnavailableError, match="未対応"):
        restore_profile_document({"schema_version": "unknown/v1"})
