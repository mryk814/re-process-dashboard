from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable
from material_workbench.contracts.workspace_bundle_contracts import (
    WorkspaceBundleManifest,
    WorkspaceRestoreCommitResult,
    WorkspaceRestoreResolution,
)
from material_workbench.application.workspace_bundle_shared import (
    LIFECYCLE_ROW_TABLES,
    MANIFEST_ARCHIVE_PATH,
    WorkspaceBundleError,
    _file_digest,
)
from material_workbench.application.workspace_bundle_resource_install import (
    _cleanup_installed_resources,
    _cleanup_installed_row_payloads,
    _install_resources,
    _install_row_payloads,
)
from material_workbench.application.workspace_bundle_manifest import _database_evidence
from material_workbench.application.workspace_bundle_restore_plan import (
    _read_state,
    _restore_root,
    _write_state,
)


def commit_workspace_restore(
    *,
    database: Path,
    data_library_root: Path,
    restore_token: str,
    _fault_injector: Callable[[str], None] | None = None,
) -> WorkspaceRestoreCommitResult:
    root = _restore_root(database, restore_token)
    state = _read_state(root)
    if state.get("status") != "prepared":
        raise WorkspaceBundleError("Restore is not in prepared state")
    expires_at = datetime.fromisoformat(str(state["expires_at"]))
    if datetime.now(UTC) >= expires_at:
        raise WorkspaceBundleError("Prepared restore has expired")
    manifest = WorkspaceBundleManifest.model_validate_json(
        (root / "next" / MANIFEST_ARCHIVE_PATH).read_text(encoding="utf-8")
    )
    staged_database = root / "next" / Path(str(state["database_archive_path"]))
    if _file_digest(staged_database) != state["staged_database_sha256"]:
        raise WorkspaceBundleError("Prepared restore database changed before commit")

    installed_resources: tuple[str, ...] = ()
    installed_row_payloads: tuple[str, ...] = ()
    try:
        installed_row_payloads = _install_row_payloads(
            database=database,
            staged_database=staged_database,
            restore_root=root,
            state=state,
            fault_injector=_fault_injector,
        )
        installed_resources = _install_resources(
            database=database,
            data_library_root=data_library_root,
            root=root,
            manifest=manifest,
        )
        # Locator rebinding intentionally changes only normalized operational paths.
        _, _, evidence, _ = _database_evidence(
            staged_database, expected_tables=manifest.table_evidence
        )
        expected = {item.table: item for item in manifest.table_evidence}
        for item in evidence:
            if (
                manifest.schema_version == "workspace-bundle/v1"
                and not manifest.row_payload_files
                and item.table in LIFECYCLE_ROW_TABLES
            ):
                continue
            before = expected[item.table]
            if item.row_count != before.row_count or item.digest != before.digest:
                raise WorkspaceBundleError(
                    f"Commit changed Workspace evidence in {item.table}"
                )
    except Exception:
        _cleanup_installed_resources(data_library_root, installed_resources)
        _cleanup_installed_row_payloads(database, installed_row_payloads)
        raise

    rollback_database = root / "rollback-workbench.db"
    state["status"] = "committing"
    state["commit_database_sha256"] = _file_digest(staged_database)
    state["previous_database_sha256"] = (
        _file_digest(database) if database.exists() else None
    )
    state["installed_resource_roots"] = list(installed_resources)
    state["installed_row_payload_files"] = list(installed_row_payloads)
    _write_state(root, state)
    if _fault_injector is not None:
        _fault_injector("after_journal_committing")
    moved_current = False
    installed_next = False
    try:
        if database.exists():
            os.replace(database, rollback_database)
            moved_current = True
        if _fault_injector is not None:
            # This checkpoint means that the previous-database move phase has
            # completed.  On a first restore there is no database to move, but
            # a process can still stop at the same transaction boundary.
            _fault_injector("after_current_moved")
        os.replace(staged_database, database)
        installed_next = True
        if _fault_injector is not None:
            _fault_injector("after_database_committed")
        state["status"] = "committed"
        state["committed_at"] = datetime.now(UTC).isoformat()
        _write_state(root, state)
        if _fault_injector is not None:
            _fault_injector("after_journal_committed")
    except Exception as exc:
        if installed_next and database.exists():
            failed = root / "failed-workbench.db"
            os.replace(database, failed)
        if moved_current and rollback_database.exists():
            os.replace(rollback_database, database)
        _cleanup_installed_resources(data_library_root, installed_resources)
        _cleanup_installed_row_payloads(database, installed_row_payloads)
        state["status"] = "commit_failed"
        state["failure"] = str(exc)
        _write_state(root, state)
        raise WorkspaceBundleError(
            f"Workspace restore commit failed; current Workspace was preserved: {exc}"
        ) from exc
    return WorkspaceRestoreCommitResult(
        restore_token=restore_token,
        rollback_available=moved_current,
    )


def rollback_workspace_restore(
    *,
    database: Path,
    data_library_root: Path,
    restore_token: str,
) -> WorkspaceRestoreResolution:
    root = _restore_root(database, restore_token)
    state = _read_state(root)
    rollback_database = root / "rollback-workbench.db"
    if not rollback_database.exists():
        if state.get("previous_database_sha256") is not None:
            raise WorkspaceBundleError("Rollback database is unavailable")
        if database.exists():
            database.unlink()
        _cleanup_installed_resources(
            data_library_root,
            state.get("installed_resource_roots"),
        )
        _cleanup_installed_row_payloads(
            database,
            state.get("installed_row_payload_files"),
        )
        shutil.rmtree(root, ignore_errors=True)
        return WorkspaceRestoreResolution(
            status="rolled_back", restore_token=restore_token
        )
    failed_database = root / "failed-workbench.db"
    if database.exists():
        os.replace(database, failed_database)
    os.replace(rollback_database, database)
    _cleanup_installed_resources(
        data_library_root,
        state.get("installed_resource_roots"),
    )
    _cleanup_installed_row_payloads(
        database,
        state.get("installed_row_payload_files"),
    )
    shutil.rmtree(root, ignore_errors=True)
    return WorkspaceRestoreResolution(status="rolled_back", restore_token=restore_token)


def finalize_workspace_restore(
    *,
    database: Path,
    restore_token: str,
) -> WorkspaceRestoreResolution:
    root = _restore_root(database, restore_token)
    state = _read_state(root)
    if state.get("status") != "committed":
        raise WorkspaceBundleError("Only a committed restore can be finalized")
    shutil.rmtree(root)
    parent = root.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return WorkspaceRestoreResolution(status="finalized", restore_token=restore_token)


def cancel_workspace_restore(
    *,
    database: Path,
    restore_token: str,
) -> WorkspaceRestoreResolution:
    root = _restore_root(database, restore_token)
    state = _read_state(root)
    if state.get("status") not in {"prepared", "commit_failed"}:
        raise WorkspaceBundleError(
            "Only a prepared or preserved failed restore can be cancelled"
        )
    _cleanup_installed_row_payloads(
        database,
        state.get("installed_row_payload_files"),
    )
    shutil.rmtree(root)
    parent = root.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
    return WorkspaceRestoreResolution(status="cancelled", restore_token=restore_token)


def recover_incomplete_workspace_restores(
    database: Path,
    data_library_root: Path | None = None,
) -> list[str]:
    restore_parent = database.parent / ".workspace-restore"
    recovered: list[str] = []
    if not restore_parent.exists():
        return recovered
    library_root = data_library_root or database.parent / "data-library"
    for root in sorted(path for path in restore_parent.iterdir() if path.is_dir()):
        try:
            state = _read_state(root)
        except WorkspaceBundleError:
            continue
        if state.get("status") in {"committing", "committed"}:
            rollback_database = root / "rollback-workbench.db"
            if rollback_database.exists():
                failed_database = root / "failed-workbench.db"
                if database.exists():
                    os.replace(database, failed_database)
                os.replace(rollback_database, database)
                _cleanup_installed_resources(
                    library_root,
                    state.get("installed_resource_roots"),
                )
                _cleanup_installed_row_payloads(
                    database,
                    state.get("installed_row_payload_files"),
                )
                recovered.append(str(state.get("token", root.name)))
                shutil.rmtree(root, ignore_errors=True)
            elif state.get("previous_database_sha256") is None and (
                not database.exists()
                or _file_digest(database) == state.get("commit_database_sha256")
            ):
                # A first restore has no rollback database.  Depending on the
                # stop point, the imported database is either not installed yet
                # or is the active database.  Returning to the pre-transaction
                # state therefore means an empty Workspace.
                if database.exists():
                    database.unlink()
                _cleanup_installed_resources(
                    library_root,
                    state.get("installed_resource_roots"),
                )
                _cleanup_installed_row_payloads(
                    database,
                    state.get("installed_row_payload_files"),
                )
                recovered.append(str(state.get("token", root.name)))
                shutil.rmtree(root, ignore_errors=True)
            elif (
                state.get("status") == "committing"
                and state.get("previous_database_sha256") is not None
                and database.exists()
                and _file_digest(database) == state.get("previous_database_sha256")
            ):
                # The process stopped after journaling but before moving the
                # current DB. The original Workspace is already active.
                _cleanup_installed_resources(
                    library_root,
                    state.get("installed_resource_roots"),
                )
                _cleanup_installed_row_payloads(
                    database,
                    state.get("installed_row_payload_files"),
                )
                recovered.append(str(state.get("token", root.name)))
                shutil.rmtree(root, ignore_errors=True)
        elif state.get("status") == "prepared":
            try:
                expires_at = datetime.fromisoformat(str(state["expires_at"]))
            except (KeyError, ValueError):
                continue
            if datetime.now(UTC) >= expires_at:
                _cleanup_installed_resources(
                    library_root,
                    state.get("installed_resource_roots"),
                )
                _cleanup_installed_row_payloads(
                    database,
                    state.get("installed_row_payload_files"),
                )
                recovered.append(str(state.get("token", root.name)))
                shutil.rmtree(root, ignore_errors=True)
        elif state.get("status") == "commit_failed":
            _cleanup_installed_resources(
                library_root,
                state.get("installed_resource_roots"),
            )
            _cleanup_installed_row_payloads(
                database,
                state.get("installed_row_payload_files"),
            )
            recovered.append(str(state.get("token", root.name)))
            shutil.rmtree(root, ignore_errors=True)
    if restore_parent.exists() and not any(restore_parent.iterdir()):
        restore_parent.rmdir()
    return recovered
