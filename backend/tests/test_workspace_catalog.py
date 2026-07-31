from __future__ import annotations

from pathlib import Path

import pytest

from decision_workbench.contracts.data_library_contracts import (
    DataAssetCreateInput,
    DatasetRevisionCreateInput,
    DatasetViewMemberInput,
    DatasetViewRevisionCreateInput,
    ModelPackageRefCreateInput,
    ProfileRevisionCreateInput,
    ProjectSeriesCreateInput,
)
from decision_workbench.persistence.workspace_catalog import (
    CatalogConflictError,
    CatalogReferenceError,
    WorkspaceCatalog,
)


def _asset() -> DataAssetCreateInput:
    return DataAssetCreateInput(
        original_filename="source.xlsx",
        sha256="a" * 64,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        locator_kind="bundled",
        locator="data/source/source.xlsx",
    )


def _profile(*, revision: int = 1, digest: str = "profile-digest") -> ProfileRevisionCreateInput:
    return ProfileRevisionCreateInput(
        profile_id="steel-profile",
        revision=revision,
        name="鋼材Profile",
        profile_digest=digest,
        canonical_contract_digest="canonical-v1",
        effective_profile_json={"entities": {"condition": {"key": "condition_id"}}},
    )


def _dataset(catalog: WorkspaceCatalog):
    asset = catalog.upsert_data_asset(_asset())
    profile = catalog.upsert_profile_revision(_profile())
    return catalog.upsert_dataset_revision(
        DatasetRevisionCreateInput(
            data_asset_id=asset.id,
            profile_revision_id=profile.id,
            canonicalization_contract_digest="canonicalizer-v1",
        )
    )


def test_catalog_upserts_are_deterministic_across_databases(tmp_path: Path) -> None:
    first = WorkspaceCatalog(tmp_path / "first.db")
    second = WorkspaceCatalog(tmp_path / "second.db")

    first_dataset = _dataset(first)
    second_dataset = _dataset(second)
    first_view = first.ensure_single_dataset_view(first_dataset.id, name="設備A")
    second_view = second.ensure_single_dataset_view(second_dataset.id, name="設備A")
    package_payload = ModelPackageRefCreateInput(
        package_id="annealed-gp-v1",
        task_id="annealed-properties-v1",
        task_contract_digest="task-contract-v1",
        manifest_digest="manifest-v1",
        locator="models/packages/annealed-gp-v1",
        manifest_json={"schema_version": "model-package-v1", "targets": ["TS"]},
    )

    assert first.upsert_data_asset(_asset()).id == second.upsert_data_asset(_asset()).id
    assert first_dataset.id == second_dataset.id
    assert first_dataset.dataset_digest == second_dataset.dataset_digest
    assert first_view.id == second_view.id
    assert first_view.view_digest == second_view.view_digest
    assert first.upsert_model_package_ref(package_payload).id == second.upsert_model_package_ref(package_payload).id


def test_bundled_resources_rebind_after_portable_installation_moves(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog(tmp_path / "catalog.db")
    original_asset = _asset().model_copy(update={"locator": str(tmp_path / "old" / "source.xlsx")})
    moved_asset = original_asset.model_copy(update={"locator": str(tmp_path / "moved" / "source.xlsx")})

    asset = catalog.upsert_data_asset(original_asset)
    rebound_asset = catalog.upsert_data_asset(moved_asset)

    assert rebound_asset.id == asset.id
    assert rebound_asset.locator == moved_asset.locator

    original_package = ModelPackageRefCreateInput(
        package_id="annealed-gp-v1",
        task_id="annealed-properties-v1",
        task_contract_digest="task-contract-v1",
        manifest_digest="manifest-v1",
        locator=str(tmp_path / "old" / "models" / "annealed-gp-v1"),
        manifest_json={"schema_version": "model-package-v1", "targets": ["TS"]},
    )
    moved_package = original_package.model_copy(
        update={"locator": str(tmp_path / "moved" / "models" / "annealed-gp-v1")}
    )

    package = catalog.upsert_model_package_ref(original_package)
    rebound_package = catalog.upsert_model_package_ref(moved_package)

    assert rebound_package.id == package.id
    assert rebound_package.locator == moved_package.locator


def test_managed_dataset_locator_is_not_replaced_by_bundled_bootstrap(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog(tmp_path / "catalog.db")
    managed = _asset().model_copy(
        update={"locator_kind": "managed", "locator": str(tmp_path / "library" / "source.xlsx")}
    )
    bundled = _asset().model_copy(update={"locator": str(tmp_path / "resources" / "source.xlsx")})

    asset = catalog.upsert_data_asset(managed)
    after_bootstrap = catalog.upsert_data_asset(bundled)

    assert after_bootstrap.id == asset.id
    assert after_bootstrap.locator_kind == "managed"
    assert after_bootstrap.locator == managed.locator


def test_profile_logical_revision_rejects_different_immutable_content(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog(tmp_path / "catalog.db")
    catalog.upsert_profile_revision(_profile())

    with pytest.raises(CatalogConflictError, match="別内容"):
        catalog.upsert_profile_revision(_profile(digest="changed-profile-digest"))


def test_view_preserves_member_boundaries_and_rejects_missing_references(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog(tmp_path / "catalog.db")
    first = _dataset(catalog)
    second_asset = catalog.upsert_data_asset(
        _asset().model_copy(update={"sha256": "b" * 64, "original_filename": "equipment-b.xlsx"})
    )
    second = catalog.upsert_dataset_revision(
        DatasetRevisionCreateInput(
            data_asset_id=second_asset.id,
            profile_revision_id=catalog.list_profile_revisions()[0].id,
            canonicalization_contract_digest="canonicalizer-v1",
        )
    )
    created = catalog.upsert_dataset_view_revision(
        DatasetViewRevisionCreateInput(
            view_id="equipment-comparison",
            revision=1,
            name="設備比較",
            kind="cohort_comparison",
            members=[
                DatasetViewMemberInput(
                    dataset_revision_id=second.id,
                    ordinal=1,
                    cohort_key="equipment-b",
                    cohort_label="設備B",
                    provenance_json={"equipment": "B"},
                ),
                DatasetViewMemberInput(
                    dataset_revision_id=first.id,
                    ordinal=0,
                    cohort_key="equipment-a",
                    cohort_label="設備A",
                    provenance_json={"equipment": "A"},
                ),
            ],
        )
    )

    assert [member.cohort_key for member in created.members] == ["equipment-a", "equipment-b"]
    assert created.members[1].provenance_json == {"equipment": "B"}

    catalog.archive_dataset_revision(first.id)
    with pytest.raises(CatalogReferenceError, match="Dataset Revision"):
        catalog.ensure_single_dataset_view(first.id, name="archive済み")


def test_archive_changes_default_visibility_without_destroying_history(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog(tmp_path / "catalog.db")
    dataset = _dataset(catalog)

    archived = catalog.archive_dataset_revision(dataset.id)

    assert archived is not None and archived.archived_at is not None
    assert catalog.get_dataset_revision(dataset.id) is None
    assert catalog.list_dataset_revisions() == []
    assert catalog.get_dataset_revision(dataset.id, include_archived=True) == archived


def test_series_creation_does_not_deduplicate_display_text_and_bootstrap_id_is_stable(tmp_path: Path) -> None:
    catalog = WorkspaceCatalog(tmp_path / "catalog.db")
    payload = ProjectSeriesCreateInput(name="一連の検討", description="設備Aの再評価")

    first = catalog.create_project_series(payload)
    second = catalog.create_project_series(payload)
    seeded = catalog.ensure_project_series("bootstrap-series", payload)

    assert first.id != second.id
    assert catalog.ensure_project_series("bootstrap-series", payload).id == seeded.id
    with pytest.raises(CatalogConflictError, match="別内容"):
        catalog.ensure_project_series(
            "bootstrap-series", ProjectSeriesCreateInput(name="別の検討", description="")
        )
