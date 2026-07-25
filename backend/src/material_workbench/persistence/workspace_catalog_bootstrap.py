"""Register bundled resources and bind legacy Projects without inventing history."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import sqlite3

from material_workbench.data.dataset_registration import (
    CANONICAL_DATASET_CONTRACT_DIGEST,
    CANONICALIZATION_CONTRACT_DIGEST,
    EXCEL_MEDIA_TYPE,
    file_sha256,
    register_dataset_records,
)
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.contracts.schemas import (
    ModelPackageRefCreateInput,
    ProjectSeriesCreateInput,
)
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.modeling.model_packages import ModelPackageLoader, PackageContractError
from material_workbench.modeling.model_lifecycle import (
    runtime_capability_digest,
    task_input_contract_digest,
)
from material_workbench.persistence.chain_catalog_migration import (
    refresh_single_task_project_identities,
)
from material_workbench.task_modules import PRIMARY_DEFAULT_SOURCE, PROCESS_SOURCE


AVAILABLE_PACKAGES_PATH = Path("models/available-packages.json")
PROFILE_ROOT = Path(__file__).parent.parent / "data"
PRIMARY_DATASET_PROFILES = (
    (PRIMARY_DEFAULT_SOURCE, PROFILE_ROOT / "dataset-input-profile-tutorial.json"),
    (PROCESS_SOURCE, PROFILE_ROOT / "dataset-input-profile-process-v1.json"),
)
REPLACED_MPEA_TASK_ID = "mpea-literature-tys-v1"
CURRENT_MPEA_ROOM_TASK_ID = "mpea-room-tensile-v1"
REPLACED_MPEA_ROOM_PACKAGE_ID = "mpea-room-tensile-ridge-v1"
REPLACED_MPEA_ROOM_MANIFEST_DIGEST = (
    "a5c116ca97f84c8ba9f3731531387773d3c6d7448116bcf3a9a77ba7a0e052b0"
)


class WorkspaceCatalogBootstrapError(RuntimeError):
    """The live resources cannot be represented by the persisted catalog."""


@dataclass(frozen=True)
class ProjectBinding:
    task_id: str
    dataset_view_revision_id: str
    task_contract_digest: str
    model_package_ref_id: str
    model_package_manifest_digest: str


def task_definition_digest(registry: TaskRegistry, task_id: str) -> str:
    definition = registry.contract_for(task_id).task_definition
    return semantic_digest(definition.model_dump(mode="json"))


def register_runtime_resources(catalog: WorkspaceCatalog, registry: TaskRegistry) -> dict[str, ProjectBinding]:
    """Register every currently configured task runtime as immutable catalog records."""

    bindings: dict[str, ProjectBinding] = {}
    views_by_dataset: dict[str, str] = {}
    for task_id in registry.available_task_ids:
        entry = registry.entry_for(task_id)
        data = entry.predictor_runtime.data
        source_path = Path(data.source_path)
        profile_path = Path(data.profile_path)
        registered = register_dataset_records(
            catalog=catalog,
            source_path=source_path,
            source_sha256=data.source_sha256,
            profile_path=profile_path,
            locator_kind="bundled",
            locator=source_path,
            name=source_path.stem,
        )
        if registered.dataset_revision_id not in views_by_dataset:
            views_by_dataset[registered.dataset_revision_id] = registered.dataset_view_revision_id

        package = entry.model_package
        contract_digest = task_definition_digest(registry, task_id)
        package_ref = catalog.upsert_model_package_ref(ModelPackageRefCreateInput(
            package_id=package.manifest.package_id,
            task_id=task_id,
            task_contract_digest=contract_digest,
            manifest_digest=package.manifest_sha256,
            locator=str(package.root),
            manifest_json=package.manifest.model_dump(mode="json"),
        ))
        bindings[task_id] = ProjectBinding(
            task_id=task_id,
            dataset_view_revision_id=views_by_dataset[registered.dataset_revision_id],
            task_contract_digest=contract_digest,
            model_package_ref_id=package_ref.id,
            model_package_manifest_digest=package.manifest_sha256,
        )
    return bindings


def register_primary_datasets(catalog: WorkspaceCatalog) -> None:
    """Keep bundled datasets visible even when they intentionally lack an active model."""

    for source_path, profile_path in PRIMARY_DATASET_PROFILES:
        if not source_path.is_file():
            continue
        register_dataset_records(
            catalog=catalog,
            source_path=source_path,
            source_sha256=file_sha256(source_path),
            profile_path=profile_path,
            locator_kind="bundled",
            locator=source_path,
            name=source_path.stem,
        )


def register_available_packages(
    catalog: WorkspaceCatalog,
    registry: TaskRegistry,
    path: Path = AVAILABLE_PACKAGES_PATH,
) -> int:
    """Register explicit alternatives whose training Dataset is currently available."""

    if not path.exists():
        return 0
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("schema_version") != "available-model-packages/v1":
            raise ValueError("unsupported schema version")
        references = document["packages"]
        if not isinstance(references, list) or not all(isinstance(item, str) for item in references):
            raise ValueError("packages must be a string list")
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise WorkspaceCatalogBootstrapError(f"利用可能なModel Package一覧を読めません: {exc}") from exc

    models_root = path.resolve().parent
    available_training = {
        (asset.sha256, profile.profile_digest)
        for dataset in catalog.list_dataset_revisions()
        if (asset := catalog.get_data_asset(dataset.data_asset_id)) is not None
        and (profile := catalog.get_profile_revision(dataset.profile_revision_id)) is not None
    }
    registered = 0
    for reference in references:
        relative = Path(reference)
        if relative.is_absolute():
            raise WorkspaceCatalogBootstrapError("利用可能なModel Package参照は相対パスで指定してください")
        package_root = (models_root / relative).resolve()
        if models_root not in package_root.parents:
            raise WorkspaceCatalogBootstrapError("利用可能なModel Package参照がmodels外を指しています")
        try:
            package = ModelPackageLoader().load(package_root)
        except (OSError, PackageContractError) as exc:
            raise WorkspaceCatalogBootstrapError(f"Model Packageを検証できません: {package_root}: {exc}") from exc
        training_id = package.manifest.provenance.training_data_id
        profile_id = package.manifest.provenance.dataset_profile_id
        if not training_id.startswith("sha256:") or (
            training_id.removeprefix("sha256:"), profile_id
        ) not in available_training:
            continue
        if package.manifest.task_id not in registry.task_ids:
            raise WorkspaceCatalogBootstrapError(
                f"Model PackageのPrediction Taskが登録されていません: {package.manifest.task_id}"
            )
        contract = registry.contract_for(package.manifest.task_id)
        if (
            package.manifest.input_contract_digest
            != task_input_contract_digest(contract.task_definition)
            or package.manifest.runtime_capability_digest
            != runtime_capability_digest(contract.runtime_capability)
        ):
            raise WorkspaceCatalogBootstrapError(
                f"Model Packageが現在のPrediction Task契約と一致しません: {package.manifest.package_id}"
            )
        catalog.upsert_model_package_ref(ModelPackageRefCreateInput(
            package_id=package.manifest.package_id,
            task_id=package.manifest.task_id,
            task_contract_digest=task_definition_digest(registry, package.manifest.task_id),
            manifest_digest=package.manifest_sha256,
            locator=str(package.root),
            manifest_json=package.manifest.model_dump(mode="json"),
        ))
        registered += 1
    return registered


def bind_legacy_projects(database: str | Path, catalog: WorkspaceCatalog, bindings: dict[str, ProjectBinding]) -> int:
    """Pin unbound Projects to upgrade-time resources and label that assumption."""

    migrated_at = datetime.now(UTC).isoformat()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,name,description,task_id FROM projects WHERE binding_provenance='unbound_legacy'"
        ).fetchall()

    prepared: list[tuple[sqlite3.Row, ProjectBinding, str]] = []
    for row in rows:
        binding = bindings.get(row["task_id"])
        if binding is None:
            continue
        series_id = f"project-series-upgrade-{row['id']}"
        catalog.ensure_project_series(
            series_id,
            ProjectSeriesCreateInput(name=row["name"], description=row["description"]),
        )
        prepared.append((row, binding, series_id))

    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for row, binding, series_id in prepared:
            conn.execute(
                "UPDATE projects SET dataset_view_revision_id=?,task_contract_digest=?,model_package_ref_id=?,"
                "model_package_manifest_digest=?,project_series_id=?,binding_provenance='assumed_current_at_upgrade',"
                "binding_migrated_at=? WHERE id=?",
                (
                    binding.dataset_view_revision_id,
                    binding.task_contract_digest,
                    binding.model_package_ref_id,
                    binding.model_package_manifest_digest,
                    series_id,
                    migrated_at,
                    row["id"],
                ),
            )
    return len(prepared)


def refresh_replaced_tutorial_projects(
    database: str | Path, bindings: dict[str, ProjectBinding]
) -> int:
    """Move Projects backed by the replaced bundled tutorial to its new contract.

    This migration is deliberately scoped by the bundled tutorial filename and
    never touches Projects backed by user-managed or other bundled data. Once
    every tutorial Project is rebound, unreachable stale catalog records are
    archived so Data Library does not expose broken duplicate entries.
    """

    migrated_at = datetime.now(UTC).isoformat()
    updated = 0
    stale_view_ids: set[str] = set()
    stale_package_ref_ids: set[str] = set()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT p.id,p.task_id,p.dataset_view_revision_id,p.task_contract_digest,"
            "p.model_package_ref_id,p.model_package_manifest_digest,"
            "a.original_filename,a.locator_kind "
            "FROM projects p "
            "JOIN dataset_view_revisions v ON v.id=p.dataset_view_revision_id AND v.kind='single' "
            "JOIN dataset_view_members vm ON vm.dataset_view_revision_id=v.id "
            "JOIN dataset_revisions d ON d.id=vm.dataset_revision_id "
            "JOIN data_assets a ON a.id=d.data_asset_id "
            "WHERE a.locator_kind='bundled' AND a.original_filename=?",
            (Path(PRIMARY_DEFAULT_SOURCE).name,),
        ).fetchall()
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            binding = bindings.get(row["task_id"])
            if binding is None:
                continue
            desired = (
                binding.dataset_view_revision_id,
                binding.task_contract_digest,
                binding.model_package_ref_id,
                binding.model_package_manifest_digest,
            )
            if (
                row["dataset_view_revision_id"] == binding.dataset_view_revision_id
                and row["task_contract_digest"] == binding.task_contract_digest
            ):
                continue
            stale_view_ids.add(row["dataset_view_revision_id"])
            stale_package_ref_ids.add(row["model_package_ref_id"])
            conn.execute(
                "UPDATE projects SET dataset_view_revision_id=?,task_contract_digest=?,"
                "model_package_ref_id=?,model_package_manifest_digest=?,binding_migrated_at=? "
                "WHERE id=?",
                (*desired, migrated_at, row["id"]),
            )
            updated += 1

        for view_id in stale_view_ids:
            conn.execute(
                "UPDATE dataset_view_revisions SET archived_at=? WHERE id=? "
                "AND NOT EXISTS (SELECT 1 FROM projects WHERE dataset_view_revision_id=?)",
                (migrated_at, view_id, view_id),
            )
        conn.execute(
            "UPDATE dataset_revisions SET archived_at=? "
            "WHERE archived_at IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM dataset_view_members vm "
            "JOIN dataset_view_revisions v ON v.id=vm.dataset_view_revision_id AND v.archived_at IS NULL "
            "WHERE vm.dataset_revision_id=dataset_revisions.id"
            ")",
            (migrated_at,),
        )
        conn.execute(
            "UPDATE data_assets SET archived_at=? "
            "WHERE archived_at IS NULL "
            "AND NOT EXISTS ("
            "SELECT 1 FROM dataset_revisions d "
            "WHERE d.data_asset_id=data_assets.id AND d.archived_at IS NULL"
            ")",
            (migrated_at,),
        )
        for package_ref_id in stale_package_ref_ids:
            conn.execute(
                "UPDATE model_package_refs SET archived_at=? WHERE id=? "
                "AND NOT EXISTS (SELECT 1 FROM projects WHERE model_package_ref_id=?)",
                (migrated_at, package_ref_id, package_ref_id),
            )
    return updated


def migrate_replaced_mpea_room_projects(
    database: str | Path, bindings: dict[str, ProjectBinding]
) -> int:
    """Move only the short-lived three-output MPEA contract to its permanent Task ID."""

    binding = bindings.get(CURRENT_MPEA_ROOM_TASK_ID)
    if binding is None:
        return 0
    migrated_at = datetime.now(UTC).isoformat()
    stale_view_ids: set[str] = set()
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT p.id,p.dataset_view_revision_id "
            "FROM projects p "
            "JOIN model_package_refs m ON m.id=p.model_package_ref_id "
            "WHERE p.task_id=? AND m.package_id=? AND m.manifest_digest=? ",
            (
                REPLACED_MPEA_TASK_ID,
                REPLACED_MPEA_ROOM_PACKAGE_ID,
                REPLACED_MPEA_ROOM_MANIFEST_DIGEST,
            ),
        ).fetchall()
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            stale_view_ids.add(row["dataset_view_revision_id"])
            conn.execute(
                "UPDATE projects SET task_id=?,dataset_view_revision_id=?,task_contract_digest=?,"
                "model_package_ref_id=?,model_package_manifest_digest=?,binding_migrated_at=? "
                "WHERE id=?",
                (
                    CURRENT_MPEA_ROOM_TASK_ID,
                    binding.dataset_view_revision_id,
                    binding.task_contract_digest,
                    binding.model_package_ref_id,
                    binding.model_package_manifest_digest,
                    migrated_at,
                    row["id"],
                ),
            )
        for view_id in stale_view_ids:
            conn.execute(
                "UPDATE dataset_view_revisions SET archived_at=? WHERE id=? "
                "AND NOT EXISTS (SELECT 1 FROM projects WHERE dataset_view_revision_id=?)",
                (migrated_at, view_id, view_id),
            )
    return len(rows)


def archive_unreachable_stale_package_refs(database: str | Path) -> int:
    """Hide unreferenced catalog refs whose on-disk Package was replaced."""

    archived_at = datetime.now(UTC).isoformat()
    archived = 0
    with sqlite3.connect(database) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id,locator,manifest_digest FROM model_package_refs "
            "WHERE archived_at IS NULL"
        ).fetchall()
        actual_by_locator: dict[str, str | None] = {}
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            locator = row["locator"]
            if locator not in actual_by_locator:
                try:
                    actual_by_locator[locator] = ModelPackageLoader().load(
                        Path(locator)
                    ).manifest_sha256
                except (OSError, PackageContractError):
                    actual_by_locator[locator] = None
            actual_digest = actual_by_locator[locator]
            if actual_digest is None or row["manifest_digest"] == actual_digest:
                continue
            result = conn.execute(
                "UPDATE model_package_refs SET archived_at=? WHERE id=? "
                "AND NOT EXISTS (SELECT 1 FROM projects WHERE model_package_ref_id=?)",
                (archived_at, row["id"], row["id"]),
            )
            archived += result.rowcount
    return archived


def audit_project_bindings(database: str | Path) -> None:
    checks = (
        (
            "SELECT p.id FROM projects p LEFT JOIN dataset_view_revisions v ON v.id=p.dataset_view_revision_id "
            "WHERE json_extract(p.scientific_identity_json,'$.identity_kind')='single_task' "
            "AND p.binding_provenance<>'unbound_legacy' AND v.id IS NULL LIMIT 1",
            "Dataset View",
        ),
        (
            "SELECT p.id FROM projects p LEFT JOIN model_package_refs m ON m.id=p.model_package_ref_id "
            "WHERE json_extract(p.scientific_identity_json,'$.identity_kind')='single_task' "
            "AND p.binding_provenance<>'unbound_legacy' "
            "AND (m.id IS NULL OR m.task_id<>p.task_id OR m.manifest_digest<>p.model_package_manifest_digest) LIMIT 1",
            "Model Package",
        ),
        (
            "SELECT p.id FROM projects p LEFT JOIN project_series s ON s.id=p.project_series_id "
            "WHERE p.binding_provenance<>'unbound_legacy' AND s.id IS NULL LIMIT 1",
            "Project Series",
        ),
        (
            "SELECT p.id FROM projects p LEFT JOIN chain_revisions r "
            "ON r.id=json_extract(p.scientific_identity_json,'$.chain_revision_id') "
            "WHERE json_extract(p.scientific_identity_json,'$.identity_kind')='chain' "
            "AND (r.id IS NULL OR r.revision_digest<>"
            "json_extract(p.scientific_identity_json,'$.chain_revision_digest')) LIMIT 1",
            "Chain Revision",
        ),
    )
    with sqlite3.connect(database) as conn:
        for sql, label in checks:
            row = conn.execute(sql).fetchone()
            if row is not None:
                raise WorkspaceCatalogBootstrapError(f"Project {row[0]} の{label}参照が不正です")


def bootstrap_workspace_catalog(database: str | Path, registry: TaskRegistry) -> WorkspaceCatalog:
    catalog = WorkspaceCatalog(database)
    bindings = register_runtime_resources(catalog, registry)
    register_primary_datasets(catalog)
    register_available_packages(catalog, registry)
    bind_legacy_projects(database, catalog, bindings)
    refresh_replaced_tutorial_projects(database, bindings)
    migrate_replaced_mpea_room_projects(database, bindings)
    archive_unreachable_stale_package_refs(database)
    audit_project_bindings(database)
    refresh_single_task_project_identities(database)
    return catalog
