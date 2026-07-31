from __future__ import annotations

from pathlib import Path

import pytest

from material_workbench.application.dataset_registration import register_managed_dataset
from material_workbench.data.profile_workbench import inspect_workbook, validate_workbook_profile
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "material_workbench_tutorial_v2.xlsx"
PROFILE = ROOT / "backend" / "src" / "material_workbench" / "data" / "dataset-input-profile-tutorial.json"


def test_inspect_workbook_reports_profile_and_canonical_counts() -> None:
    report = inspect_workbook(SOURCE, PROFILE)

    assert report["profile_error"] is None
    assert report["source_sha256"]
    assert len(report["sheets"]) >= 5
    assert report["canonicalization"]["profile_id"] == "thin-sheet-tutorial-v1"
    assert report["canonicalization"]["observations"] > 0
    assert "annealed-properties-v1" in report["canonicalization"]["task_ids"]
    assert isinstance(report["canonicalization"]["unresolved_heat_series_by_task"], dict)


def test_validate_reports_effective_profile_and_task_cohorts() -> None:
    report = validate_workbook_profile(SOURCE, PROFILE)
    assert report["registration_ready"] is True
    assert report["profile_digest"]
    assert report["observations_by_task"]["annealed-properties-v1"] > 0


def test_register_dataset_source_is_content_addressed_and_preserves_source(tmp_path: Path) -> None:
    source_before = SOURCE.read_bytes()
    database = tmp_path / "workspace.db"
    library = tmp_path / "library"

    first = register_managed_dataset(
        database=database,
        source=SOURCE,
        library_root=library,
        profile_path=PROFILE,
        name="検証用Dataset",
    )
    second = register_managed_dataset(
        database=database,
        source=SOURCE,
        library_root=library,
        profile_path=PROFILE,
        name="検証用Dataset",
    )

    assert first == second
    assert Path(first.locator).read_bytes() == source_before
    assert SOURCE.read_bytes() == source_before
    catalog = WorkspaceCatalog(database)
    assert len(catalog.list_data_assets()) == 1
    assert len(catalog.list_profile_revisions()) == 1
    assert len(catalog.list_dataset_revisions()) == 1
    assert len(catalog.list_dataset_view_revisions()) == 1


def test_register_dataset_rolls_back_after_copy_when_catalog_registration_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-copy failure leaves no managed file or partial Catalog identity."""

    database = tmp_path / "workspace.db"
    library = tmp_path / "library"
    original_view = WorkspaceCatalog.ensure_single_dataset_view

    def fail_after_creating_view(
        self: WorkspaceCatalog,
        *args: object,
        **kwargs: object,
    ) -> object:
        original_view(self, *args, **kwargs)
        raise RuntimeError("forced post-copy catalog failure")

    monkeypatch.setattr(
        WorkspaceCatalog,
        "ensure_single_dataset_view",
        fail_after_creating_view,
    )
    with pytest.raises(RuntimeError, match="forced post-copy catalog failure"):
        register_managed_dataset(
            database=database,
            source=SOURCE,
            library_root=library,
            profile_path=PROFILE,
        )

    catalog = WorkspaceCatalog(database)
    assert catalog.list_data_assets(include_archived=True) == []
    assert catalog.list_profile_revisions(include_archived=True) == []
    assert catalog.list_dataset_revisions(include_archived=True) == []
    assert catalog.list_dataset_view_revisions(include_archived=True) == []
    assert not list(library.rglob(f"*{SOURCE.suffix}"))

    monkeypatch.setattr(WorkspaceCatalog, "ensure_single_dataset_view", original_view)
    retried = register_managed_dataset(
        database=database,
        source=SOURCE,
        library_root=library,
        profile_path=PROFILE,
    )
    assert Path(retried.locator).is_file()


def test_register_dataset_resume_ignores_custom_single_views(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workspace.db"
    library = tmp_path / "library"
    first = register_managed_dataset(
        database=database,
        source=SOURCE,
        library_root=library,
        profile_path=PROFILE,
        name="正規Dataset View",
        member_provenance={"source": "canonical"},
    )
    catalog = WorkspaceCatalog(database)
    forged = catalog.ensure_single_dataset_view(
        first.dataset_revision_id,
        name="別用途のsingle view",
        view_id="forged-single-view",
        member_provenance={"source": "forged"},
    )

    resumed = register_managed_dataset(
        database=database,
        source=SOURCE,
        library_root=library,
        profile_path=PROFILE,
        name="再開時の表示名",
        member_provenance={"source": "different"},
    )

    assert resumed.dataset_view_revision_id == first.dataset_view_revision_id
    assert resumed.dataset_view_revision_id != forged.id
    canonical = catalog.get_dataset_view_revision(
        resumed.dataset_view_revision_id
    )
    assert canonical is not None
    assert canonical.view_id == f"single-{first.dataset_revision_id}"
    assert canonical.members[0].provenance_json == {"source": "canonical"}


def test_register_promotes_existing_bundled_asset_to_managed_without_orphan(tmp_path: Path) -> None:
    from material_workbench.data.file_integrity import file_sha256
    from material_workbench.contracts.data_library_contracts import DataAssetCreateInput

    database = tmp_path / "workspace.db"
    catalog = WorkspaceCatalog(database)
    existing = catalog.upsert_data_asset(DataAssetCreateInput(
        original_filename=SOURCE.name,
        sha256=file_sha256(SOURCE),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        locator_kind="bundled",
        locator=str(SOURCE),
    ))
    result = register_managed_dataset(
        database=database,
        source=SOURCE,
        library_root=tmp_path / "library",
        profile_path=PROFILE,
    )

    promoted = catalog.get_data_asset(existing.id)
    assert promoted is not None and promoted.locator_kind == "managed"
    assert promoted.locator == result.locator
    assert Path(result.locator).is_file()
