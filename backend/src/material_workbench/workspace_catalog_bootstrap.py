"""Register bundled resources and bind legacy Projects without inventing history."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

from .dataset_profile import load_dataset_profile
from .inference_work_graph import semantic_digest
from .model_lifecycle import dataset_profile_digest
from .schemas import (
    DataAssetCreateInput,
    DatasetRevisionCreateInput,
    ModelPackageRefCreateInput,
    ProfileRevisionCreateInput,
    ProjectSeriesCreateInput,
)
from .task_registry import TaskRegistry
from .workspace_catalog import WorkspaceCatalog


CANONICAL_DATASET_CONTRACT_DIGEST = semantic_digest({"id": "canonical-dataset/v1"})
CANONICALIZATION_CONTRACT_DIGEST = semantic_digest({"id": "workbook-canonicalizer/v1"})
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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


def _profile_revision_number(catalog: WorkspaceCatalog, profile_id: str, profile_digest: str) -> int:
    revisions = [item for item in catalog.list_profile_revisions(include_archived=True) if item.profile_id == profile_id]
    matching = next((item for item in revisions if item.profile_digest == profile_digest), None)
    return matching.revision if matching else max((item.revision for item in revisions), default=0) + 1


def register_runtime_resources(catalog: WorkspaceCatalog, registry: TaskRegistry) -> dict[str, ProjectBinding]:
    """Register every currently configured task runtime as immutable catalog records."""

    bindings: dict[str, ProjectBinding] = {}
    views_by_dataset: dict[str, str] = {}
    for task_id in registry.task_ids:
        entry = registry.entry_for(task_id)
        data = entry.predictor_runtime.data
        source_path = Path(data.source_path)
        profile_path = Path(data.profile_path)
        profile = load_dataset_profile(profile_path)
        effective_profile = profile.model_dump(mode="json", exclude={"task_definitions"})
        effective_digest = dataset_profile_digest(profile_path)

        asset = catalog.upsert_data_asset(DataAssetCreateInput(
            original_filename=source_path.name,
            sha256=data.source_sha256,
            media_type=EXCEL_MEDIA_TYPE if source_path.suffix.lower() == ".xlsx" else "application/octet-stream",
            locator_kind="bundled",
            locator=str(source_path),
        ))
        profile_revision = catalog.upsert_profile_revision(ProfileRevisionCreateInput(
            profile_id=profile.profile_id,
            revision=_profile_revision_number(catalog, profile.profile_id, effective_digest),
            name=profile.profile_id,
            profile_digest=effective_digest,
            canonical_contract_digest=CANONICAL_DATASET_CONTRACT_DIGEST,
            effective_profile_json=effective_profile,
        ))
        dataset = catalog.upsert_dataset_revision(DatasetRevisionCreateInput(
            data_asset_id=asset.id,
            profile_revision_id=profile_revision.id,
            canonicalization_contract_digest=CANONICALIZATION_CONTRACT_DIGEST,
        ))
        if dataset.id not in views_by_dataset:
            view = catalog.ensure_single_dataset_view(dataset.id, name=source_path.stem)
            views_by_dataset[dataset.id] = view.id

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
            dataset_view_revision_id=views_by_dataset[dataset.id],
            task_contract_digest=contract_digest,
            model_package_ref_id=package_ref.id,
            model_package_manifest_digest=package.manifest_sha256,
        )
    return bindings


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
            raise WorkspaceCatalogBootstrapError(
                f"Project {row['id']} のPrediction Taskを解決できません: {row['task_id']}"
            )
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


def audit_project_bindings(database: str | Path) -> None:
    checks = (
        (
            "SELECT p.id FROM projects p LEFT JOIN dataset_view_revisions v ON v.id=p.dataset_view_revision_id "
            "WHERE v.id IS NULL LIMIT 1",
            "Dataset View",
        ),
        (
            "SELECT p.id FROM projects p LEFT JOIN model_package_refs m ON m.id=p.model_package_ref_id "
            "WHERE m.id IS NULL OR m.task_id<>p.task_id OR m.manifest_digest<>p.model_package_manifest_digest LIMIT 1",
            "Model Package",
        ),
        (
            "SELECT p.id FROM projects p LEFT JOIN project_series s ON s.id=p.project_series_id "
            "WHERE s.id IS NULL LIMIT 1",
            "Project Series",
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
    bind_legacy_projects(database, catalog, bindings)
    audit_project_bindings(database)
    return catalog
