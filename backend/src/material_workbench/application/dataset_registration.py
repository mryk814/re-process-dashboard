"""Shared registration service for immutable Dataset identities."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
from typing import Any, Literal
from uuid import uuid4

from material_workbench.data.dataset_profile import load_dataset_profile
from material_workbench.data.file_integrity import file_sha256
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.model_lifecycle import dataset_profile_digest
from material_workbench.contracts.schemas import DataAssetCreateInput, DatasetRevisionCreateInput, ProfileRevisionCreateInput
from material_workbench.persistence.workspace_catalog import CatalogConflictError, WorkspaceCatalog


CANONICAL_DATASET_CONTRACT_DIGEST = semantic_digest({"id": "canonical-dataset/v1"})
CANONICALIZATION_CONTRACT_DIGEST = semantic_digest({"id": "workbook-canonicalizer/v1"})
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def profile_revision_number(catalog: WorkspaceCatalog, profile_id: str, profile_digest: str) -> int:
    revisions = [item for item in catalog.list_profile_revisions(include_archived=True) if item.profile_id == profile_id]
    matching = next((item for item in revisions if item.profile_digest == profile_digest), None)
    return matching.revision if matching else max((item.revision for item in revisions), default=0) + 1


@dataclass(frozen=True)
class DatasetRegistrationResult:
    data_asset_id: str
    profile_revision_id: str
    dataset_revision_id: str
    dataset_view_revision_id: str
    source_sha256: str
    locator: str
    profile_id: str
    task_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def register_dataset_records(
    *,
    catalog: WorkspaceCatalog,
    source_path: Path,
    source_sha256: str,
    profile_path: Path,
    locator_kind: Literal["managed", "bundled"],
    locator: Path,
    name: str,
    member_provenance: dict[str, Any] | None = None,
) -> DatasetRegistrationResult:
    """Create the same content-addressed identities for startup and developer imports."""

    raw_profile = __import__("json").loads(profile_path.read_text(encoding="utf-8"))
    if raw_profile.get("schema_version") == "tabular-dataset-profile/v1":
        from material_workbench.modeling.tabular_regression import load_tabular_profile

        profile = load_tabular_profile(profile_path)
        task_ids = (profile.task_id,)
        profile_id = profile.profile_id
    elif raw_profile.get("schema_version") == "observation-dataset-profile/v1":
        from material_workbench.data.observation_profile import load_observation_profile

        profile = load_observation_profile(profile_path)
        task_ids = (profile.task_id,)
        profile_id = profile.profile_id
    elif raw_profile.get("schema_version") == "welding-stage-b-profile/v1":
        from material_workbench.data.stage_b_training import load_stage_b_profile

        profile = load_stage_b_profile(profile_path)
        task_ids = (profile.task_id,)
        profile_id = profile.id
    else:
        profile = load_dataset_profile(profile_path)
        task_ids = tuple(sorted(profile.tasks))
        profile_id = profile.profile_id
    effective_profile = profile.model_dump(mode="json", exclude={"task_definitions"})
    effective_digest = dataset_profile_digest(profile_path)
    asset = catalog.upsert_data_asset(DataAssetCreateInput(
        original_filename=source_path.name,
        sha256=source_sha256,
        media_type=EXCEL_MEDIA_TYPE if source_path.suffix.lower() == ".xlsx" else "application/octet-stream",
        locator_kind=locator_kind,
        locator=str(locator.resolve()),
    ))
    profile_revision = next((
        item
        for item in catalog.list_profile_revisions()
        if item.profile_id == profile_id and item.profile_digest == effective_digest
    ), None)
    if profile_revision is None:
        profile_revision = catalog.upsert_profile_revision(ProfileRevisionCreateInput(
            profile_id=profile_id,
            revision=profile_revision_number(catalog, profile_id, effective_digest),
            name=profile_id,
            profile_digest=effective_digest,
            canonical_contract_digest=CANONICAL_DATASET_CONTRACT_DIGEST,
            effective_profile_json=effective_profile,
        ))
    dataset = catalog.upsert_dataset_revision(DatasetRevisionCreateInput(
        data_asset_id=asset.id,
        profile_revision_id=profile_revision.id,
        canonicalization_contract_digest=CANONICALIZATION_CONTRACT_DIGEST,
    ))
    existing_view = next((
        item
        for item in catalog.list_dataset_view_revisions(include_archived=True)
        if item.kind == "single"
        and len(item.members) == 1
        and item.members[0].dataset_revision_id == dataset.id
    ), None)
    if existing_view is not None:
        # The Dataset identity is content-addressed. Startup may encounter an
        # already imported personal Dataset through a runtime; preserve the
        # first registration's name and lineage instead of rewriting history.
        view = existing_view
    elif dataset.archived_at is None:
        view = catalog.ensure_single_dataset_view(
            dataset.id,
            name=name,
            member_provenance=member_provenance,
        )
    else:
        raise CatalogConflictError(
            f"利用停止中のDatasetに対応するDataset Viewが見つかりません: {dataset.id}"
        )
    return DatasetRegistrationResult(
        data_asset_id=asset.id,
        profile_revision_id=profile_revision.id,
        dataset_revision_id=dataset.id,
        dataset_view_revision_id=view.id,
        source_sha256=source_sha256,
        locator=str(locator.resolve()),
        profile_id=profile_id,
        task_ids=task_ids,
    )


def _copy_to_managed_library(source: Path, library_root: Path, digest: str) -> Path:
    destination = (library_root / "assets" / digest[:2] / f"{digest}{source.suffix.lower()}").resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if file_sha256(destination) != digest:
            raise CatalogConflictError(f"managed libraryの既存ファイルがSHA-256と一致しません: {destination}")
        return destination
    staging = destination.with_name(f".{destination.name}.{uuid4().hex}.partial")
    try:
        shutil.copyfile(source, staging)
        if file_sha256(staging) != digest:
            raise OSError("managed libraryへのコピー後にSHA-256が一致しません")
        staging.replace(destination)
    finally:
        staging.unlink(missing_ok=True)
    return destination


def register_managed_dataset(
    *,
    database: Path,
    source: Path,
    library_root: Path,
    profile_path: Path,
    name: str | None = None,
    member_provenance: dict[str, Any] | None = None,
) -> DatasetRegistrationResult:
    """Copy a prevalidated source and register it in a workspace catalog."""

    from material_workbench.data.profile_workbench import validate_source_profile

    source = source.resolve()
    profile_path = profile_path.resolve()
    report = validate_source_profile(source, profile_path)
    if not report["registration_ready"]:
        raise ValueError("Profile validation found no eligible observations")
    digest = str(report["source_sha256"])
    catalog = WorkspaceCatalog(database)
    existing = next((item for item in catalog.list_data_assets(include_archived=True) if item.sha256 == digest), None)
    if existing is not None and existing.locator_kind == "managed":
        locator = Path(existing.locator)
    else:
        locator = _copy_to_managed_library(source, library_root.resolve(), digest)
        if existing is not None:
            catalog.promote_data_asset_to_managed(existing.id, str(locator))
    return register_dataset_records(
        catalog=catalog,
        source_path=source,
        source_sha256=digest,
        profile_path=profile_path,
        locator_kind="managed",
        locator=locator,
        name=name or source.stem,
        member_provenance=member_provenance,
    )
