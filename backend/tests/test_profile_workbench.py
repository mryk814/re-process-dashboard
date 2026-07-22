from __future__ import annotations

from pathlib import Path

from material_workbench.dataset_registration import register_managed_dataset
from material_workbench.profile_workbench import inspect_workbook, validate_workbook_profile
from material_workbench.workspace_catalog import WorkspaceCatalog


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data" / "source" / "process_dashboard_realistic_excel_v2.xlsx"
PROFILE = ROOT / "backend" / "src" / "material_workbench" / "dataset-input-profile-v1.json"


def test_inspect_workbook_reports_profile_and_canonical_counts() -> None:
    report = inspect_workbook(SOURCE, PROFILE)

    assert report["profile_error"] is None
    assert report["source_sha256"]
    assert len(report["sheets"]) >= 5
    assert report["canonicalization"]["profile_id"] == "thin-sheet-workbook-v2"
    assert report["canonicalization"]["observations"] > 0
    assert "annealed-properties-v1" in report["canonicalization"]["task_ids"]


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
