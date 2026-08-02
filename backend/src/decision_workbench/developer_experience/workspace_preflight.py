from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from decision_workbench.contracts.chain_contracts import ProjectScientificIdentity
from decision_workbench.contracts.task_contracts import (
    TaskContractFixture,
    persisted_task_definition_payload,
)
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.model_lifecycle import (
    ACTIVE_PACKAGES_PATH,
    load_active_packages,
    resolve_configured_package,
    validate_active_package_task_set,
)
from decision_workbench.modeling.packages.contracts import (
    ModelPackageManifest,
    PackageContractError,
)
from decision_workbench.persistence.workspace_catalog import (
    model_package_reference_labels,
)
from decision_workbench.task_composition.catalog import registered_task_modules
from decision_workbench.tasks.task_registry import TaskRegistryError, load_task_contracts


_PROJECT_IDENTITY_ADAPTER = TypeAdapter(ProjectScientificIdentity)


class WorkspacePreflightPackage(Protocol):
    manifest: ModelPackageManifest

    @property
    def manifest_sha256(self) -> str: ...


class WorkspacePreflightEntryLike(Protocol):
    model_package: WorkspacePreflightPackage


class WorkspacePreflightRegistry(Protocol):
    @property
    def available_task_ids(self) -> tuple[str, ...]: ...

    def contract_for(self, task_id: str) -> TaskContractFixture: ...

    def entry_for(self, task_id: str) -> WorkspacePreflightEntryLike: ...


@dataclass(frozen=True)
class WorkspacePreflightPackageIdentity:
    manifest: ModelPackageManifest
    manifest_sha256: str


@dataclass(frozen=True)
class WorkspacePreflightEntry:
    model_package: WorkspacePreflightPackageIdentity


class CurrentWorkspacePreflightRegistry:
    """Package and Task metadata needed by the read-only workspace check.

    Unlike the production TaskRegistry this deliberately does not load source
    workbooks or initialize prediction runtimes. The API startup immediately
    performs those complete checks, while this preflight only compares the
    current immutable identities with references already stored in the DB.
    """

    def __init__(
        self,
        *,
        active_packages_path: str | Path = ACTIVE_PACKAGES_PATH,
        contract_root: str | Path | None = None,
    ) -> None:
        root = (
            Path(contract_root)
            if contract_root is not None
            else Path(__file__).resolve().parents[1] / "tasks" / "task_definitions"
        )
        contracts = load_task_contracts(root)
        registered = set(registered_task_modules())
        if registered != set(contracts):
            missing = sorted(set(contracts) - registered)
            unknown = sorted(registered - set(contracts))
            raise TaskRegistryError(
                "TaskModule registry must exactly match task definitions; "
                f"missing={missing}, unknown={unknown}"
            )
        configured_path = Path(active_packages_path)
        configured = load_active_packages(configured_path)
        validate_active_package_task_set(configured, set(contracts))
        entries: dict[str, WorkspacePreflightEntry] = {}
        for task_id in contracts:
            try:
                package_root = resolve_configured_package(
                    task_id,
                    config_path=configured_path,
                )
                manifest_bytes = (package_root / "manifest.json").read_bytes()
                manifest = ModelPackageManifest.model_validate_json(manifest_bytes)
                if manifest.task_id != task_id:
                    raise PackageContractError(
                        f"active Package task {manifest.task_id} does not match {task_id}"
                    )
                package = WorkspacePreflightPackageIdentity(
                    manifest=manifest,
                    manifest_sha256=hashlib.sha256(manifest_bytes).hexdigest(),
                )
            except (OSError, ValueError, KeyError):
                # Resource failures are rendered by the normal startup
                # diagnostic. They are not persisted-workspace conflicts.
                continue
            entries[task_id] = WorkspacePreflightEntry(model_package=package)
        self._contracts = MappingProxyType(contracts)
        self._entries = MappingProxyType(entries)
        self._active_packages_path = configured_path
        self._production_identities: frozenset[tuple[str, str, str]] | None = None

    @property
    def available_task_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def contract_for(self, task_id: str) -> TaskContractFixture:
        return self._contracts[task_id]

    def entry_for(self, task_id: str) -> WorkspacePreflightEntry:
        return self._entries[task_id]

    def confirms_package_identity(self, task_id: str) -> bool:
        """Run the production source/runtime checks only before blocking startup."""

        if self._production_identities is None:
            try:
                # Local import avoids app -> preflight -> app initialization.
                from decision_workbench.bootstrap.resources import (
                    prepare_app_resources,
                )

                resources = prepare_app_resources(
                    active_packages_path=self._active_packages_path,
                )
            except (OSError, ValueError, KeyError):
                self._production_identities = frozenset()
            else:
                self._production_identities = frozenset(
                    (
                        available_task_id,
                        production.manifest.package_id,
                        production.manifest_sha256,
                    )
                    for available_task_id in resources.task_registry.available_task_ids
                    for production in (
                        resources.task_registry.entry_for(
                            available_task_id
                        ).model_package,
                    )
                )
        candidate = self._entries[task_id].model_package
        return (
            task_id,
            candidate.manifest.package_id,
            candidate.manifest_sha256,
        ) in self._production_identities


class WorkspacePreflightFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["warning", "error"]
    stage: Literal["resource", "catalog", "project_binding", "chain_binding"]
    resource_id: str
    cause: str
    impact: str
    recovery_hint: str
    registered_digest: str | None = None
    current_digest: str | None = None


class WorkspacePreflightReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["workspace-preflight/v1"] = "workspace-preflight/v1"
    status: Literal["ok", "error"]
    code: Literal[0, 1]
    database: str
    read_only: Literal[True] = True
    database_exists: bool
    findings: list[WorkspacePreflightFinding]


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_only_connection(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"{database.resolve().as_uri()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    escaped = table.replace('"', '""')
    return {
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{escaped}")')
    }


def _package_findings(
    connection: sqlite3.Connection,
    registry: WorkspacePreflightRegistry,
) -> list[WorkspacePreflightFinding]:
    findings: list[WorkspacePreflightFinding] = []
    for task_id in registry.available_task_ids:
        entry = registry.entry_for(task_id)
        package = entry.model_package
        row = connection.execute(
            "SELECT id,package_id,task_id,task_contract_digest,manifest_digest,"
            "manifest_json,archived_at FROM model_package_refs "
            "WHERE package_id=? AND manifest_digest=?",
            (package.manifest.package_id, package.manifest_sha256),
        ).fetchone()
        if row is None:
            continue
        current_contract = semantic_digest(
            persisted_task_definition_payload(
                registry.contract_for(task_id).task_definition
            )
        )
        current_manifest = _canonical_json(
            package.manifest.model_dump(mode="json")
        )
        try:
            registered_manifest = json.loads(row["manifest_json"])
            if isinstance(registered_manifest, dict):
                registered_manifest.pop("_catalog_identity", None)
            comparable_manifest = _canonical_json(registered_manifest)
        except (TypeError, ValueError, json.JSONDecodeError):
            comparable_manifest = row["manifest_json"]
        mismatches: list[str] = []
        if row["task_id"] != task_id:
            mismatches.append(
                f"task_id: 登録={row['task_id']} / 現行={task_id}"
            )
        if row["task_contract_digest"] != current_contract:
            mismatches.append(
                "task_contract_digest: "
                f"登録={row['task_contract_digest']} / 現行={current_contract}"
            )
        if comparable_manifest != current_manifest:
            mismatches.append("manifest内容が現行Packageと異なります")
        if not mismatches:
            continue
        identity_validator = getattr(registry, "confirms_package_identity", None)
        if callable(identity_validator) and not identity_validator(task_id):
            # A broken current Package is diagnosed by normal API startup.
            # It must not be presented as a persisted-workspace conflict.
            continue
        latest_maintenance = (
            connection.execute(
                "SELECT id,operation FROM workspace_maintenance_events "
                "WHERE resource_kind='model_package_ref' AND resource_id=? "
                "ORDER BY created_at DESC,id DESC LIMIT 1",
                (row["id"],),
            ).fetchone()
            if "workspace_maintenance_events" in _tables(connection)
            else None
        )
        references = model_package_reference_labels(connection, row)
        authorized = (
            row["archived_at"] is not None
            and latest_maintenance is not None
            and latest_maintenance["operation"] == "deactivate"
            and not references
        )
        findings.append(
            WorkspacePreflightFinding(
                severity="warning" if authorized else "error",
                stage="catalog",
                resource_id=package.manifest.package_id,
                cause=(
                    "明示的な利用停止を確認しました。次回bootstrapで現行contractへ再登録します: "
                    if authorized
                    else ""
                )
                + "; ".join(mismatches),
                impact=(
                    "未参照登録の旧内容は監査記録に保持され、"
                    "現行contractで再登録されます。"
                    if authorized
                    else (
                        "同じPackage identityを別の契約へ結び直すため、"
                        "workspace catalog bootstrapが停止します。"
                    )
                ),
                recovery_hint=(
                    "そのまま起動して監査付き再登録を完了してください。"
                    if authorized
                    else (
                        f"`npm run model:verify -- --task {task_id}`で現行Packageを確認し、"
                        "必要なら新しいPackage versionとして再生成してください。"
                        "既存の判断台帳を変更しない場合は、別workspaceで起動してください。"
                    )
                ),
                registered_digest=row["task_contract_digest"],
                current_digest=current_contract,
            )
        )
    return findings


def _project_findings(
    connection: sqlite3.Connection,
    *,
    chain_revisions_available: bool,
) -> list[WorkspacePreflightFinding]:
    findings: list[WorkspacePreflightFinding] = []
    rows = connection.execute(
        "SELECT p.id,p.task_id,p.task_contract_digest,p.model_package_ref_id,"
        "p.model_package_manifest_digest,p.dataset_view_revision_id,"
        "p.scientific_identity_json,m.id AS package_exists,"
        "m.task_id AS package_task_id,m.task_contract_digest AS package_contract_digest,"
        "m.manifest_digest AS package_manifest_digest,"
        "v.id AS view_exists "
        "FROM projects p "
        "LEFT JOIN model_package_refs m ON m.id=p.model_package_ref_id "
        "LEFT JOIN dataset_view_revisions v ON v.id=p.dataset_view_revision_id"
    ).fetchall()
    for row in rows:
        try:
            raw_identity = json.loads(row["scientific_identity_json"] or "{}")
            identity_model = _PROJECT_IDENTITY_ADAPTER.validate_python(raw_identity)
            identity = identity_model.model_dump(mode="json")
        except (json.JSONDecodeError, ValidationError):
            findings.append(
                WorkspacePreflightFinding(
                    severity="error",
                    stage="project_binding",
                    resource_id=row["id"],
                    cause="scientific identityが契約に適合しません",
                    impact="Projectが固定した科学的identityを復元できません。",
                    recovery_hint=(
                        "このworkspaceのbackupとログを保全し、"
                        "整合性検証済みbackupへ復元してください。"
                    ),
                )
            )
            continue
        if identity.get("identity_kind") == "single_task":
            # Store migrations can create canonical Projects before catalog
            # bootstrap binds both immutable references. This fully unbound
            # state is recoverable by the normal startup path; a partial or
            # stale binding is not.
            if (
                row["model_package_ref_id"] is None
                and row["dataset_view_revision_id"] is None
                and identity["binding_provenance"] == "unbound_legacy"
            ):
                continue
            causes: list[str] = []
            identity_mirrors = {
                "task_id": row["task_id"],
                "model_package_ref_id": row["model_package_ref_id"],
                "model_package_manifest_digest": row[
                    "model_package_manifest_digest"
                ],
                "dataset_view_revision_id": row["dataset_view_revision_id"],
                "task_contract_digest": row["task_contract_digest"],
            }
            for key, mirror in identity_mirrors.items():
                if identity.get(key) != mirror:
                    causes.append(
                        f"scientific identityの{key}とProject列が一致しません"
                    )
            if row["package_exists"] is None:
                causes.append(
                    f"Model Package参照 {row['model_package_ref_id']} が存在しません"
                )
            else:
                if (
                    row["model_package_manifest_digest"]
                    != row["package_manifest_digest"]
                ):
                    causes.append("ProjectとPackageのmanifest digestが一致しません")
                if (
                    row["task_contract_digest"]
                    != row["package_contract_digest"]
                ):
                    causes.append("ProjectとPackageのTask contract digestが一致しません")
            if row["view_exists"] is None:
                causes.append(
                    f"Dataset View参照 {row['dataset_view_revision_id']} が存在しません"
                )
            if causes:
                findings.append(
                    WorkspacePreflightFinding(
                        severity="error",
                        stage="project_binding",
                        resource_id=row["id"],
                        cause="; ".join(causes),
                        impact="Projectが固定した予測・データの版を安全に解決できません。",
                        recovery_hint=(
                            "このworkspaceのbackupとログを保全し、"
                            "`npm run workspace:maintenance -- inspect`で参照状態を確認してください。"
                        ),
                    )
                )
        elif identity.get("identity_kind") == "chain":
            if not chain_revisions_available:
                continue
            revision_id = identity.get("chain_revision_id")
            revision_digest = identity.get("chain_revision_digest")
            revision = (
                connection.execute(
                    "SELECT revision_digest FROM chain_revisions WHERE id=?",
                    (revision_id,),
                ).fetchone()
                if revision_id
                else None
            )
            if revision is None or revision["revision_digest"] != revision_digest:
                findings.append(
                    WorkspacePreflightFinding(
                        severity="error",
                        stage="chain_binding",
                        resource_id=row["id"],
                        cause=(
                            f"Chain Revision {revision_id} "
                            f"({revision_digest}) を解決できません"
                        ),
                        impact="保存済みChain Projectの段構成を再現できません。",
                        recovery_hint=(
                            "このworkspaceのbackupとログを保全し、"
                            "一致するChain Revisionを含むアプリ版へ戻してください。"
                        ),
                        registered_digest=revision_digest,
                        current_digest=(
                            revision["revision_digest"] if revision is not None else None
                        ),
                    )
                )
    return findings


def inspect_workspace_compatibility(
    database: str | Path,
    registry: WorkspacePreflightRegistry,
) -> WorkspacePreflightReport:
    path = Path(database).expanduser().resolve()
    if not path.exists():
        return WorkspacePreflightReport(
            status="ok",
            code=0,
            database=str(path),
            database_exists=False,
            findings=[],
        )

    with _read_only_connection(path) as connection:
        tables = _tables(connection)
        required_columns = {
            "model_package_refs": {
                "id",
                "package_id",
                "task_id",
                "task_contract_digest",
                "manifest_digest",
                "manifest_json",
                "archived_at",
            },
            "projects": {
                "id",
                "task_contract_digest",
                "model_package_ref_id",
                "model_package_manifest_digest",
                "dataset_view_revision_id",
                "scientific_identity_json",
            },
            "dataset_view_revisions": {"id"},
        }
        # A new or legacy database is migrated by Store before catalog bootstrap.
        # The preflight must not prevent that supported startup path.
        if any(
            table not in tables
            or not columns.issubset(_columns(connection, table))
            for table, columns in required_columns.items()
        ):
            return WorkspacePreflightReport(
                status="ok",
                code=0,
                database=str(path),
                database_exists=True,
                findings=[],
            )
        findings = _package_findings(connection, registry)
        chain_revisions_available = (
            "chain_revisions" in tables
            and {"id", "revision_digest"}.issubset(
                _columns(connection, "chain_revisions")
            )
        )
        chain_catalog_current = (
            "schema_migrations" in tables
            and connection.execute(
                "SELECT 1 FROM schema_migrations WHERE id='chain-catalog-v1'"
            ).fetchone()
            is not None
        )
        if chain_catalog_current and not chain_revisions_available:
            findings.append(
                WorkspacePreflightFinding(
                    severity="error",
                    stage="chain_binding",
                    resource_id="chain-catalog-v1",
                    cause="現行migration記録に対応するChain Revision tableがありません",
                    impact="保存済みChain Projectと段構成を解決できず、起動が停止します。",
                    recovery_hint=(
                        "このworkspaceのbackupとログを保全し、"
                        "整合性検証済みbackupへ復元してください。"
                    ),
                )
            )
        findings.extend(
            _project_findings(
                connection,
                chain_revisions_available=chain_revisions_available,
            )
        )

    has_errors = any(item.severity == "error" for item in findings)
    return WorkspacePreflightReport(
        status="error" if has_errors else "ok",
        code=1 if has_errors else 0,
        database=str(path),
        database_exists=True,
        findings=findings,
    )
