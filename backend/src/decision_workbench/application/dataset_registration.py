"""Shared registration service for immutable Dataset identities."""
from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from decision_workbench.contracts.data_library_contracts import (
    DataAsset,
    DataAssetCreateInput,
    DatasetRevisionCreateInput,
    ProfileRevisionCreateInput,
)
from decision_workbench.contracts.dataset_disposition_contracts import (
    DATASET_CANONICALIZATION_CONTRACT_DIGEST,
    DatasetDisposition,
    DatasetDispositionStatus,
    disposition_digest,
)
from decision_workbench.data.file_integrity import file_sha256
from decision_workbench.data.profile_family_registry import (
    load_profile_document,
    profile_registration_metadata,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.model_lifecycle import dataset_profile_digest
from decision_workbench.persistence.workspace_catalog import (
    CatalogConflictError,
    WorkspaceCatalog,
)

CANONICAL_DATASET_CONTRACT_DIGEST = semantic_digest({"id": "canonical-dataset/v1"})
CANONICALIZATION_CONTRACT_DIGEST = DATASET_CANONICALIZATION_CONTRACT_DIGEST
EXCEL_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MANAGED_DATASET_REGISTRATION_LOCK = RLock()


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
    disposition_status: DatasetDispositionStatus
    disposition_digest: str | None
    disposition: DatasetDisposition | None

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["disposition"] = (
            self.disposition.model_dump(mode="json")
            if self.disposition is not None
            else None
        )
        return result


@dataclass(frozen=True)
class ManagedDatasetRegistrationCheckpoint:
    data_asset_ids: frozenset[str]
    profile_revision_ids: frozenset[str]
    dataset_revision_ids: frozenset[str]
    dataset_view_revision_ids: frozenset[str]


def _managed_library_destination(source: Path, library_root: Path, digest: str) -> Path:
    return (
        library_root
        / "assets"
        / digest[:2]
        / f"{digest}{source.suffix.lower()}"
    ).resolve()


def _remove_created_managed_copy(locator: Path, library_root: Path) -> None:
    """Delete only the content-addressed bytes created by the failed attempt."""

    assets_root = (library_root / "assets").resolve()
    resolved_locator = locator.resolve()
    if assets_root not in resolved_locator.parents:
        raise ValueError("managed Dataset locator leaves the managed library")
    resolved_locator.unlink(missing_ok=True)
    parent = resolved_locator.parent
    while parent == assets_root or assets_root in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def _rollback_failed_managed_registration(
    *,
    database: Path,
    source_sha256: str,
    profile_id: str,
    profile_digest: str,
    checkpoint: ManagedDatasetRegistrationCheckpoint,
    created_locator: Path | None,
    library_root: Path,
    promoted_asset: DataAsset | None,
) -> None:
    """Compensate a failed registration even when no result object exists yet."""

    catalog = WorkspaceCatalog(database)
    asset = next(
        (
            item
            for item in catalog.list_data_assets(include_archived=True)
            if item.sha256 == source_sha256
        ),
        None,
    )
    profile = next(
        (
            item
            for item in catalog.list_profile_revisions(include_archived=True)
            if item.profile_id == profile_id and item.profile_digest == profile_digest
        ),
        None,
    )
    new_dataset_ids = {
        item.id
        for item in catalog.list_dataset_revisions(include_archived=True)
        if item.id not in checkpoint.dataset_revision_ids
        and asset is not None
        and item.data_asset_id == asset.id
        and profile is not None
        and item.profile_revision_id == profile.id
    }
    new_view_ids = {
        view.id
        for view in catalog.list_dataset_view_revisions(include_archived=True)
        if view.id not in checkpoint.dataset_view_revision_ids
        and any(member.dataset_revision_id in new_dataset_ids for member in view.members)
    }
    for revision_id in new_view_ids:
        catalog.remove_unreferenced_dataset_registration(
            dataset_view_revision_id=revision_id,
        )
    for revision_id in new_dataset_ids:
        catalog.remove_unreferenced_dataset_registration(
            dataset_revision_id=revision_id,
        )
    if profile is not None and profile.id not in checkpoint.profile_revision_ids:
        catalog.remove_unreferenced_dataset_registration(
            profile_revision_id=profile.id,
        )
    if asset is not None and asset.id not in checkpoint.data_asset_ids:
        catalog.remove_unreferenced_dataset_registration(data_asset_id=asset.id)
    if promoted_asset is not None:
        catalog.restore_data_asset_locator(
            promoted_asset.id,
            locator_kind=promoted_asset.locator_kind,
            locator=promoted_asset.locator,
        )
    if created_locator is not None:
        _remove_created_managed_copy(created_locator, library_root)


def managed_dataset_registration_checkpoint(
    database: Path,
) -> ManagedDatasetRegistrationCheckpoint:
    """Capture identities that predate one compensatable registration."""

    catalog = WorkspaceCatalog(database)
    return ManagedDatasetRegistrationCheckpoint(
        data_asset_ids=frozenset(
            item.id for item in catalog.list_data_assets(include_archived=True)
        ),
        profile_revision_ids=frozenset(
            item.id for item in catalog.list_profile_revisions(include_archived=True)
        ),
        dataset_revision_ids=frozenset(
            item.id for item in catalog.list_dataset_revisions(include_archived=True)
        ),
        dataset_view_revision_ids=frozenset(
            item.id
            for item in catalog.list_dataset_view_revisions(include_archived=True)
        ),
    )


def rollback_managed_dataset_registration(
    *,
    database: Path,
    registration: DatasetRegistrationResult,
    checkpoint: ManagedDatasetRegistrationCheckpoint,
) -> None:
    """Undo only records and managed bytes introduced after ``checkpoint``."""

    catalog = WorkspaceCatalog(database)
    data_asset_id = (
        registration.data_asset_id
        if registration.data_asset_id not in checkpoint.data_asset_ids
        else None
    )
    catalog.remove_unreferenced_dataset_registration(
        dataset_view_revision_id=(
            registration.dataset_view_revision_id
            if registration.dataset_view_revision_id
            not in checkpoint.dataset_view_revision_ids
            else None
        ),
        dataset_revision_id=(
            registration.dataset_revision_id
            if registration.dataset_revision_id
            not in checkpoint.dataset_revision_ids
            else None
        ),
        profile_revision_id=(
            registration.profile_revision_id
            if registration.profile_revision_id
            not in checkpoint.profile_revision_ids
            else None
        ),
        data_asset_id=data_asset_id,
    )
    if data_asset_id:
        locator = Path(registration.locator)
        locator.unlink(missing_ok=True)
        parent = locator.parent
        while parent.name in {locator.parent.name, "assets"} and parent.exists():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


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
    disposition: DatasetDisposition | None = None,
) -> DatasetRegistrationResult:
    """Create the same content-addressed identities for startup and developer imports."""

    profile = load_profile_document(profile_path)
    metadata = profile_registration_metadata(profile)
    task_ids = metadata.task_ids
    profile_id = metadata.profile_id
    effective_profile = metadata.effective_profile
    effective_digest = dataset_profile_digest(profile_path)
    if disposition is None:
        from decision_workbench.data.profile_workbench import validate_source_profile

        report = validate_source_profile(source_path, profile_path)
        raw_disposition = report.get("disposition")
        if not isinstance(raw_disposition, dict):
            raise CatalogConflictError(
                "Profile validation did not produce a Dataset disposition"
            )
        disposition = DatasetDisposition.model_validate(raw_disposition)
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
        disposition_digest=disposition_digest(disposition),
        disposition_json=disposition,
        disposition_status="recorded",
    ))
    canonical_view_id = f"single-{dataset.id}"
    existing_view = next((
        item
        for item in catalog.list_dataset_view_revisions(include_archived=True)
        if (
            item.view_id == canonical_view_id
            and item.kind == "single"
            and len(item.members) == 1
            and item.members[0].dataset_revision_id == dataset.id
        )
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
        disposition_status=dataset.disposition_status,
        disposition_digest=dataset.disposition_digest,
        disposition=dataset.disposition_json,
    )


def _copy_to_managed_library(source: Path, library_root: Path, digest: str) -> Path:
    destination = _managed_library_destination(source, library_root, digest)
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
    promote_existing_bundled: bool = True,
) -> DatasetRegistrationResult:
    """Copy a prevalidated source and register it in a workspace catalog."""

    from decision_workbench.data.profile_workbench import validate_source_profile

    source = source.resolve()
    profile_path = profile_path.resolve()
    report = validate_source_profile(source, profile_path)
    if not report["registration_ready"]:
        raise ValueError("Profile validation found no eligible observations")
    digest = str(report["source_sha256"])
    profile_metadata = profile_registration_metadata(load_profile_document(profile_path))
    profile_digest = dataset_profile_digest(profile_path)
    managed_root = library_root.resolve()
    with _MANAGED_DATASET_REGISTRATION_LOCK:
        catalog = WorkspaceCatalog(database)
        checkpoint = managed_dataset_registration_checkpoint(database)
        existing = next(
            (
                item
                for item in catalog.list_data_assets(include_archived=True)
                if item.sha256 == digest
            ),
            None,
        )
        created_locator: Path | None = None
        promoted_asset: DataAsset | None = None
        try:
            if existing is not None and (
                existing.locator_kind == "managed" or not promote_existing_bundled
            ):
                locator = Path(existing.locator)
            else:
                destination = _managed_library_destination(source, managed_root, digest)
                destination_existed = destination.exists()
                locator = _copy_to_managed_library(source, managed_root, digest)
                if not destination_existed:
                    created_locator = locator
                if existing is not None:
                    catalog.promote_data_asset_to_managed(existing.id, str(locator))
                    promoted_asset = existing
            return register_dataset_records(
                catalog=catalog,
                source_path=source,
                source_sha256=digest,
                profile_path=profile_path,
                locator_kind="managed",
                locator=locator,
                name=name or source.stem,
                member_provenance=member_provenance,
                disposition=DatasetDisposition.model_validate(report["disposition"]),
            )
        except Exception:
            _rollback_failed_managed_registration(
                database=database,
                source_sha256=digest,
                profile_id=profile_metadata.profile_id,
                profile_digest=profile_digest,
                checkpoint=checkpoint,
                created_locator=created_locator,
                library_root=managed_root,
                promoted_asset=promoted_asset,
            )
            raise
