"""Repository for immutable Data Library identities.

Catalog records are content-addressed and append-only.  ``upsert`` means
"ensure this exact immutable record exists"; it never rewrites an existing
revision.  Archiving only changes visibility and preserves historical
references.
"""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from decision_workbench.contracts.data_library_contracts import (
    DataAsset,
    DataAssetCreateInput,
    DatasetRevision,
    DatasetRevisionCreateInput,
    DatasetViewMember,
    DatasetViewMemberInput,
    DatasetViewRevision,
    DatasetViewRevisionCreateInput,
    ModelPackageRef,
    ModelPackageRefCreateInput,
    ProfileRevision,
    ProfileRevisionCreateInput,
    ProjectSeries,
    ProjectSeriesCreateInput,
    ProjectSeriesUpdateInput,
)
from decision_workbench.persistence.workspace_catalog_migration import migrate_workspace_catalog
from decision_workbench.persistence.workspace_maintenance_migration import (
    migrate_workspace_maintenance_events,
)
from decision_workbench.persistence.sqlite_connection import (
    sqlite_connection,
)


class CatalogConflictError(ValueError):
    """A logical revision already exists with different immutable content."""


class CatalogReferenceError(ValueError):
    """A referenced catalog record is missing or archived."""


class CatalogIntegrityError(RuntimeError):
    """Persisted catalog data cannot be decoded according to its contract."""


CatalogModel = TypeVar("CatalogModel", bound=BaseModel)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical_json(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(namespace: str, value: Any) -> str:
    encoded = f"{namespace}\0{_canonical_json(value)}".encode("utf-8")
    return sha256(encoded).hexdigest()


def _content_id(prefix: str, digest: str) -> str:
    return f"{prefix}-{digest[:24]}"


def _archived_at(archived: bool) -> str | None:
    return _now() if archived else None


def _loads_object(raw: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CatalogIntegrityError(f"{label} JSONを読み取れません") from exc
    if not isinstance(value, dict):
        raise CatalogIntegrityError(f"{label} JSONはobjectである必要があります")
    return value


def model_package_reference_labels(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
) -> list[str]:
    """Return every durable evidence location that pins this Package identity."""

    labels = [
        f"Project: {project['name']}"
        for project in conn.execute(
            "SELECT name FROM projects WHERE model_package_ref_id=? ORDER BY name",
            (row["id"],),
        )
    ]
    digest = str(row["manifest_digest"]).removeprefix("sha256:")
    needles = (digest, f"sha256:{digest}")
    excluded = {
        "model_package_refs",
        "workspace_maintenance_events",
        "schema_migrations",
    }
    tables = [
        str(item["name"])
        for item in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        if not str(item["name"]).startswith("sqlite_")
        and str(item["name"]) not in excluded
    ]
    for table in tables:
        escaped_table = table.replace('"', '""')
        columns = [
            str(item["name"])
            for item in conn.execute(f'PRAGMA table_info("{escaped_table}")')
            if "TEXT" in str(item["type"]).upper()
        ]
        for column in columns:
            escaped_column = column.replace('"', '""')
            evidence = conn.execute(
                f'SELECT rowid FROM "{escaped_table}" '
                f'WHERE instr(COALESCE("{escaped_column}",\'\'),?) > 0 '
                f'OR instr(COALESCE("{escaped_column}",\'\'),?) > 0 LIMIT 1',
                needles,
            ).fetchone()
            if evidence is not None:
                labels.append(f"Evidence: {table}.{column} rowid={evidence['rowid']}")
    return labels


class WorkspaceCatalog:
    """SQLite-backed repository for Data Library catalog records."""

    def __init__(self, database: str | Path) -> None:
        self.path = str(database)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        migrate_workspace_catalog(self.path)
        migrate_workspace_maintenance_events(self.path)

    def _connect(self):
        return sqlite_connection(self.path)

    @staticmethod
    def _asset(row: sqlite3.Row) -> DataAsset:
        return DataAsset(**dict(row))

    @staticmethod
    def _profile(row: sqlite3.Row) -> ProfileRevision:
        values = dict(row)
        values["effective_profile_json"] = _loads_object(
            values["effective_profile_json"], label=f"Profile Revision {values['id']}"
        )
        return ProfileRevision(**values)

    @staticmethod
    def _dataset(row: sqlite3.Row) -> DatasetRevision:
        return DatasetRevision(**dict(row))

    @staticmethod
    def _package(row: sqlite3.Row) -> ModelPackageRef:
        values = dict(row)
        values["manifest_json"] = _loads_object(
            values["manifest_json"], label=f"Model Package Ref {values['id']}"
        )
        return ModelPackageRef(**values)

    @staticmethod
    def _series(row: sqlite3.Row) -> ProjectSeries:
        return ProjectSeries(**dict(row))

    @staticmethod
    def _active_clause(include_archived: bool) -> str:
        return "" if include_archived else " AND archived_at IS NULL"

    def upsert_data_asset(self, payload: DataAssetCreateInput) -> DataAsset:
        asset_id = _content_id("data-asset", payload.sha256)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM data_assets WHERE sha256=?", (payload.sha256,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO data_assets(id,sha256,original_filename,media_type,locator_kind,locator,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (asset_id, payload.sha256, payload.original_filename, payload.media_type,
                     payload.locator_kind, payload.locator, _now()),
                )
                row = conn.execute("SELECT * FROM data_assets WHERE id=?", (asset_id,)).fetchone()
            elif (
                row["locator_kind"] == "bundled"
                and payload.locator_kind == "bundled"
                and row["locator"] != payload.locator
            ):
                # A portable installation may be moved without changing the
                # immutable asset identity. Managed copies remain authoritative.
                conn.execute(
                    "UPDATE data_assets SET locator=? WHERE id=?",
                    (payload.locator, row["id"]),
                )
                row = conn.execute("SELECT * FROM data_assets WHERE id=?", (row["id"],)).fetchone()
        assert row is not None
        return self._asset(row)

    def get_data_asset(self, asset_id: str, *, include_archived: bool = False) -> DataAsset | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM data_assets WHERE id=?{self._active_clause(include_archived)}", (asset_id,)
            ).fetchone()
        return self._asset(row) if row else None

    def list_data_assets(self, *, include_archived: bool = False) -> list[DataAsset]:
        where = "" if include_archived else " WHERE archived_at IS NULL"
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM data_assets{where} ORDER BY created_at,id").fetchall()
        return [self._asset(row) for row in rows]

    def archive_data_asset(self, asset_id: str, *, archived: bool = True) -> DataAsset | None:
        return self._set_archived("data_assets", asset_id, archived, self._asset)

    def promote_data_asset_to_managed(self, asset_id: str, locator: str) -> DataAsset:
        """Move an operational locator to an identical managed copy without changing identity."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM data_assets WHERE id=?", (asset_id,)).fetchone()
            if row is None:
                raise CatalogReferenceError(f"Data Assetが見つかりません: {asset_id}")
            if row["locator_kind"] == "managed":
                return self._asset(row)
            conn.execute(
                "UPDATE data_assets SET locator_kind='managed', locator=? WHERE id=?",
                (locator, asset_id),
            )
            row = conn.execute("SELECT * FROM data_assets WHERE id=?", (asset_id,)).fetchone()
        assert row is not None
        return self._asset(row)

    def restore_data_asset_locator(
        self,
        asset_id: str,
        *,
        locator_kind: Literal["managed", "bundled"],
        locator: str,
    ) -> DataAsset:
        """Restore a pre-registration locator after a failed managed promotion."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM data_assets WHERE id=?", (asset_id,)).fetchone()
            if row is None:
                raise CatalogReferenceError(f"Data Assetが見つかりません: {asset_id}")
            conn.execute(
                "UPDATE data_assets SET locator_kind=?, locator=? WHERE id=?",
                (locator_kind, locator, asset_id),
            )
            row = conn.execute("SELECT * FROM data_assets WHERE id=?", (asset_id,)).fetchone()
        assert row is not None
        return self._asset(row)

    def upsert_profile_revision(self, payload: ProfileRevisionCreateInput) -> ProfileRevision:
        effective_json = _canonical_json(payload.effective_profile_json)
        identity_digest = _digest("profile-revision-id-v1", payload.profile_digest)
        profile_id = _content_id("profile-revision", identity_digest)
        immutable = (
            payload.profile_id, payload.revision, payload.name, payload.profile_digest,
            payload.canonical_contract_digest, effective_json,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM dataset_profile_revisions WHERE profile_digest=? OR (profile_id=? AND revision=?)",
                (payload.profile_digest, payload.profile_id, payload.revision),
            ).fetchone()
            if row is not None:
                stored = tuple(row[key] for key in (
                    "profile_id", "revision", "name", "profile_digest",
                    "canonical_contract_digest", "effective_profile_json",
                ))
                if stored != immutable:
                    raise CatalogConflictError(
                        f"Profile {payload.profile_id} revision {payload.revision} は別内容で登録済みです"
                    )
            else:
                conn.execute(
                    "INSERT INTO dataset_profile_revisions(id,profile_id,revision,name,profile_digest,"
                    "canonical_contract_digest,effective_profile_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (profile_id, *immutable, _now()),
                )
                row = conn.execute("SELECT * FROM dataset_profile_revisions WHERE id=?", (profile_id,)).fetchone()
        assert row is not None
        return self._profile(row)

    def get_profile_revision(self, revision_id: str, *, include_archived: bool = False) -> ProfileRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM dataset_profile_revisions WHERE id=?{self._active_clause(include_archived)}",
                (revision_id,),
            ).fetchone()
        return self._profile(row) if row else None

    def list_profile_revisions(self, *, include_archived: bool = False) -> list[ProfileRevision]:
        where = "" if include_archived else " WHERE archived_at IS NULL"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM dataset_profile_revisions{where} ORDER BY profile_id,revision,id"
            ).fetchall()
        return [self._profile(row) for row in rows]

    def archive_profile_revision(self, revision_id: str, *, archived: bool = True) -> ProfileRevision | None:
        return self._set_archived("dataset_profile_revisions", revision_id, archived, self._profile)

    def upsert_dataset_revision(self, payload: DatasetRevisionCreateInput) -> DatasetRevision:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            asset = conn.execute(
                "SELECT sha256 FROM data_assets WHERE id=? AND archived_at IS NULL", (payload.data_asset_id,)
            ).fetchone()
            profile = conn.execute(
                "SELECT profile_digest FROM dataset_profile_revisions WHERE id=? AND archived_at IS NULL",
                (payload.profile_revision_id,),
            ).fetchone()
            if asset is None:
                raise CatalogReferenceError(f"利用可能なData Assetが見つかりません: {payload.data_asset_id}")
            if profile is None:
                raise CatalogReferenceError(
                    f"利用可能なProfile Revisionが見つかりません: {payload.profile_revision_id}"
                )
            identity = {
                "data_asset_sha256": asset["sha256"],
                "profile_digest": profile["profile_digest"],
                "canonicalization_contract_digest": payload.canonicalization_contract_digest,
            }
            dataset_digest = _digest("dataset-revision-v1", identity)
            revision_id = _content_id("dataset-revision", dataset_digest)
            row = conn.execute(
                "SELECT * FROM dataset_revisions WHERE dataset_digest=? OR "
                "(data_asset_id=? AND profile_revision_id=? AND canonicalization_contract_digest=?)",
                (dataset_digest, payload.data_asset_id, payload.profile_revision_id,
                 payload.canonicalization_contract_digest),
            ).fetchone()
            if row is not None:
                stored = (
                    row["data_asset_id"], row["profile_revision_id"],
                    row["canonicalization_contract_digest"], row["dataset_digest"],
                )
                expected = (
                    payload.data_asset_id, payload.profile_revision_id,
                    payload.canonicalization_contract_digest, dataset_digest,
                )
                if stored != expected:
                    raise CatalogConflictError("Dataset Revisionのidentityが既存レコードと一致しません")
            else:
                conn.execute(
                    "INSERT INTO dataset_revisions(id,data_asset_id,profile_revision_id,"
                    "canonicalization_contract_digest,dataset_digest,created_at) VALUES (?,?,?,?,?,?)",
                    (revision_id, payload.data_asset_id, payload.profile_revision_id,
                     payload.canonicalization_contract_digest, dataset_digest, _now()),
                )
                row = conn.execute("SELECT * FROM dataset_revisions WHERE id=?", (revision_id,)).fetchone()
        assert row is not None
        return self._dataset(row)

    def get_dataset_revision(self, revision_id: str, *, include_archived: bool = False) -> DatasetRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM dataset_revisions WHERE id=?{self._active_clause(include_archived)}",
                (revision_id,),
            ).fetchone()
        return self._dataset(row) if row else None

    def list_dataset_revisions(self, *, include_archived: bool = False) -> list[DatasetRevision]:
        where = "" if include_archived else " WHERE archived_at IS NULL"
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM dataset_revisions{where} ORDER BY created_at,id").fetchall()
        return [self._dataset(row) for row in rows]

    def archive_dataset_revision(self, revision_id: str, *, archived: bool = True) -> DatasetRevision | None:
        return self._set_archived("dataset_revisions", revision_id, archived, self._dataset)

    def set_dataset_revision_availability(
        self, revision_id: str, *, archived: bool
    ) -> DatasetRevision | None:
        """Change Dataset availability atomically with the Project reference guard."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM dataset_revisions WHERE id=?", (revision_id,)
            ).fetchone()
            if row is None:
                return None
            if archived:
                projects = [
                    project["name"]
                    for project in conn.execute(
                        "SELECT DISTINCT p.name FROM projects p "
                        "JOIN dataset_view_members vm "
                        "ON vm.dataset_view_revision_id=p.dataset_view_revision_id "
                        "WHERE vm.dataset_revision_id=? ORDER BY p.name",
                        (revision_id,),
                    )
                ]
                if projects:
                    raise CatalogReferenceError(
                        f"参照中のプロジェクトがあるため利用停止できません: {', '.join(projects)}"
                    )
            if (row["archived_at"] is not None) != archived:
                conn.execute(
                    "UPDATE dataset_revisions SET archived_at=? WHERE id=?",
                    (_archived_at(archived), revision_id),
                )
                row = conn.execute(
                    "SELECT * FROM dataset_revisions WHERE id=?", (revision_id,)
                ).fetchone()
            return self._dataset(row)

    def _view(self, conn: sqlite3.Connection, row: sqlite3.Row) -> DatasetViewRevision:
        members = [
            DatasetViewMember(
                dataset_view_revision_id=member["dataset_view_revision_id"],
                dataset_revision_id=member["dataset_revision_id"],
                ordinal=member["ordinal"],
                cohort_key=member["cohort_key"],
                cohort_label=member["cohort_label"],
                provenance_json=_loads_object(
                    member["provenance_json"],
                    label=f"Dataset View member {member['dataset_view_revision_id']}:{member['ordinal']}",
                ),
            )
            for member in conn.execute(
                "SELECT * FROM dataset_view_members WHERE dataset_view_revision_id=? ORDER BY ordinal",
                (row["id"],),
            )
        ]
        return DatasetViewRevision(members=members, **dict(row))

    def upsert_dataset_view_revision(self, payload: DatasetViewRevisionCreateInput) -> DatasetViewRevision:
        ordered_members = sorted(payload.members, key=lambda member: member.ordinal)
        member_values = [
            {
                "dataset_revision_id": member.dataset_revision_id,
                "ordinal": member.ordinal,
                "cohort_key": member.cohort_key,
                "cohort_label": member.cohort_label,
                "provenance_json": member.provenance_json,
            }
            for member in ordered_members
        ]
        identity = {
            "view_id": payload.view_id,
            "revision": payload.revision,
            "name": payload.name,
            "kind": payload.kind,
            "members": member_values,
        }
        view_digest = _digest("dataset-view-revision-v1", identity)
        revision_id = _content_id("dataset-view-revision", view_digest)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            active_ids = {
                row["id"]
                for row in conn.execute(
                    f"SELECT id FROM dataset_revisions WHERE id IN ({','.join('?' for _ in ordered_members)}) "
                    "AND archived_at IS NULL",
                    tuple(member.dataset_revision_id for member in ordered_members),
                )
            }
            missing = [member.dataset_revision_id for member in ordered_members if member.dataset_revision_id not in active_ids]
            if missing:
                raise CatalogReferenceError(f"利用可能なDataset Revisionが見つかりません: {', '.join(missing)}")
            row = conn.execute(
                "SELECT * FROM dataset_view_revisions WHERE view_digest=? OR (view_id=? AND revision=?)",
                (view_digest, payload.view_id, payload.revision),
            ).fetchone()
            if row is not None:
                existing = self._view(conn, row)
                if existing.view_digest != view_digest:
                    raise CatalogConflictError(
                        f"Dataset View {payload.view_id} revision {payload.revision} は別内容で登録済みです"
                    )
                return existing
            conn.execute(
                "INSERT INTO dataset_view_revisions(id,view_id,revision,name,kind,view_digest,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (revision_id, payload.view_id, payload.revision, payload.name, payload.kind, view_digest, _now()),
            )
            conn.executemany(
                "INSERT INTO dataset_view_members(dataset_view_revision_id,dataset_revision_id,ordinal,"
                "cohort_key,cohort_label,provenance_json) VALUES (?,?,?,?,?,?)",
                [
                    (revision_id, member.dataset_revision_id, member.ordinal, member.cohort_key,
                     member.cohort_label, _canonical_json(member.provenance_json))
                    for member in ordered_members
                ],
            )
            row = conn.execute("SELECT * FROM dataset_view_revisions WHERE id=?", (revision_id,)).fetchone()
            assert row is not None
            return self._view(conn, row)

    def ensure_single_dataset_view(
        self,
        dataset_revision_id: str,
        *,
        name: str,
        view_id: str | None = None,
        member_provenance: dict[str, Any] | None = None,
    ) -> DatasetViewRevision:
        logical_id = view_id or f"single-{dataset_revision_id}"
        return self.upsert_dataset_view_revision(
            DatasetViewRevisionCreateInput(
                view_id=logical_id,
                revision=1,
                name=name,
                kind="single",
                members=[
                    DatasetViewMemberInput(
                        dataset_revision_id=dataset_revision_id,
                        ordinal=0,
                        provenance_json=member_provenance or {},
                    )
                ],
            )
        )

    def get_dataset_view_revision(
        self, revision_id: str, *, include_archived: bool = False
    ) -> DatasetViewRevision | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM dataset_view_revisions WHERE id=?{self._active_clause(include_archived)}",
                (revision_id,),
            ).fetchone()
            return self._view(conn, row) if row else None

    def list_dataset_view_revisions(self, *, include_archived: bool = False) -> list[DatasetViewRevision]:
        where = "" if include_archived else " WHERE archived_at IS NULL"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM dataset_view_revisions{where} ORDER BY view_id,revision,id"
            ).fetchall()
            return [self._view(conn, row) for row in rows]

    def archive_dataset_view_revision(
        self, revision_id: str, *, archived: bool = True
    ) -> DatasetViewRevision | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM dataset_view_revisions WHERE id=?", (revision_id,)).fetchone()
            if row is None:
                return None
            if (row["archived_at"] is not None) != archived:
                conn.execute(
                    "UPDATE dataset_view_revisions SET archived_at=? WHERE id=?",
                    (_archived_at(archived), revision_id),
                )
                row = conn.execute("SELECT * FROM dataset_view_revisions WHERE id=?", (revision_id,)).fetchone()
            return self._view(conn, row)

    def remove_unreferenced_dataset_registration(
        self,
        *,
        data_asset_id: str | None = None,
        profile_revision_id: str | None = None,
        dataset_revision_id: str | None = None,
        dataset_view_revision_id: str | None = None,
    ) -> None:
        """Remove a just-created registration before any Project can bind it.

        Catalog identities are append-only during normal operation.  This narrow
        compensating operation exists for an onboarding transaction that has not
        reached its runtime generation swap.  It refuses to erase any record
        referenced by a Project or a remaining Dataset View.
        """

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if dataset_view_revision_id:
                project = conn.execute(
                    "SELECT name FROM projects WHERE dataset_view_revision_id=? LIMIT 1",
                    (dataset_view_revision_id,),
                ).fetchone()
                if project is not None:
                    raise CatalogReferenceError(
                        "Projectが参照しているDataset Viewは取り消せません"
                    )
                conn.execute(
                    "DELETE FROM dataset_view_members WHERE dataset_view_revision_id=?",
                    (dataset_view_revision_id,),
                )
                conn.execute(
                    "DELETE FROM dataset_view_revisions WHERE id=?",
                    (dataset_view_revision_id,),
                )
            if dataset_revision_id:
                member = conn.execute(
                    "SELECT dataset_view_revision_id FROM dataset_view_members "
                    "WHERE dataset_revision_id=? LIMIT 1",
                    (dataset_revision_id,),
                ).fetchone()
                if member is not None:
                    raise CatalogReferenceError(
                        "Dataset Viewが参照しているDatasetは取り消せません"
                    )
                conn.execute(
                    "DELETE FROM dataset_revisions WHERE id=?",
                    (dataset_revision_id,),
                )
            if profile_revision_id:
                dataset = conn.execute(
                    "SELECT id FROM dataset_revisions WHERE profile_revision_id=? LIMIT 1",
                    (profile_revision_id,),
                ).fetchone()
                if dataset is not None:
                    raise CatalogReferenceError(
                        "Datasetが参照しているProfileは取り消せません"
                    )
                conn.execute(
                    "DELETE FROM dataset_profile_revisions WHERE id=?",
                    (profile_revision_id,),
                )
            if data_asset_id:
                dataset = conn.execute(
                    "SELECT id FROM dataset_revisions WHERE data_asset_id=? LIMIT 1",
                    (data_asset_id,),
                ).fetchone()
                if dataset is not None:
                    raise CatalogReferenceError(
                        "Datasetが参照しているData Assetは取り消せません"
                    )
                conn.execute("DELETE FROM data_assets WHERE id=?", (data_asset_id,))

    def upsert_model_package_ref(self, payload: ModelPackageRefCreateInput) -> ModelPackageRef:
        manifest_json = _canonical_json(payload.manifest_json)
        identity_digest = _digest(
            "model-package-ref-v1", {"package_id": payload.package_id, "manifest_digest": payload.manifest_digest}
        )
        reference_id = _content_id("model-package-ref", identity_digest)
        immutable = (
            payload.package_id, payload.task_id, payload.task_contract_digest, payload.manifest_digest,
            payload.locator, manifest_json,
        )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM model_package_refs WHERE package_id=? AND manifest_digest=?",
                (payload.package_id, payload.manifest_digest),
            ).fetchone()
            if row is not None:
                stored = tuple(row[key] for key in (
                    "package_id", "task_id", "task_contract_digest", "manifest_digest", "locator", "manifest_json"
                ))
                stored_without_locator = (
                    row["package_id"],
                    row["task_id"],
                    row["task_contract_digest"],
                    row["manifest_digest"],
                    row["manifest_json"],
                )
                immutable_without_locator = (
                    payload.package_id,
                    payload.task_id,
                    payload.task_contract_digest,
                    payload.manifest_digest,
                    manifest_json,
                )
                if stored_without_locator != immutable_without_locator:
                    latest_maintenance = conn.execute(
                        "SELECT id,operation FROM workspace_maintenance_events "
                        "WHERE resource_kind='model_package_ref' "
                        "AND resource_id=? "
                        "ORDER BY created_at DESC,id DESC LIMIT 1",
                        (row["id"],),
                    ).fetchone()
                    references = model_package_reference_labels(conn, row)
                    if (
                        row["archived_at"] is None
                        or latest_maintenance is None
                        or latest_maintenance["operation"] != "deactivate"
                        or references
                    ):
                        raise CatalogConflictError(
                            f"Model Package {payload.package_id} ({payload.manifest_digest}) は別内容で登録済みです"
                        )
                    previous = {
                        key: row[key]
                        for key in (
                            "package_id",
                            "task_id",
                            "task_contract_digest",
                            "manifest_digest",
                            "locator",
                            "manifest_json",
                            "archived_at",
                        )
                    }
                    conn.execute(
                        "UPDATE model_package_refs SET task_id=?,task_contract_digest=?,"
                        "locator=?,manifest_json=?,archived_at=NULL WHERE id=?",
                        (
                            payload.task_id,
                            payload.task_contract_digest,
                            payload.locator,
                            manifest_json,
                            row["id"],
                        ),
                    )
                    conn.execute(
                        "INSERT INTO workspace_maintenance_events("
                        "id,operation,resource_kind,resource_id,reason,detail_json,created_at"
                        ") VALUES (?,?,?,?,?,?,?)",
                        (
                            f"maintenance-{uuid4()}",
                            "reactivate-current-contract",
                            "model_package_ref",
                            row["id"],
                            "明示的に利用停止された未参照登録を現行contractで再登録",
                            _canonical_json(
                                {
                                    "previous": previous,
                                    "current": {
                                        "task_id": payload.task_id,
                                        "task_contract_digest": payload.task_contract_digest,
                                        "manifest_json": manifest_json,
                                        "locator": payload.locator,
                                    },
                                    "deactivation_event_id": latest_maintenance["id"],
                                }
                            ),
                            _now(),
                        ),
                    )
                    row = conn.execute(
                        "SELECT * FROM model_package_refs WHERE id=?",
                        (row["id"],),
                    ).fetchone()
                    stored = immutable
                if stored != immutable:
                    # Package identity is fixed by package_id + manifest digest.
                    # Rebind only the operational location when a portable
                    # installation has moved.
                    conn.execute(
                        "UPDATE model_package_refs SET locator=? WHERE id=?",
                        (payload.locator, row["id"]),
                    )
                    row = conn.execute(
                        "SELECT * FROM model_package_refs WHERE id=?",
                        (row["id"],),
                    ).fetchone()
            else:
                conn.execute(
                    "INSERT INTO model_package_refs(id,package_id,task_id,task_contract_digest,manifest_digest,"
                    "locator,manifest_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (reference_id, *immutable, _now()),
                )
                row = conn.execute("SELECT * FROM model_package_refs WHERE id=?", (reference_id,)).fetchone()
        assert row is not None
        return self._package(row)

    def get_model_package_ref(self, reference_id: str, *, include_archived: bool = False) -> ModelPackageRef | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM model_package_refs WHERE id=?{self._active_clause(include_archived)}",
                (reference_id,),
            ).fetchone()
        return self._package(row) if row else None

    def list_model_package_refs(self, *, include_archived: bool = False) -> list[ModelPackageRef]:
        where = "" if include_archived else " WHERE archived_at IS NULL"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM model_package_refs{where} ORDER BY task_id,package_id,created_at,id"
            ).fetchall()
        return [self._package(row) for row in rows]

    def archive_model_package_ref(
        self, reference_id: str, *, archived: bool = True
    ) -> ModelPackageRef | None:
        return self._set_archived("model_package_refs", reference_id, archived, self._package)

    def set_model_package_ref_availability(
        self, reference_id: str, *, archived: bool
    ) -> ModelPackageRef | None:
        """Change Package availability atomically with the Project reference guard."""

        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM model_package_refs WHERE id=?", (reference_id,)
            ).fetchone()
            if row is None:
                return None
            if archived:
                projects = [
                    project["name"]
                    for project in conn.execute(
                        "SELECT name FROM projects WHERE model_package_ref_id=? ORDER BY name",
                        (reference_id,),
                    )
                ]
                if projects:
                    raise CatalogReferenceError(
                        f"参照中のプロジェクトがあるため利用停止できません: {', '.join(projects)}"
                    )
            if (row["archived_at"] is not None) != archived:
                conn.execute(
                    "UPDATE model_package_refs SET archived_at=? WHERE id=?",
                    (_archived_at(archived), reference_id),
                )
                row = conn.execute(
                    "SELECT * FROM model_package_refs WHERE id=?", (reference_id,)
                ).fetchone()
            return self._package(row)

    def deactivate_model_package_ref_for_maintenance(
        self,
        reference_id: str,
        *,
        reason: str,
    ) -> ModelPackageRef | None:
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("利用停止の理由を入力してください")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM model_package_refs WHERE id=?",
                (reference_id,),
            ).fetchone()
            if row is None:
                return None
            references = model_package_reference_labels(conn, row)
            if references:
                raise CatalogReferenceError(
                    "保存済み証拠から参照されているため利用停止できません: "
                    + ", ".join(references)
                )
            if row["archived_at"] is None:
                conn.execute(
                    "UPDATE model_package_refs SET archived_at=? WHERE id=?",
                    (_now(), reference_id),
                )
            conn.execute(
                "INSERT INTO workspace_maintenance_events("
                "id,operation,resource_kind,resource_id,reason,detail_json,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    f"maintenance-{uuid4()}",
                    "deactivate",
                    "model_package_ref",
                    reference_id,
                    normalized_reason,
                    _canonical_json(
                        {
                            key: row[key]
                            for key in (
                                "package_id",
                                "task_id",
                                "task_contract_digest",
                                "manifest_digest",
                                "locator",
                                "manifest_json",
                                "created_at",
                                "archived_at",
                            )
                        }
                    ),
                    _now(),
                ),
            )
            updated = conn.execute(
                "SELECT * FROM model_package_refs WHERE id=?",
                (reference_id,),
            ).fetchone()
            assert updated is not None
            return self._package(updated)

    def list_workspace_maintenance_events(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM workspace_maintenance_events "
                "ORDER BY created_at,id"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "operation": row["operation"],
                "resource_kind": row["resource_kind"],
                "resource_id": row["resource_id"],
                "reason": row["reason"],
                "detail": _loads_object(
                    row["detail_json"],
                    label="Workspace maintenance event",
                ),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def create_project_series(self, payload: ProjectSeriesCreateInput) -> ProjectSeries:
        return self.ensure_project_series(f"project-series-{uuid4()}", payload)

    def ensure_project_series(self, series_id: str, payload: ProjectSeriesCreateInput) -> ProjectSeries:
        """Ensure a caller-owned stable Series identity, primarily for bootstrap.

        Ordinary creation uses a random identity because two series may have the
        same display name and description without representing the same lineage.
        """
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM project_series WHERE id=?", (series_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO project_series(id,name,description,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (series_id, payload.name, payload.description, now, now),
                )
                row = conn.execute("SELECT * FROM project_series WHERE id=?", (series_id,)).fetchone()
            elif (row["name"], row["description"]) != (payload.name, payload.description):
                raise CatalogConflictError(f"Project Series {series_id} は別内容で登録済みです")
        assert row is not None
        return self._series(row)

    def get_project_series(self, series_id: str, *, include_archived: bool = False) -> ProjectSeries | None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM project_series WHERE id=?{self._active_clause(include_archived)}", (series_id,)
            ).fetchone()
        return self._series(row) if row else None

    def list_project_series(self, *, include_archived: bool = False) -> list[ProjectSeries]:
        where = "" if include_archived else " WHERE archived_at IS NULL"
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM project_series{where} ORDER BY created_at,id").fetchall()
        return [self._series(row) for row in rows]

    def update_project_series(self, series_id: str, payload: ProjectSeriesUpdateInput) -> ProjectSeries | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            result = conn.execute(
                "UPDATE project_series SET name=?,description=?,updated_at=?,archived_at=? WHERE id=?",
                (payload.name, payload.description, _now(), _archived_at(payload.archived), series_id),
            )
            row = conn.execute("SELECT * FROM project_series WHERE id=?", (series_id,)).fetchone()
        return self._series(row) if result.rowcount and row else None

    def archive_project_series(self, series_id: str, *, archived: bool = True) -> ProjectSeries | None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM project_series WHERE id=?", (series_id,)).fetchone()
            if row is None:
                return None
            if (row["archived_at"] is not None) != archived:
                now = _now()
                conn.execute(
                    "UPDATE project_series SET archived_at=?,updated_at=? WHERE id=?",
                    (_archived_at(archived), now, series_id),
                )
                row = conn.execute("SELECT * FROM project_series WHERE id=?", (series_id,)).fetchone()
        return self._series(row)

    def _set_archived(
        self,
        table: str,
        record_id: str,
        archived: bool,
        parser: Any,
    ) -> Any | None:
        allowed = {
            "data_assets", "dataset_profile_revisions", "dataset_revisions", "model_package_refs"
        }
        if table not in allowed:
            raise ValueError(f"archive対象tableが不正です: {table}")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
            if row is None:
                return None
            is_archived = row["archived_at"] is not None
            if is_archived != archived:
                conn.execute(
                    f"UPDATE {table} SET archived_at=? WHERE id=?", (_archived_at(archived), record_id)
                )
                row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        return parser(row)
