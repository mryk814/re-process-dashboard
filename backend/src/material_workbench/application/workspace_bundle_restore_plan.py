from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
from material_workbench.contracts.chain_contracts import (
    semantic_digest,
    task_contract_surface,
    validate_chain_revision,
)
from material_workbench.contracts.workspace_bundle_contracts import (
    WorkspaceBundleDiagnostic,
    WorkspaceBundleManifest,
    WorkspaceBundleResource,
    WorkspaceRestorePrepared,
)
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalog,
)
from material_workbench.persistence.sqlite_connection import (
    connect_sqlite,
    validate_sqlite_foreign_keys,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.row_payload_store import (
    RowPayloadReference,
    RowPayloadStore,
)
from material_workbench.persistence.data_lifecycle_payload_storage import (
    StoredLifecycleRowResource,
    hydrate_curation_run,
    hydrate_raw_snapshot,
)
from material_workbench.contracts.data_lifecycle_contracts import (
    CurationRun,
    RawSourceSnapshot,
)
from material_workbench.persistence.welding_chain_bootstrap import (
    welding_stage_a_surface,
)
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.application.project_runtime import ProjectRuntimeResolver
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.application.workspace_bundle_shared import (
    LIFECYCLE_ROW_TABLES,
    RESTORE_EXPIRY_HOURS,
    WorkspaceBundleError,
    _file_digest,
)
from material_workbench.application.workspace_bundle_archive import (
    _extract_verified_bundle,
    _validate_staged_row_payloads,
)
from material_workbench.application.workspace_bundle_manifest import (
    _current_migration_inventory,
    _database_evidence,
    _migration_inventory,
    _validate_migration_inventory,
)


def _lifecycle_semantic_evidence(database: Path) -> dict[str, str]:
    connection = connect_sqlite(database)
    store = RowPayloadStore(database)
    evidence: dict[str, str] = {}
    try:
        for table, model, hydrate, record_kind in (
            (
                "raw_source_snapshots",
                RawSourceSnapshot,
                hydrate_raw_snapshot,
                "raw-json-record/v1",
            ),
            (
                "source_curation_runs",
                CurationRun,
                hydrate_curation_run,
                "curated-row/v1",
            ),
        ):
            columns = {
                str(row["name"])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            selected = ["id", "payload"]
            if "row_payload_sha256" in columns:
                selected.extend(
                    ["row_payload_sha256", "row_payload_bytes", "row_count"]
                )
            rows = connection.execute(
                f'SELECT {",".join(selected)} FROM "{table}" ORDER BY id'
            )
            digest = sha256()
            for row in rows:
                stored = str(row["payload"])
                try:
                    wrapper = StoredLifecycleRowResource.model_validate_json(stored)
                except Exception:
                    try:
                        resource = model.model_validate_json(stored)
                    except Exception:
                        payload = {
                            "resource_id": str(row["id"]),
                            "quarantined_sha256": sha256(
                                stored.encode("utf-8")
                            ).hexdigest(),
                        }
                    else:
                        payload = resource.model_dump(mode="json")
                else:
                    if wrapper.unavailable_reason is not None:
                        if wrapper.quarantined_payload is None:
                            raise WorkspaceBundleError(
                                "Lifecycle quarantine reference is missing"
                            )
                        payload = {
                            "resource_id": str(row["id"]),
                            "quarantined_sha256": (wrapper.quarantined_payload.sha256),
                        }
                    else:
                        values = (
                            row["row_payload_sha256"],
                            row["row_payload_bytes"],
                            row["row_count"],
                        )
                        reference = (
                            RowPayloadReference(
                                record_kind=record_kind,
                                sha256=str(values[0]),
                                size_bytes=int(values[1]),
                                row_count=int(values[2]),
                            )
                            if all(value is not None for value in values)
                            else wrapper.row_payload
                        )
                        resource = hydrate(
                            stored,
                            store,
                            expected_reference=reference,
                            expected_resource_id=str(row["id"]),
                        )
                        payload = resource.model_dump(mode="json")
                encoded = json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
            evidence[table] = digest.hexdigest()
    finally:
        connection.close()
    return evidence


def _resource_by_reference(
    manifest: WorkspaceBundleManifest,
) -> dict[tuple[str, str], WorkspaceBundleResource]:
    return {
        (resource.kind, resource.reference_id): resource
        for resource in manifest.bundled_resources
    }


def _staged_resource_root(
    extracted: Path,
    resource: WorkspaceBundleResource,
) -> Path:
    return extracted / Path(resource.bundle_root)


def _rebind_staged_locators(
    database: Path,
    extracted: Path,
    manifest: WorkspaceBundleManifest,
) -> None:
    resources = _resource_by_reference(manifest)
    connection = connect_sqlite(database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        for row in connection.execute("SELECT id FROM data_assets").fetchall():
            resource = resources.get(("data_asset", str(row["id"])))
            if resource is None or resource.primary_file is None:
                raise WorkspaceBundleError(
                    f"Managed Data Asset is missing from bundle: {row['id']}"
                )
            locator = extracted / Path(resource.primary_file)
            connection.execute(
                "UPDATE data_assets SET locator_kind='managed',locator=? WHERE id=?",
                (str(locator.resolve()), row["id"]),
            )
        for row in connection.execute("SELECT id FROM model_package_refs").fetchall():
            resource = resources.get(("model_package", str(row["id"])))
            if resource is None:
                raise WorkspaceBundleError(
                    f"Model Package is missing from bundle: {row['id']}"
                )
            connection.execute(
                "UPDATE model_package_refs SET locator=? WHERE id=?",
                (str(_staged_resource_root(extracted, resource).resolve()), row["id"]),
            )
        connection.commit()
    finally:
        connection.close()


def _validate_restored_references(
    database: Path,
    task_registry: TaskRegistry,
    transform_catalog: DeterministicTransformCatalog | None = None,
) -> tuple[WorkspaceBundleDiagnostic, ...]:
    catalog = WorkspaceCatalog(database)
    store = Store(database)
    validate_sqlite_foreign_keys(database)
    resolver = ProjectRuntimeResolver(catalog, task_registry)
    unresolved: list[str] = []
    package_refs = catalog.list_model_package_refs(include_archived=True)
    for project in store.list_projects(include_archived=True):
        identity = project.scientific_identity
        if identity.identity_kind == "chain":
            revision = store.get_chain_revision(identity.chain_revision_id)
            if revision is None:
                unresolved.append(f"{project.id}: 固定されたChain Revisionがありません")
                continue
            revision_payload = revision.model_dump(
                mode="json", exclude={"revision_digest"}
            )
            if (
                revision.revision_digest != identity.chain_revision_digest
                or revision.revision_digest != semantic_digest(revision_payload)
            ):
                unresolved.append(f"{project.id}: Chain Revision digestが一致しません")
                continue
            definition = store.get_chain_definition(
                revision.chain_id, revision.chain_definition_digest
            )
            if definition is None:
                unresolved.append(
                    f"{project.id}: 固定されたChain Definitionがありません"
                )
                continue
            surfaces = {}
            for stage in revision.stages:
                if stage.stage_kind == "deterministic_transform":
                    if transform_catalog is None:
                        unresolved.append(
                            f"{project.id}/{stage.stage_id}: "
                            "決定論的Transformの実行資源を検証できません"
                        )
                        continue
                    try:
                        transform_entry = transform_catalog.entry(stage.contract_id)
                        surface = welding_stage_a_surface(transform_entry.package)
                    except Exception as exc:
                        unresolved.append(f"{project.id}/{stage.stage_id}: {exc}")
                        continue
                    surfaces[(stage.stage_kind, stage.contract_id)] = surface
                    actual_package = f"sha256:{transform_entry.package.manifest_sha256}"
                    if surface.contract_digest != stage.contract_digest:
                        unresolved.append(
                            f"{project.id}/{stage.stage_id}: "
                            "Transform contract digestが一致しません"
                        )
                    if actual_package != stage.package_manifest_digest:
                        unresolved.append(
                            f"{project.id}/{stage.stage_id}: "
                            "Transform Package digestが一致しません"
                        )
                    continue
                try:
                    contract = task_registry.contract_for(stage.contract_id)
                    entry = task_registry.entry_for(stage.contract_id)
                except Exception as exc:
                    unresolved.append(f"{project.id}/{stage.stage_id}: {exc}")
                    continue
                actual_contract = semantic_digest(
                    contract.task_definition.model_dump(mode="json")
                )
                surfaces[(stage.stage_kind, stage.contract_id)] = task_contract_surface(
                    contract.task_definition,
                    contract_digest=actual_contract,
                )
                if actual_contract != stage.contract_digest:
                    unresolved.append(
                        f"{project.id}/{stage.stage_id}: Task contract digestが一致しません"
                    )
                if entry.package_digest != stage.package_manifest_digest:
                    unresolved.append(
                        f"{project.id}/{stage.stage_id}: Model Package digestが一致しません"
                    )
                matching_refs = [
                    ref
                    for ref in package_refs
                    if ref.task_id == stage.contract_id
                    and ref.task_contract_digest == stage.contract_digest
                    and f"sha256:{ref.manifest_digest}" == stage.package_manifest_digest
                ]
                if len(matching_refs) != 1:
                    unresolved.append(
                        f"{project.id}/{stage.stage_id}: "
                        "固定されたModel Package参照を一意に解決できません"
                    )
                else:
                    ref = matching_refs[0]
                    try:
                        package = ModelPackageLoader().load(Path(ref.locator))
                    except Exception as exc:
                        unresolved.append(
                            f"{project.id}/{stage.stage_id}: "
                            f"Model Package本体を検証できません: {exc}"
                        )
                    else:
                        if (
                            package.manifest_sha256 != ref.manifest_digest
                            or package.manifest.task_id != ref.task_id
                            or package.manifest.package_id != ref.package_id
                        ):
                            unresolved.append(
                                f"{project.id}/{stage.stage_id}: "
                                "Model Package本体と参照が一致しません"
                            )
                view = catalog.get_dataset_view_revision(
                    stage.dataset_view_revision_id or "",
                    include_archived=True,
                )
                if view is None:
                    unresolved.append(
                        f"{project.id}/{stage.stage_id}: Dataset Viewがありません"
                    )
                    continue
                profile_digests: set[str] = set()
                for member in view.members:
                    dataset = catalog.get_dataset_revision(
                        member.dataset_revision_id,
                        include_archived=True,
                    )
                    profile = (
                        catalog.get_profile_revision(
                            dataset.profile_revision_id,
                            include_archived=True,
                        )
                        if dataset is not None
                        else None
                    )
                    if profile is not None:
                        profile_digests.add(profile.profile_digest)
                if profile_digests != {stage.dataset_profile_digest}:
                    unresolved.append(
                        f"{project.id}/{stage.stage_id}: Dataset Profile digestが一致しません"
                    )
            if len(surfaces) == len(revision.stages):
                try:
                    validate_chain_revision(
                        definition,
                        revision,
                        contracts=surfaces,
                    )
                except (KeyError, ValueError) as exc:
                    unresolved.append(
                        f"{project.id}: Chain Revision内部契約が不正です: {exc}"
                    )
            continue
        try:
            resolver.resolve(project)
        except Exception as exc:
            unresolved.append(f"{project.id}: {exc}")
    return (
        WorkspaceBundleDiagnostic(
            id="restored-fixed-references",
            status="warning" if unresolved else "ok",
            detail=(
                "; ".join(unresolved[:10])
                if unresolved
                else "All Project fixed references resolved"
            ),
        ),
    )


def _state_path(root: Path) -> Path:
    return root / "state.json"


def _write_state(root: Path, state: dict[str, object]) -> None:
    temporary = root / ".state.json.tmp"
    temporary.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, _state_path(root))


def prepare_workspace_restore(
    *,
    database: Path,
    data_library_root: Path,
    source: str | Path,
    task_registry: TaskRegistry,
    transform_catalog: DeterministicTransformCatalog | None = None,
) -> WorkspaceRestorePrepared:
    token = uuid4().hex
    restore_root = database.parent / ".workspace-restore" / token
    next_root = restore_root / "next"
    next_root.mkdir(parents=True, exist_ok=False)
    source_path = Path(source).expanduser().resolve()
    try:
        manifest, manifest_digest = _extract_verified_bundle(source_path, next_root)
        staged_database = next_root / Path(manifest.database.path)
        _validate_migration_inventory(staged_database, manifest)
        legacy_lifecycle_evidence = (
            _lifecycle_semantic_evidence(staged_database)
            if manifest.schema_version == "workspace-bundle/v1"
            and not manifest.row_payload_files
            else None
        )
        # Store applies only the application's allow-listed migrations.
        Store(staged_database)
        _validate_staged_row_payloads(staged_database, manifest)
        if (
            legacy_lifecycle_evidence is not None
            and _lifecycle_semantic_evidence(staged_database)
            != legacy_lifecycle_evidence
        ):
            raise WorkspaceBundleError(
                "Migration changed Data Lifecycle semantic evidence"
            )
        migrated_inventory = _migration_inventory(staged_database)
        supported_inventory = _current_migration_inventory()
        if migrated_inventory != supported_inventory:
            missing = sorted(set(supported_inventory) - set(migrated_inventory))
            raise WorkspaceBundleError(
                "Workspace migration did not reach the current schema"
                + (f": missing {', '.join(missing)}" if missing else "")
            )
        _rebind_staged_locators(staged_database, next_root, manifest)
        reference_diagnostics = _validate_restored_references(
            staged_database,
            task_registry,
            transform_catalog,
        )
        _, _, evidence, database_diagnostics = _database_evidence(
            staged_database,
            expected_tables=manifest.table_evidence,
        )
        expected = {item.table: item for item in manifest.table_evidence}
        for item in evidence:
            if (
                legacy_lifecycle_evidence is not None
                and item.table in LIFECYCLE_ROW_TABLES
            ):
                continue
            before = expected[item.table]
            if item.row_count != before.row_count or item.digest != before.digest:
                raise WorkspaceBundleError(
                    f"Migration changed Workspace evidence in {item.table}"
                )
        staged_digest = _file_digest(staged_database)
        state: dict[str, object] = {
            "schema_version": "workspace-restore-state/v1",
            "token": token,
            "status": "prepared",
            "bundle_sha256": _file_digest(source_path),
            "manifest_sha256": manifest_digest,
            "staged_database_sha256": staged_digest,
            "database_archive_path": manifest.database.path,
            "expires_at": (
                datetime.now(UTC) + timedelta(hours=RESTORE_EXPIRY_HOURS)
            ).isoformat(),
        }
        _write_state(restore_root, state)
        return WorkspaceRestorePrepared(
            restore_token=token,
            manifest=manifest,
            migrated_database_sha256=staged_digest,
            diagnostics=(*database_diagnostics, *reference_diagnostics),
        )
    except Exception:
        shutil.rmtree(restore_root, ignore_errors=True)
        raise


def _restore_root(database: Path, token: str) -> Path:
    if len(token) != 32 or any(
        character not in "0123456789abcdef" for character in token
    ):
        raise WorkspaceBundleError("Invalid restore token")
    root = database.parent / ".workspace-restore" / token
    if not root.is_dir():
        raise WorkspaceBundleError("Prepared restore was not found")
    return root


def _read_state(root: Path) -> dict[str, object]:
    try:
        state = json.loads(_state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise WorkspaceBundleError("Restore state is invalid") from exc
    if state.get("schema_version") != "workspace-restore-state/v1":
        raise WorkspaceBundleError("Restore state version is unsupported")
    return state


def _final_resource_path(
    data_library_root: Path,
    resource: WorkspaceBundleResource,
) -> Path:
    digest = resource.bundle_digest
    kind = "data-assets" if resource.kind == "data_asset" else "model-packages"
    return data_library_root / "by-digest" / kind / digest
