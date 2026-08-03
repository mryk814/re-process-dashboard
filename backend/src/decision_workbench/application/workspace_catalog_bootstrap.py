"""Register bundled resources and bind legacy Projects without inventing history."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import json
import sqlite3

from decision_workbench.application.dataset_registration import (
    CANONICAL_DATASET_CONTRACT_DIGEST,
    CANONICALIZATION_CONTRACT_DIGEST,
    EXCEL_MEDIA_TYPE,
    register_dataset_records,
)
from decision_workbench.data.file_integrity import file_sha256
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.contracts.feature_recipe_contracts import FeatureRecipe
from decision_workbench.contracts.data_library_contracts import (
    ModelPackageRefCreateInput,
    ModelPackageRegistrationWarning,
)
from decision_workbench.contracts.task_contracts import persisted_task_definition_payload
from decision_workbench.tasks.task_registry import TaskRegistry
from decision_workbench.persistence.workspace_catalog import (
    CatalogConflictError,
    WorkspaceCatalog,
)
from decision_workbench.persistence.sqlite_connection import sqlite_connection
from decision_workbench.modeling.packages.contracts import (
    FeaturePipelineDocument,
    PackageContractError,
)
from decision_workbench.modeling.packages.loader import ModelPackageLoader
from decision_workbench.modeling.packages.verification import (
    VerifiedModelPackage,
)
from decision_workbench.modeling.model_lifecycle import (
    AVAILABLE_PACKAGES_PATH,
    QualityReport,
    runtime_capability_digest,
    task_input_contract_digest,
)
from decision_workbench.modeling.training.validation_plan import ValidationPlan
from decision_workbench.persistence.chain_catalog_migration import (
    refresh_single_task_project_identities,
)
from decision_workbench.task_composition.builtin.sources import (
    PRIMARY_DEFAULT_SOURCE,
    PROCESS_SOURCE,
)


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
REPLACED_BUNDLED_TUTORIAL_FILENAMES = (
    "material_workbench_tutorial_v1.xlsx",
    Path(PRIMARY_DEFAULT_SOURCE).name,
)
REPLACED_MODEL_PACKAGE_IDS = {
    "annealed-gp-stable-ard-process-v1": "annealed-gp-stable-ard-process-v2",
    "annealed-gp-stable-ard-tutorial-v1": "annealed-gp-stable-ard-tutorial-v2",
    "annealed-heteroscedastic-gp-process-v1": "annealed-heteroscedastic-gp-process-v2",
    "annealed-hierarchical-bayes-process-v1": "annealed-hierarchical-bayes-process-v2",
    "annealed-lightgbm-standard-process-v1": "annealed-lightgbm-standard-process-v2",
    "annealed-lightgbm-standard-tutorial-v1": "annealed-lightgbm-standard-tutorial-v2",
    "hot-rolled-horseshoe-process-v1": "hot-rolled-horseshoe-process-v2",
    "hot-rolled-tutorial-v1": "hot-rolled-tutorial-v2",
}


class WorkspaceCatalogBootstrapError(RuntimeError):
    """The live resources cannot be represented by the persisted catalog."""


@dataclass(frozen=True)
class ProjectBinding:
    task_id: str
    dataset_view_revision_id: str
    task_contract_digest: str
    model_package_ref_id: str
    model_package_manifest_digest: str


def _catalog_manifest(package: VerifiedModelPackage) -> dict[str, object]:
    """Persist only browser-safe identities derived from verified artifacts."""

    manifest = package.manifest.model_dump(mode="json")
    feature_recipe = None
    validation_plans: list[dict[str, str]] = []
    pipeline_spec = package.manifest.feature_pipeline
    if pipeline_spec is not None:
        pipeline = FeaturePipelineDocument.model_validate_json(
            package.artifact_path(pipeline_spec.spec).read_text(
                encoding="utf-8"
            )
        )
        if pipeline.feature_recipe is not None:
            recipe = FeatureRecipe.model_validate_json(
                package.artifact_path(
                    pipeline.feature_recipe.recipe
                ).read_text(encoding="utf-8")
            )
            feature_recipe = {
                "identity_id": recipe.id,
                "version": recipe.version,
                "digest": pipeline.feature_recipe.recipe_digest,
            }
    if package.manifest.quality_report is not None:
        quality = QualityReport.model_validate_json(
            package.artifact_path(
                package.manifest.quality_report
            ).read_text(encoding="utf-8")
        )
        for target, payload in sorted(
            (quality.validation_plans or {}).items()
        ):
            plan_payload = dict(payload)
            digest = plan_payload.pop("digest", None)
            plan = ValidationPlan.model_validate(plan_payload)
            if not isinstance(digest, str):
                evidence = (quality.validation_evidence or {}).get(target)
                digest = (
                    evidence.validation_plan_digest
                    if evidence is not None
                    else None
                )
            if digest is not None:
                validation_plans.append(
                    {
                        "target": target,
                        "schema_version": plan.schema_version,
                        "strategy": plan.strategy,
                        "digest": digest,
                        "identity_source": "validation_plan",
                    }
                )
        if not validation_plans:
            validation_plans.extend(
                {
                    "target": metric.target,
                    "schema_version": quality.schema_version,
                    "strategy": quality.split,
                    "digest": semantic_digest(
                        {
                            "schema_version": quality.schema_version,
                            "split": quality.split,
                            "folds": quality.folds,
                            "target": metric.target,
                        }
                    ),
                    "identity_source": "quality_report_split",
                }
                for metric in quality.targets
            )
    manifest["_catalog_identity"] = {
        "schema_version": "model-package-catalog-identity/v1",
        "feature_recipe": feature_recipe,
        "validation_plans": validation_plans,
    }
    return manifest


def task_definition_digest(registry: TaskRegistry, task_id: str) -> str:
    definition = registry.contract_for(task_id).task_definition
    return semantic_digest(persisted_task_definition_payload(definition))


def register_runtime_resources(
    catalog: WorkspaceCatalog,
    registry: TaskRegistry,
    *,
    package_origins: dict[str, str] | None = None,
    personal_model_store_paths: Iterable[Path] = (),
) -> dict[str, ProjectBinding]:
    """Register every currently configured task runtime as immutable catalog records."""

    bindings: dict[str, ProjectBinding] = {}
    views_by_dataset: dict[str, str] = {}
    personal_model_stores = {
        Path(item).resolve() for item in personal_model_store_paths
    }
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
            manifest_json=_catalog_manifest(package),
        ))
        if package_origins is not None:
            package_root = package.root.resolve()
            package_origins[package_ref.id] = (
                "personal"
                if any(
                    store == package_root or store in package_root.parents
                    for store in personal_model_stores
                )
                else "bundled"
            )
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
    *,
    storage_scope: str = "bundled",
    package_origins: dict[str, str] | None = None,
    warnings: list[ModelPackageRegistrationWarning] | None = None,
    strict: bool = True,
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
        message = f"利用可能なModel Package一覧を読めません: {exc}"
        if strict:
            raise WorkspaceCatalogBootstrapError(message) from exc
        if warnings is not None:
            warnings.append(ModelPackageRegistrationWarning(
                source=str(path),
                message=message,
            ))
        return 0

    models_root = path.resolve().parent
    available_training = {
        (asset.sha256, profile.profile_digest)
        for dataset in catalog.list_dataset_revisions()
        if (asset := catalog.get_data_asset(dataset.data_asset_id)) is not None
        and (profile := catalog.get_profile_revision(dataset.profile_revision_id)) is not None
    }
    registered = 0
    for reference in references:
        try:
            relative = Path(reference)
            if relative.is_absolute():
                raise WorkspaceCatalogBootstrapError(
                    "利用可能なModel Package参照は相対パスで指定してください"
                )
            package_root = (models_root / relative).resolve()
            if models_root not in package_root.parents:
                raise WorkspaceCatalogBootstrapError(
                    "利用可能なModel Package参照がmodels外を指しています"
                )
            package = ModelPackageLoader().load(package_root)
            training_id = package.manifest.provenance.training_data_id
            profile_id = package.manifest.provenance.dataset_profile_id
            if not training_id.startswith("sha256:") or (
                training_id.removeprefix("sha256:"), profile_id
            ) not in available_training:
                continue
            if package.manifest.task_id not in registry.task_ids:
                raise WorkspaceCatalogBootstrapError(
                    "Model PackageのPrediction Taskが登録されていません: "
                    f"{package.manifest.task_id}"
                )
            contract = registry.contract_for(package.manifest.task_id)
            if (
                package.manifest.input_contract_digest
                != task_input_contract_digest(contract.task_definition)
                or package.manifest.runtime_capability_digest
                != runtime_capability_digest(contract.runtime_capability)
            ):
                raise WorkspaceCatalogBootstrapError(
                    "Model Packageが現在のPrediction Task契約と一致しません: "
                    f"{package.manifest.package_id}"
                )
            same_id_refs = [
                item
                for item in catalog.list_model_package_refs(include_archived=True)
                if item.package_id == package.manifest.package_id
            ]
            if any(
                item.manifest_digest != package.manifest_sha256
                for item in same_id_refs
            ):
                raise WorkspaceCatalogBootstrapError(
                    "同じpackage_idのModel Packageが別内容で登録済みです: "
                    f"{package.manifest.package_id}"
                )
            existing = next(
                (
                    item
                    for item in same_id_refs
                    if item.manifest_digest == package.manifest_sha256
                ),
                None,
            )
            if (
                storage_scope == "personal"
                and existing is not None
                and package_origins is not None
                and package_origins.get(existing.id) == "bundled"
            ):
                continue
            package_ref = catalog.upsert_model_package_ref(ModelPackageRefCreateInput(
                package_id=package.manifest.package_id,
                task_id=package.manifest.task_id,
                task_contract_digest=task_definition_digest(
                    registry, package.manifest.task_id
                ),
                manifest_digest=package.manifest_sha256,
                locator=str(package.root),
                manifest_json=_catalog_manifest(package),
            ))
            if package_origins is not None:
                package_origins[package_ref.id] = storage_scope
            registered += 1
        except (
            OSError,
            KeyError,
            ValueError,
            PackageContractError,
            WorkspaceCatalogBootstrapError,
            CatalogConflictError,
        ) as exc:
            message = (
                str(exc)
                if isinstance(exc, (WorkspaceCatalogBootstrapError, CatalogConflictError))
                else f"Model Packageを検証できません: {reference}: {exc}"
            )
            if strict:
                if isinstance(exc, WorkspaceCatalogBootstrapError):
                    raise
                raise WorkspaceCatalogBootstrapError(message) from exc
            if warnings is not None:
                warnings.append(ModelPackageRegistrationWarning(
                    source=str(path),
                    reference=reference,
                    message=message,
                ))
    return registered


def bind_legacy_projects(database: str | Path, catalog: WorkspaceCatalog, bindings: dict[str, ProjectBinding]) -> int:
    """Pin unbound Projects to upgrade-time resources and label that assumption."""

    migrated_at = datetime.now(UTC).isoformat()
    with sqlite_connection(database) as conn:
        rows = conn.execute(
            "SELECT id,name,description,task_id FROM projects WHERE binding_provenance='unbound_legacy'"
        ).fetchall()

    prepared: list[tuple[sqlite3.Row, ProjectBinding]] = []
    for row in rows:
        binding = bindings.get(row["task_id"])
        if binding is None:
            continue
        prepared.append((row, binding))

    with sqlite_connection(database) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        for row, binding in prepared:
            conn.execute(
                "UPDATE projects SET dataset_view_revision_id=?,task_contract_digest=?,model_package_ref_id=?,"
                "model_package_manifest_digest=?,binding_provenance='assumed_current_at_upgrade',"
                "binding_migrated_at=? WHERE id=?",
                (
                    binding.dataset_view_revision_id,
                    binding.task_contract_digest,
                    binding.model_package_ref_id,
                    binding.model_package_manifest_digest,
                    migrated_at,
                    row["id"],
                ),
            )
    return len(prepared)


def migrate_replaced_model_package_projects(database: str | Path) -> int:
    """Rebind Projects after an accidentally in-place Package revision.

    The old Package directories remain byte-immutable for provenance. Projects
    move explicitly to the corresponding v2 Package so the current Task
    contract and the pinned manifest digest change together.
    """

    migrated_at = datetime.now(UTC).isoformat()
    updated = 0
    with sqlite_connection(database) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for previous_id, current_id in REPLACED_MODEL_PACKAGE_IDS.items():
            current = conn.execute(
                "SELECT id,task_contract_digest,manifest_digest "
                "FROM model_package_refs WHERE package_id=? AND archived_at IS NULL "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (current_id,),
            ).fetchone()
            if current is None:
                continue
            result = conn.execute(
                "UPDATE projects SET task_contract_digest=?,model_package_ref_id=?,"
                "model_package_manifest_digest=?,binding_migrated_at=? "
                "WHERE model_package_ref_id IN ("
                "SELECT id FROM model_package_refs WHERE package_id=?"
                ")",
                (
                    current["task_contract_digest"],
                    current["id"],
                    current["manifest_digest"],
                    migrated_at,
                    previous_id,
                ),
            )
            updated += result.rowcount
        conn.execute(
            "UPDATE model_package_refs SET archived_at=? "
            f"WHERE package_id IN ({','.join('?' for _ in REPLACED_MODEL_PACKAGE_IDS)}) "
            "AND NOT EXISTS ("
            "SELECT 1 FROM projects WHERE model_package_ref_id=model_package_refs.id"
            ")",
            (migrated_at, *REPLACED_MODEL_PACKAGE_IDS),
        )
    return updated


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
    with sqlite_connection(database) as conn:
        rows = conn.execute(
            "SELECT p.id,p.task_id,p.dataset_view_revision_id,p.task_contract_digest,"
            "p.model_package_ref_id,p.model_package_manifest_digest,"
            "a.original_filename,a.locator_kind "
            "FROM projects p "
            "JOIN dataset_view_revisions v ON v.id=p.dataset_view_revision_id AND v.kind='single' "
            "JOIN dataset_view_members vm ON vm.dataset_view_revision_id=v.id "
            "JOIN dataset_revisions d ON d.id=vm.dataset_revision_id "
            "JOIN data_assets a ON a.id=d.data_asset_id "
            "WHERE a.locator_kind='bundled' "
            f"AND a.original_filename IN ({','.join('?' for _ in REPLACED_BUNDLED_TUTORIAL_FILENAMES)})",
            REPLACED_BUNDLED_TUTORIAL_FILENAMES,
        ).fetchall()
        conn.execute("BEGIN IMMEDIATE")
        for row in rows:
            binding = bindings.get(row["task_id"])
            if binding is None:
                continue
            desired = (binding.dataset_view_revision_id, binding.task_contract_digest)
            if (
                row["dataset_view_revision_id"] == binding.dataset_view_revision_id
                and row["task_contract_digest"] == binding.task_contract_digest
            ):
                continue
            stale_view_ids.add(row["dataset_view_revision_id"])
            conn.execute(
                "UPDATE projects SET dataset_view_revision_id=?,task_contract_digest=?,"
                "binding_migrated_at=? "
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
    with sqlite_connection(database) as conn:
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
    with sqlite_connection(database) as conn:
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
            "WHERE p.project_series_id IS NOT NULL AND s.id IS NULL LIMIT 1",
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
    with sqlite_connection(database) as conn:
        for sql, label in checks:
            row = conn.execute(sql).fetchone()
            if row is not None:
                raise WorkspaceCatalogBootstrapError(f"Project {row[0]} の{label}参照が不正です")


def bootstrap_workspace_catalog(
    database: str | Path,
    registry: TaskRegistry,
    *,
    available_packages_paths: Iterable[Path] = (AVAILABLE_PACKAGES_PATH,),
    personal_available_packages_paths: Iterable[Path] = (),
    package_origins: dict[str, str] | None = None,
    warnings: list[ModelPackageRegistrationWarning] | None = None,
) -> WorkspaceCatalog:
    catalog = WorkspaceCatalog(database)
    origins = package_origins if package_origins is not None else {}
    available_paths = tuple(
        dict.fromkeys(Path(item).resolve() for item in available_packages_paths)
    )
    personal_available_paths = tuple(
        dict.fromkeys(
            Path(item).resolve()
            for item in personal_available_packages_paths
        )
    )
    bindings = register_runtime_resources(
        catalog,
        registry,
        package_origins=origins,
        personal_model_store_paths=(
            path.parent for path in personal_available_paths
        ),
    )
    register_primary_datasets(catalog)
    personal_paths = set(personal_available_paths)
    for path in available_paths:
        is_personal = path in personal_paths
        register_available_packages(
            catalog,
            registry,
            path,
            storage_scope="personal" if is_personal else "bundled",
            package_origins=origins,
            warnings=warnings,
            strict=not is_personal,
        )
    bind_legacy_projects(database, catalog, bindings)
    migrate_replaced_model_package_projects(database)
    refresh_replaced_tutorial_projects(database, bindings)
    migrate_replaced_mpea_room_projects(database, bindings)
    archive_unreachable_stale_package_refs(database)
    audit_project_bindings(database)
    refresh_single_task_project_identities(database)
    return catalog
