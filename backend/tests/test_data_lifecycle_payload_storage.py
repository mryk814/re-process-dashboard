from __future__ import annotations

import json
import hashlib
import multiprocessing
import os
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import cast

import pytest

from decision_workbench.application.data_lifecycle import DataLifecycleService
from decision_workbench.application.workspace_bundle import (
    WorkspaceBundleError,
    commit_workspace_restore,
    create_workspace_backup,
    prepare_workspace_restore,
    recover_incomplete_workspace_restores,
)
from decision_workbench.application.workspace_bundle.manifest import (
    _database_evidence,
)
from decision_workbench.contracts.data_lifecycle_contracts import (
    CurationRecipeCreateInput,
    CurationRunCreateInput,
    ObjectSelection,
    SourceConnectorCreateInput,
    SourceFetchRequest,
)
from decision_workbench.persistence.data_lifecycle_payload_storage import (
    LifecyclePayloadUnavailableError,
)
from decision_workbench.persistence.row_payload_store import (
    RowPayloadError,
    RowPayloadReference,
    RowPayloadStore,
)
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import TaskRegistry


def _connector(name: str, locator: str) -> SourceConnectorCreateInput:
    return SourceConnectorCreateInput(
        name=name,
        connector_type="object_storage_json_v1",
        source_locator=locator,
        selection=ObjectSelection(format="json_array", primary_key="id"),
    )


def _recipe() -> CurationRecipeCreateInput:
    return CurationRecipeCreateInput(
        recipe_id="cas-quality",
        version=1,
        name="CAS品質判定",
        steps=(
            {"kind": "coerce_number_v1", "fields": ["x", "target"]},
            {"kind": "required_fields_v1", "fields": ["id", "x"]},
            {"kind": "target_eligibility_v1", "fields": ["target"]},
        ),
    )


def test_row_payload_store_is_canonical_deduplicated_and_verified(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    store = RowPayloadStore(database)
    rows = (
        {"日本語": "値", "b": 2, "a": 1},
        {"a": 3, "b": None, "日本語": "別"},
    )

    first = store.write(rows, record_kind="raw-json-record/v1")
    second = store.write(
        (
            {"a": 1, "b": 2, "日本語": "値"},
            {"日本語": "別", "b": None, "a": 3},
        ),
        record_kind="raw-json-record/v1",
    )

    assert first == second
    assert store.read(first) == rows
    assert len(tuple(store.root.rglob("*.jsonl"))) == 1
    assert store.path_for(first).read_bytes().endswith(b"\n")
    with pytest.raises(RowPayloadError, match="canonical JSON"):
        store.write(({"x": float("nan")},), record_kind="raw-json-record/v1")

    store.path_for(first).write_bytes(b'{"a":1}\n')
    with pytest.raises(RowPayloadError, match="size|digest"):
        store.read(first)


def test_repository_stores_rows_only_in_content_addressed_files(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector("CAS source", "fixture://cas"))
    recipe = service.create_recipe(_recipe())
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2},{"id":"B","x":3}]',
            object_version="v1",
        ),
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        raw_row = connection.execute(
            "SELECT payload,row_payload_sha256,row_count "
            "FROM raw_source_snapshots WHERE id=?",
            (raw.id,),
        ).fetchone()
        run_row = connection.execute(
            "SELECT payload,row_payload_sha256,row_count,quality_payload "
            "FROM source_curation_runs WHERE id=?",
            (run.id,),
        ).fetchone()

    assert raw_row is not None and run_row is not None
    assert "rows" not in json.loads(raw_row["payload"])["resource"]
    assert "rows" not in json.loads(run_row["payload"])["resource"]
    assert raw_row["row_payload_sha256"]
    assert run_row["row_payload_sha256"]
    assert raw_row["row_count"] == raw.row_count
    assert run_row["row_count"] == len(run.rows)
    assert json.loads(run_row["quality_payload"]) == run.quality.model_dump(
        mode="json"
    )
    assert service.repository.get_raw_snapshot(raw.id) == raw
    assert service.repository.get_curation_run(run.id) == run


def test_legacy_inline_rows_migrate_without_changing_public_identity(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector("Legacy source", "fixture://legacy"))
    recipe = service.create_recipe(_recipe())
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="legacy",
        ),
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DROP TRIGGER guard_raw_source_snapshots_update_row_payload"
        )
        connection.execute(
            "DROP TRIGGER guard_source_curation_runs_update_row_payload"
        )
        connection.execute(
            "UPDATE raw_source_snapshots SET payload=?,row_payload_sha256=NULL,"
            "row_payload_bytes=NULL,row_count=NULL WHERE id=?",
            (raw.model_dump_json(), raw.id),
        )
        connection.execute(
            "UPDATE source_curation_runs SET payload=?,row_payload_sha256=NULL,"
            "row_payload_bytes=NULL,row_count=NULL,quality_payload=NULL WHERE id=?",
            (run.model_dump_json(), run.id),
        )
        connection.execute(
            "DELETE FROM schema_migrations "
            "WHERE id='source-data-lifecycle-row-payload-v2'"
        )
    shutil.rmtree(database.parent / "row-payloads")

    Store(database)
    migrated = DataLifecycleService(database)

    assert migrated.repository.get_raw_snapshot(raw.id) == raw
    assert migrated.repository.get_curation_run(run.id) == run
    with sqlite3.connect(database) as connection:
        raw_stored = connection.execute(
            "SELECT payload FROM raw_source_snapshots WHERE id=?",
            (raw.id,),
        ).fetchone()[0]
        run_stored = connection.execute(
            "SELECT payload FROM source_curation_runs WHERE id=?",
            (run.id,),
        ).fetchone()[0]
    assert "rows" not in json.loads(raw_stored)["resource"]
    assert "rows" not in json.loads(run_stored)["resource"]


def test_invalid_legacy_inline_payload_is_durably_quarantined(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(
        _connector("Legacy invalid", "fixture://legacy-invalid")
    )
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="legacy-invalid",
        ),
    )
    original = '{"id":"raw-broken","rows":[{"id":"A"}],"unknown":'
    with sqlite3.connect(database) as connection:
        connection.execute(
            "DROP TRIGGER guard_raw_source_snapshots_update_row_payload"
        )
        connection.execute(
            "UPDATE raw_source_snapshots SET payload=?,row_payload_sha256=NULL,"
            "row_payload_bytes=NULL,row_count=NULL WHERE id=?",
            (original, raw.id),
        )
        connection.execute(
            "DELETE FROM schema_migrations "
            "WHERE id='source-data-lifecycle-row-payload-v2'"
        )

    Store(database)

    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    quarantine = (
        database.parent
        / "row-payloads"
        / "quarantine"
        / "raw_source_snapshot"
        / f"{digest}.json"
    )
    assert quarantine.read_text(encoding="utf-8") == original
    with sqlite3.connect(database) as connection:
        stored, indexed_digest = connection.execute(
            "SELECT payload,row_payload_sha256 FROM raw_source_snapshots WHERE id=?",
            (raw.id,),
        ).fetchone()
        finding = connection.execute(
            "SELECT reason FROM data_lifecycle_payload_findings "
            "WHERE resource_kind='raw_source_snapshot' AND resource_id=?",
            (raw.id,),
        ).fetchone()[0]
    assert json.loads(stored)["unavailable_reason"]
    assert indexed_digest is None
    assert digest in finding
    assert quarantine.relative_to(database.parent).as_posix() in finding
    bundle = tmp_path / "quarantined.mdwb"
    backup = create_workspace_backup(
        database=database,
        data_library_root=database.parent / "data-library",
        destination=bundle,
        app_version="test",
    )
    assert any(
        record.path.endswith(f"/{digest}.json")
        for record in backup.manifest.row_payload_files
    )
    target_database = tmp_path / "restored" / "workbench.db"
    prepared = prepare_workspace_restore(
        database=target_database,
        data_library_root=target_database.parent / "data-library",
        source=bundle,
        task_registry=cast(TaskRegistry, object()),
    )
    commit_workspace_restore(
        database=target_database,
        data_library_root=target_database.parent / "data-library",
        restore_token=prepared.restore_token,
    )
    restored_quarantine = (
        target_database.parent
        / "row-payloads"
        / "quarantine"
        / "raw_source_snapshot"
        / f"{digest}.json"
    )
    assert restored_quarantine.read_text(encoding="utf-8") == original
    quarantine.unlink()
    with pytest.raises(WorkspaceBundleError, match="quarantine"):
        create_workspace_backup(
            database=database,
            data_library_root=database.parent / "data-library",
            destination=tmp_path / "missing-quarantine.mdwb",
            app_version="test",
        )


def test_schema_guard_rejects_inline_or_mismatched_references(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(_connector("Guard", "fixture://guard"))
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="guard",
        ),
    )
    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="invalid lifecycle"):
            connection.execute(
                "UPDATE raw_source_snapshots SET row_count=row_count+1 WHERE id=?",
                (raw.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="invalid lifecycle"):
            connection.execute(
                "UPDATE raw_source_snapshots SET payload=? WHERE id=?",
                (raw.model_dump_json(), raw.id),
            )
        with pytest.raises(sqlite3.IntegrityError, match="invalid lifecycle"):
            connection.execute(
                "UPDATE raw_source_snapshots SET payload='{}' WHERE id=?",
                (raw.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="invalid lifecycle"):
            connection.execute(
                "UPDATE raw_source_snapshots SET "
                "payload=json_set(payload,'$.row_payload.sha256','x'),"
                "row_payload_sha256='x' WHERE id=?",
                (raw.id,),
            )


def test_tampered_payload_is_scoped_to_its_connector(tmp_path) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    first_connector = service.create_connector(
        _connector("Corruptible", "fixture://first")
    )
    second_connector = service.create_connector(
        _connector("Independent", "fixture://second")
    )
    first, _ = service.fetch(
        first_connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="first",
        ),
    )
    second, _ = service.fetch(
        second_connector.id,
        SourceFetchRequest(
            object_content='[{"id":"B","x":3,"target":4}]',
            object_version="second",
        ),
    )
    with sqlite3.connect(database) as connection:
        digest = connection.execute(
            "SELECT row_payload_sha256 FROM raw_source_snapshots WHERE id=?",
            (first.id,),
        ).fetchone()[0]
    row_store = RowPayloadStore(database)
    with sqlite3.connect(database) as connection:
        reference_payload = json.loads(
            connection.execute(
            "SELECT payload FROM raw_source_snapshots WHERE id=?",
            (first.id,),
            ).fetchone()[0]
        )["row_payload"]
    reference = RowPayloadReference.model_validate(reference_payload)
    assert reference.sha256 == digest
    row_store.path_for(reference).write_bytes(b'{"tampered":true}\n')

    assert service.detail(first_connector.id).raw_snapshots[0].id == first.id
    with pytest.raises(LifecyclePayloadUnavailableError):
        service.raw_row_page(first.id, offset=0, limit=50)
    assert tuple(
        item.id for item in service.detail(second_connector.id).raw_snapshots
    ) == (second.id,)
    Store(database)
    with sqlite3.connect(database) as connection:
        finding = connection.execute(
            "SELECT resource_id FROM data_lifecycle_payload_findings"
        ).fetchone()
    assert finding == (first.id,)


def test_workspace_bundle_includes_and_verifies_row_payload_files(tmp_path) -> None:
    database = tmp_path / "source" / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(
        _connector("Bundle source", "fixture://bundle")
    )
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="bundle",
        ),
    )
    bundle = tmp_path / "workspace.mdwb"
    backup = create_workspace_backup(
        database=database,
        data_library_root=database.parent / "data-library",
        destination=bundle,
        app_version="test",
    )
    assert len(backup.manifest.row_payload_files) == 1

    tampered_bundle = tmp_path / "tampered.mdwb"
    target_entry = backup.manifest.row_payload_files[0].path
    with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(
        tampered_bundle,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as destination:
        for entry in source.infolist():
            content = source.read(entry)
            if entry.filename == target_entry:
                content = b'{"tampered":true}\n'
            destination.writestr(entry, content)
    target_database = tmp_path / "target" / "workbench.db"
    with pytest.raises(WorkspaceBundleError, match="digest|size"):
        prepare_workspace_restore(
            database=target_database,
            data_library_root=target_database.parent / "data-library",
            source=tampered_bundle,
            task_registry=cast(TaskRegistry, object()),
        )
    assert not target_database.exists()

    with sqlite3.connect(database) as connection:
        digest = connection.execute(
            "SELECT row_payload_sha256 FROM raw_source_snapshots WHERE id=?",
            (raw.id,),
        ).fetchone()[0]
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM raw_source_snapshots WHERE id=?",
                (raw.id,),
            ).fetchone()[0]
        )
    reference = RowPayloadReference.model_validate(payload["row_payload"])
    assert reference.sha256 == digest
    RowPayloadStore(database).path_for(reference).unlink()
    with pytest.raises(WorkspaceBundleError, match="cannot be backed up"):
        create_workspace_backup(
            database=database,
            data_library_root=database.parent / "data-library",
            destination=tmp_path / "missing.mdwb",
            app_version="test",
        )


def test_legacy_inline_v1_bundle_migrates_with_semantic_identity(tmp_path) -> None:
    database = tmp_path / "source" / "workbench.db"
    Store(database)
    service = DataLifecycleService(database)
    connector = service.create_connector(
        _connector("Legacy bundle", "fixture://legacy-bundle")
    )
    recipe = service.create_recipe(_recipe())
    raw, _ = service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="legacy-bundle",
        ),
    )
    run = service.curate(
        raw.id,
        CurationRunCreateInput(
            recipe_resource_id=recipe.id,
            profile_revision_id="profile@1",
            profile_digest="sha256:profile",
        ),
    )
    broken_connector = service.create_connector(
        _connector("Broken legacy bundle", "fixture://legacy-broken")
    )
    broken_raw, _ = service.fetch(
        broken_connector.id,
        SourceFetchRequest(
            object_content='[{"id":"B","x":3,"target":4}]',
            object_version="legacy-broken",
        ),
    )
    broken_inline = '{"id":"legacy-broken","rows":['
    current_bundle = tmp_path / "current.mdwb"
    create_workspace_backup(
        database=database,
        data_library_root=database.parent / "data-library",
        destination=current_bundle,
        app_version="test",
    )

    legacy_database = tmp_path / "legacy-workbench.db"
    with zipfile.ZipFile(current_bundle) as bundle:
        legacy_database.write_bytes(bundle.read("workspace/workbench.db"))
        manifest = json.loads(bundle.read("manifest.json"))
        retained_entries = {
            entry.filename: bundle.read(entry)
            for entry in bundle.infolist()
            if not entry.filename.startswith("workspace/row-payloads/")
            and entry.filename
            not in {"workspace/workbench.db", "manifest.json"}
        }
    with sqlite3.connect(legacy_database) as connection:
        connection.execute(
            "DROP TRIGGER guard_raw_source_snapshots_update_row_payload"
        )
        connection.execute(
            "DROP TRIGGER guard_source_curation_runs_update_row_payload"
        )
        connection.execute(
            "UPDATE raw_source_snapshots SET payload=?,row_payload_sha256=NULL,"
            "row_payload_bytes=NULL,row_count=NULL WHERE id=?",
            (raw.model_dump_json(), raw.id),
        )
        connection.execute(
            "UPDATE source_curation_runs SET payload=?,row_payload_sha256=NULL,"
            "row_payload_bytes=NULL,row_count=NULL,quality_payload=NULL WHERE id=?",
            (run.model_dump_json(), run.id),
        )
        connection.execute(
            "UPDATE raw_source_snapshots SET payload=?,row_payload_sha256=NULL,"
            "row_payload_bytes=NULL,row_count=NULL WHERE id=?",
            (broken_inline, broken_raw.id),
        )
        connection.execute(
            "DELETE FROM schema_migrations "
            "WHERE id='source-data-lifecycle-row-payload-v2'"
        )
    migrations, packages, evidence, diagnostics = _database_evidence(
        legacy_database
    )
    manifest["schema_version"] = "workspace-bundle/v1"
    manifest.pop("row_payload_files", None)
    manifest["database"] = {
        "path": "workspace/workbench.db",
        "sha256": hashlib.sha256(legacy_database.read_bytes()).hexdigest(),
        "size_bytes": legacy_database.stat().st_size,
    }
    manifest["schema_migrations"] = [
        item.model_dump(mode="json") for item in migrations
    ]
    manifest["model_package_references"] = [
        item.model_dump(mode="json") for item in packages
    ]
    manifest["table_evidence"] = [
        item.model_dump(mode="json") for item in evidence
    ]
    manifest["table_counts"] = {
        item.table: item.row_count for item in evidence
    }
    manifest["diagnostics"] = [
        item.model_dump(mode="json") for item in diagnostics
    ]
    legacy_bundle = tmp_path / "legacy-v1.mdwb"
    with zipfile.ZipFile(
        legacy_bundle, "w", compression=zipfile.ZIP_DEFLATED
    ) as bundle:
        bundle.writestr("workspace/workbench.db", legacy_database.read_bytes())
        bundle.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        )
        for name, content in retained_entries.items():
            bundle.writestr(name, content)

    target_database = tmp_path / "target" / "workbench.db"
    prepared = prepare_workspace_restore(
        database=target_database,
        data_library_root=target_database.parent / "data-library",
        source=legacy_bundle,
        task_registry=cast(TaskRegistry, object()),
    )
    commit_workspace_restore(
        database=target_database,
        data_library_root=target_database.parent / "data-library",
        restore_token=prepared.restore_token,
    )
    restored = DataLifecycleService(target_database)
    assert restored.repository.get_raw_snapshot(raw.id) == raw
    assert restored.repository.get_curation_run(run.id) == run
    assert restored.detail(broken_connector.id).raw_snapshots[0].id == broken_raw.id
    with pytest.raises(LifecyclePayloadUnavailableError):
        restored.raw_row_page(broken_raw.id, offset=0, limit=50)
    broken_digest = hashlib.sha256(broken_inline.encode("utf-8")).hexdigest()
    assert (
        target_database.parent
        / "row-payloads"
        / "quarantine"
        / "raw_source_snapshot"
        / f"{broken_digest}.json"
    ).read_text(encoding="utf-8") == broken_inline


def test_interrupted_restore_removes_newly_installed_row_payloads(tmp_path) -> None:
    source_database = tmp_path / "source" / "workbench.db"
    Store(source_database)
    service = DataLifecycleService(source_database)
    connector = service.create_connector(
        _connector("Interrupted bundle", "fixture://interrupted")
    )
    service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="interrupted",
        ),
    )
    bundle = tmp_path / "interrupted.mdwb"
    create_workspace_backup(
        database=source_database,
        data_library_root=source_database.parent / "data-library",
        destination=bundle,
        app_version="test",
    )
    target_database = tmp_path / "target" / "workbench.db"
    prepared = prepare_workspace_restore(
        database=target_database,
        data_library_root=target_database.parent / "data-library",
        source=bundle,
        task_registry=cast(TaskRegistry, object()),
    )

    def interrupt(point: str) -> None:
        if point == "after_row_payload_installed":
            raise RuntimeError("simulated install interruption")

    with pytest.raises(RuntimeError, match="simulated install"):
        commit_workspace_restore(
            database=target_database,
            data_library_root=target_database.parent / "data-library",
            restore_token=prepared.restore_token,
            _fault_injector=interrupt,
        )
    payload_root = target_database.parent / "row-payloads"
    assert not any(payload_root.rglob("*.jsonl"))
    assert not target_database.exists()


def _crash_after_row_payload_install(
    database: str,
    data_library_root: str,
    restore_token: str,
) -> None:
    def stop(point: str) -> None:
        if point == "after_row_payload_installed":
            os._exit(97)

    commit_workspace_restore(
        database=Path(database),
        data_library_root=Path(data_library_root),
        restore_token=restore_token,
        _fault_injector=stop,
    )


def test_restart_recovery_cleans_journaled_row_payload_install(tmp_path) -> None:
    source_database = tmp_path / "source" / "workbench.db"
    Store(source_database)
    service = DataLifecycleService(source_database)
    connector = service.create_connector(
        _connector("Crash bundle", "fixture://crash")
    )
    service.fetch(
        connector.id,
        SourceFetchRequest(
            object_content='[{"id":"A","x":1,"target":2}]',
            object_version="crash",
        ),
    )
    bundle = tmp_path / "crash.mdwb"
    create_workspace_backup(
        database=source_database,
        data_library_root=source_database.parent / "data-library",
        destination=bundle,
        app_version="test",
    )
    target_database = tmp_path / "target" / "workbench.db"
    target_library = target_database.parent / "data-library"
    prepared = prepare_workspace_restore(
        database=target_database,
        data_library_root=target_library,
        source=bundle,
        task_registry=cast(TaskRegistry, object()),
    )
    process = multiprocessing.get_context("spawn").Process(
        target=_crash_after_row_payload_install,
        args=(
            str(target_database),
            str(target_library),
            prepared.restore_token,
        ),
    )
    process.start()
    process.join(timeout=30)
    assert process.exitcode == 97
    assert any((target_database.parent / "row-payloads").rglob("*.jsonl"))

    restore_root = (
        target_database.parent
        / ".workspace-restore"
        / prepared.restore_token
    )
    state_path = restore_root / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["expires_at"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert recover_incomplete_workspace_restores(
        target_database,
        target_library,
    ) == [prepared.restore_token]
    assert not any((target_database.parent / "row-payloads").rglob("*.jsonl"))
