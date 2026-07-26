from __future__ import annotations

import json
import sqlite3
import zipfile
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest
from openpyxl import Workbook

import material_workbench.application.workspace_bundle as workspace_bundle_module
from material_workbench.application.workspace_bundle import (
    WorkspaceBundleError,
    _database_evidence,
    cancel_workspace_restore,
    commit_workspace_restore,
    create_workspace_backup,
    finalize_workspace_restore,
    prepare_workspace_restore,
)
from material_workbench.contracts.schemas import (
    DataAssetCreateInput,
    DatasetRevisionCreateInput,
    ModelPackageRefCreateInput,
    ProfileRevisionCreateInput,
)
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.tasks.task_registry import TaskRegistry


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _workspace_with_managed_asset(root: Path) -> tuple[Path, Path, str]:
    database = root / "workbench.db"
    library = root / "data-library"
    source = library / "managed" / "source.xlsx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"workspace asset bytes")
    Store(database)
    asset = WorkspaceCatalog(database).upsert_data_asset(
        DataAssetCreateInput(
            original_filename="source.xlsx",
            sha256=_digest(source),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            locator_kind="managed",
            locator=str(source),
        )
    )
    return database, library, asset.id


def _prepare(
    *,
    database: Path,
    data_library: Path,
    source: Path,
):
    return prepare_workspace_restore(
        database=database,
        data_library_root=data_library,
        source=source,
        # This fixture contains no Project, so no Task runtime is resolved.
        task_registry=cast(TaskRegistry, object()),
    )


def test_workspace_bundle_restores_to_an_empty_user_data_directory(
    tmp_path: Path,
) -> None:
    source_database, source_library, asset_id = _workspace_with_managed_asset(
        tmp_path / "source"
    )
    bundle = tmp_path / "workspace.mdwb"

    backup = create_workspace_backup(
        database=source_database,
        data_library_root=source_library,
        destination=bundle,
        app_version="test",
    )

    target_root = tmp_path / "target"
    target_database = target_root / "workbench.db"
    target_library = target_root / "data-library"
    prepared = _prepare(
        database=target_database,
        data_library=target_library,
        source=bundle,
    )
    commit_workspace_restore(
        database=target_database,
        data_library_root=target_library,
        restore_token=prepared.restore_token,
    )
    finalize_workspace_restore(
        database=target_database,
        restore_token=prepared.restore_token,
    )

    assert backup.manifest.model_package_strategy == "included"
    assert prepared.manifest.table_counts == backup.manifest.table_counts
    restored = WorkspaceCatalog(target_database).get_data_asset(
        asset_id,
        include_archived=True,
    )
    assert restored is not None
    assert restored.locator_kind == "managed"
    restored_file = Path(restored.locator)
    assert target_library in restored_file.parents
    assert restored_file.read_bytes() == b"workspace asset bytes"


def test_workspace_bundle_carries_model_package_bodies(
    tmp_path: Path,
) -> None:
    database, library, _ = _workspace_with_managed_asset(tmp_path / "source")
    package_root = (
        Path(__file__).resolve().parents[2]
        / "models"
        / "packages"
        / "annealed-gp-stable-ard-tutorial-v2"
    )
    package = ModelPackageLoader().load(package_root)
    reference = WorkspaceCatalog(database).upsert_model_package_ref(
        ModelPackageRefCreateInput(
            package_id=package.manifest.package_id,
            task_id=package.manifest.task_id,
            task_contract_digest="test-task-contract",
            manifest_digest=package.manifest_sha256,
            locator=str(package_root),
            manifest_json=package.manifest.model_dump(mode="json"),
        )
    )
    bundle = tmp_path / "workspace.mdwb"
    create_workspace_backup(
        database=database,
        data_library_root=library,
        destination=bundle,
        app_version="test",
    )

    target = tmp_path / "target"
    prepared = _prepare(
        database=target / "workbench.db",
        data_library=target / "data-library",
        source=bundle,
    )
    commit_workspace_restore(
        database=target / "workbench.db",
        data_library_root=target / "data-library",
        restore_token=prepared.restore_token,
    )
    restored = WorkspaceCatalog(target / "workbench.db").get_model_package_ref(
        reference.id,
        include_archived=True,
    )

    assert restored is not None
    restored_root = Path(restored.locator)
    assert target / "data-library" in restored_root.parents
    assert ModelPackageLoader().load(restored_root).manifest_sha256 == package.manifest_sha256


def test_workspace_bundle_preserves_profile_declared_relative_evidence_images(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    workbook_path = source_root / "dataset.xlsx"
    image_path = source_root / "images" / "micrograph.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"synthetic png evidence")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Microscopy"
    sheet.append(["Image"])
    sheet.append(["images/micrograph.png"])
    workbook.save(workbook_path)
    workbook.close()

    database = source_root / "workbench.db"
    library = source_root / "data-library"
    catalog = WorkspaceCatalog(database)
    asset = catalog.upsert_data_asset(
        DataAssetCreateInput(
            original_filename=workbook_path.name,
            sha256=_digest(workbook_path),
            media_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
            locator_kind="managed",
            locator=str(workbook_path),
        )
    )
    profile = catalog.upsert_profile_revision(
        ProfileRevisionCreateInput(
            profile_id="evidence-profile",
            revision=1,
            name="Evidence Profile",
            profile_digest="evidence-profile-digest",
            canonical_contract_digest="evidence-contract",
            effective_profile_json={
                "shared": {
                    "sheets": {"microstructure": "Microscopy"},
                    "technical": [
                        {
                            "role": "microstructure",
                            "name": "evidence_image",
                            "column": "Image",
                        }
                    ],
                }
            },
        )
    )
    catalog.upsert_dataset_revision(
        DatasetRevisionCreateInput(
            data_asset_id=asset.id,
            profile_revision_id=profile.id,
            canonicalization_contract_digest="evidence-canonicalization",
        )
    )
    bundle = tmp_path / "evidence.mdwb"
    create_workspace_backup(
        database=database,
        data_library_root=library,
        destination=bundle,
        app_version="test",
    )

    target = tmp_path / "target"
    prepared = _prepare(
        database=target / "workbench.db",
        data_library=target / "data-library",
        source=bundle,
    )
    commit_workspace_restore(
        database=target / "workbench.db",
        data_library_root=target / "data-library",
        restore_token=prepared.restore_token,
    )
    restored = WorkspaceCatalog(target / "workbench.db").get_data_asset(
        asset.id,
        include_archived=True,
    )
    assert restored is not None
    restored_image = Path(restored.locator).parent / "images" / "micrograph.png"
    assert restored_image.read_bytes() == b"synthetic png evidence"


def test_live_workspace_evidence_matches_after_restore_to_another_user_data(
    client,
    tmp_path: Path,
) -> None:
    project = client.get("/api/projects").json()[0]
    candidate = client.get(f"/api/projects/{project['id']}/candidates").json()[0]
    now = project["created_at"]
    database = Path(client.app.state.store.path)
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "INSERT INTO snapshots VALUES (?,?,?,?)",
            ("workspace-snapshot", candidate["id"], "{}", now),
        )
        connection.execute(
            "INSERT INTO decision_activity_runs VALUES (?,?,?,?,?,?,?,?)",
            (
                "workspace-activity",
                "workspace-activity-identity",
                project["id"],
                candidate["id"],
                "robustness",
                "1",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO chain_snapshot_records VALUES (?,?,?,?,?,?,?)",
            (
                "workspace-chain-snapshot",
                project["id"],
                candidate["id"],
                candidate["revision"],
                "{}",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO source_connectors VALUES (?,?,?,?)",
            ("workspace-connector", "connector-digest", "{}", now),
        )
        connection.execute(
            "INSERT INTO raw_source_snapshots VALUES (?,?,?,?,?,?,?)",
            (
                "workspace-raw",
                "workspace-connector",
                "content-digest",
                "selection-digest",
                "raw-snapshot-digest",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO curation_recipes VALUES (?,?,?,?,?,?)",
            (
                "workspace-recipe",
                "workspace-recipe",
                1,
                "recipe-digest",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO source_curation_runs VALUES (?,?,?,?,?,?,?)",
            (
                "workspace-curation",
                "workspace-raw",
                "workspace-recipe",
                "profile-digest",
                "curation-digest",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO canonical_dataset_approvals VALUES (?,?,?,?,?)",
            (
                "workspace-approval",
                "workspace-curation",
                "dataset-digest",
                "{}",
                now,
            ),
        )
        connection.execute(
            "INSERT INTO approved_training_snapshots VALUES (?,?,?,?,?)",
            (
                "workspace-training",
                "workspace-approval",
                "training-digest",
                "{}",
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    bundle = tmp_path / "live-workspace.mdwb"
    backup = create_workspace_backup(
        database=database,
        data_library_root=client.app.state.data_library_root,
        destination=bundle,
        app_version="test",
    )
    target = tmp_path / "other-user-data"
    prepared = prepare_workspace_restore(
        database=target / "workbench.db",
        data_library_root=target / "data-library",
        source=bundle,
        task_registry=client.app.state.task_registry,
        transform_catalog=client.app.state.deterministic_transform_catalog,
    )
    fixed_references = next(
        item
        for item in prepared.diagnostics
        if item.id == "restored-fixed-references"
    )
    assert fixed_references.status == "ok", fixed_references.detail
    commit_workspace_restore(
        database=target / "workbench.db",
        data_library_root=target / "data-library",
        restore_token=prepared.restore_token,
    )
    _, _, restored_evidence, _ = _database_evidence(
        target / "workbench.db",
        expected_tables=backup.manifest.table_evidence,
    )

    expected = {
        item.table: (item.row_count, item.digest)
        for item in backup.manifest.table_evidence
    }
    actual = {
        item.table: (item.row_count, item.digest)
        for item in restored_evidence
    }
    assert actual == expected
    for table in (
        "projects",
        "candidate_revisions",
        "snapshots",
        "decision_activity_runs",
        "chain_snapshot_records",
        "raw_source_snapshots",
        "source_curation_runs",
        "approved_training_snapshots",
    ):
        assert actual[table][0] > 0


def test_backup_uses_a_consistent_sqlite_snapshot_during_an_uncommitted_write(
    tmp_path: Path,
) -> None:
    database, library, _ = _workspace_with_managed_asset(tmp_path / "source")
    writer = sqlite3.connect(database, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "INSERT INTO project_series(id,name,description,created_at,updated_at) "
            "VALUES ('uncommitted','not visible','','2026-07-27T00:00:00Z',"
            "'2026-07-27T00:00:00Z')"
        )
        backup = create_workspace_backup(
            database=database,
            data_library_root=library,
            destination=tmp_path / "consistent.mdwb",
            app_version="test",
        )
    finally:
        writer.rollback()
        writer.close()

    series = next(
        evidence
        for evidence in backup.manifest.table_evidence
        if evidence.table == "project_series"
    )
    assert series.row_count == 0


def test_tampered_bundle_is_rejected_without_changing_current_workspace(
    tmp_path: Path,
) -> None:
    source_database, source_library, _ = _workspace_with_managed_asset(
        tmp_path / "source"
    )
    bundle = tmp_path / "workspace.mdwb"
    create_workspace_backup(
        database=source_database,
        data_library_root=source_library,
        destination=bundle,
        app_version="test",
    )
    tampered = tmp_path / "tampered.mdwb"
    with zipfile.ZipFile(bundle) as original, zipfile.ZipFile(
        tampered, "w", compression=zipfile.ZIP_DEFLATED
    ) as changed:
        for entry in original.infolist():
            payload = original.read(entry)
            if entry.filename == "workspace/workbench.db":
                payload += b"tampered"
            changed.writestr(entry, payload)

    current_database, current_library, _ = _workspace_with_managed_asset(
        tmp_path / "current"
    )
    before = _digest(current_database)
    with pytest.raises(WorkspaceBundleError, match="(size|digest) mismatch"):
        _prepare(
            database=current_database,
            data_library=current_library,
            source=tampered,
        )
    assert _digest(current_database) == before


def test_commit_switch_failure_preserves_database_and_data_library(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_database, source_library, _ = _workspace_with_managed_asset(
        tmp_path / "source"
    )
    bundle = tmp_path / "workspace.mdwb"
    create_workspace_backup(
        database=source_database,
        data_library_root=source_library,
        destination=bundle,
        app_version="test",
    )
    current_database, current_library, _ = _workspace_with_managed_asset(
        tmp_path / "current"
    )
    prepared = _prepare(
        database=current_database,
        data_library=current_library,
        source=bundle,
    )
    before_database = _digest(current_database)
    before_library = sorted(
        path.relative_to(current_library).as_posix()
        for path in current_library.rglob("*")
    )
    real_replace = workspace_bundle_module.os.replace

    def fail_database_switch(source: str | Path, destination: str | Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if (
            destination_path == current_database
            and ".workspace-restore" in source_path.parts
            and source_path.name == "workbench.db"
        ):
            raise OSError("injected database switch failure")
        real_replace(source, destination)

    monkeypatch.setattr(workspace_bundle_module.os, "replace", fail_database_switch)
    with pytest.raises(WorkspaceBundleError, match="current Workspace was preserved"):
        commit_workspace_restore(
            database=current_database,
            data_library_root=current_library,
            restore_token=prepared.restore_token,
        )

    assert _digest(current_database) == before_database
    assert sorted(
        path.relative_to(current_library).as_posix()
        for path in current_library.rglob("*")
    ) == before_library


def test_unknown_bundle_schema_is_rejected_with_an_explicit_reason(
    tmp_path: Path,
) -> None:
    source_database, source_library, _ = _workspace_with_managed_asset(
        tmp_path / "source"
    )
    bundle = tmp_path / "workspace.mdwb"
    create_workspace_backup(
        database=source_database,
        data_library_root=source_library,
        destination=bundle,
        app_version="test",
    )
    unsupported = tmp_path / "unsupported.mdwb"
    with zipfile.ZipFile(bundle) as original, zipfile.ZipFile(
        unsupported, "w", compression=zipfile.ZIP_DEFLATED
    ) as changed:
        for entry in original.infolist():
            payload = original.read(entry)
            if entry.filename == "manifest.json":
                manifest = json.loads(payload)
                manifest["schema_version"] = "workspace-bundle/v0"
                payload = json.dumps(manifest).encode()
            changed.writestr(entry, payload)

    with pytest.raises(WorkspaceBundleError, match="Unsupported or invalid"):
        _prepare(
            database=tmp_path / "target" / "workbench.db",
            data_library=tmp_path / "target" / "data-library",
            source=unsupported,
        )


def test_known_older_schema_is_migrated_in_staging(
    tmp_path: Path,
) -> None:
    database, library, _ = _workspace_with_managed_asset(tmp_path / "source")
    with sqlite3.connect(database) as connection:
        for table in (
            "approved_training_snapshots",
            "canonical_dataset_approvals",
            "source_curation_runs",
            "curation_recipes",
            "raw_source_snapshots",
            "source_fetch_attempts",
            "source_connectors",
        ):
            connection.execute(f"DROP TABLE {table}")
        connection.execute(
            "DELETE FROM schema_migrations WHERE id='source-data-lifecycle-v1'"
        )
    bundle = tmp_path / "old-schema.mdwb"
    backup = create_workspace_backup(
        database=database,
        data_library_root=library,
        destination=bundle,
        app_version="old-test",
    )
    assert "source-data-lifecycle-v1" not in {
        item.id for item in backup.manifest.schema_migrations
    }

    target = tmp_path / "target"
    prepared = _prepare(
        database=target / "workbench.db",
        data_library=target / "data-library",
        source=bundle,
    )
    staged = (
        target
        / ".workspace-restore"
        / prepared.restore_token
        / "next"
        / "workspace"
        / "workbench.db"
    )
    connection = sqlite3.connect(staged)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations "
            "WHERE id='source-data-lifecycle-v1'"
        ).fetchone()[0] == 1
    finally:
        connection.close()
    commit_workspace_restore(
        database=target / "workbench.db",
        data_library_root=target / "data-library",
        restore_token=prepared.restore_token,
    )
    finalize_workspace_restore(
        database=target / "workbench.db",
        restore_token=prepared.restore_token,
    )
    assert WorkspaceCatalog(target / "workbench.db").list_data_assets(
        include_archived=True
    )


def test_missing_cataloged_asset_blocks_backup(
    tmp_path: Path,
) -> None:
    database, library, asset_id = _workspace_with_managed_asset(tmp_path / "source")
    asset = WorkspaceCatalog(database).get_data_asset(asset_id, include_archived=True)
    assert asset is not None
    Path(asset.locator).unlink()

    with pytest.raises(WorkspaceBundleError, match="not a regular file"):
        create_workspace_backup(
            database=database,
            data_library_root=library,
            destination=tmp_path / "missing.mdwb",
            app_version="test",
        )


def test_restore_rejects_when_expanded_bundle_cannot_fit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database, library, _ = _workspace_with_managed_asset(tmp_path / "source")
    bundle = tmp_path / "workspace.mdwb"
    create_workspace_backup(
        database=database,
        data_library_root=library,
        destination=bundle,
        app_version="test",
    )
    usage_type = type(workspace_bundle_module.shutil.disk_usage(tmp_path))
    monkeypatch.setattr(
        workspace_bundle_module.shutil,
        "disk_usage",
        lambda _path: usage_type(total=1024, used=1024, free=0),
    )

    with pytest.raises(WorkspaceBundleError, match="空き容量"):
        _prepare(
            database=tmp_path / "target" / "workbench.db",
            data_library=tmp_path / "target" / "data-library",
            source=bundle,
        )


def test_restore_rejects_an_extreme_compression_ratio(
    tmp_path: Path,
) -> None:
    bomb = tmp_path / "compression-bomb.mdwb"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", "{}")
        bundle.writestr("workspace/payload.bin", bytes(1024 * 1024))

    with pytest.raises(WorkspaceBundleError, match="compression ratio"):
        _prepare(
            database=tmp_path / "target" / "workbench.db",
            data_library=tmp_path / "target" / "data-library",
            source=bomb,
        )


@pytest.mark.parametrize("unsafe_name", ["../outside", "C:/outside", "safe/../../outside"])
def test_unsafe_archive_paths_are_rejected_before_extraction(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    database, library, _ = _workspace_with_managed_asset(tmp_path / "source")
    bundle = tmp_path / "workspace.mdwb"
    create_workspace_backup(
        database=database,
        data_library_root=library,
        destination=bundle,
        app_version="test",
    )
    unsafe = tmp_path / "unsafe.mdwb"
    with zipfile.ZipFile(bundle) as original, zipfile.ZipFile(
        unsafe, "w", compression=zipfile.ZIP_DEFLATED
    ) as changed:
        for entry in original.infolist():
            changed.writestr(entry, original.read(entry))
        changed.writestr(unsafe_name, b"escape")

    with pytest.raises(WorkspaceBundleError, match="Unsafe Workspace bundle entry"):
        _prepare(
            database=tmp_path / "target" / "workbench.db",
            data_library=tmp_path / "target" / "data-library",
            source=unsafe,
        )
