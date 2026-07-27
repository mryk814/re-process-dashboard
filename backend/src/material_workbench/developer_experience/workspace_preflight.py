from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from material_workbench.contracts.chain_contracts import ProjectScientificIdentity
from material_workbench.persistence.workspace_catalog_bootstrap import (
    task_definition_digest,
)
from material_workbench.persistence.workspace_catalog import (
    model_package_reference_labels,
)
from material_workbench.tasks.task_registry import TaskRegistry


_PROJECT_IDENTITY_ADAPTER = TypeAdapter(ProjectScientificIdentity)


class WorkspacePreflightFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["warning", "error"]
    stage: Literal["catalog", "project_binding", "chain_binding"]
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
    registry: TaskRegistry,
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
        current_contract = task_definition_digest(registry, task_id)
        current_manifest = _canonical_json(
            package.manifest.model_dump(mode="json")
        )
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
        if row["manifest_json"] != current_manifest:
            mismatches.append("manifest内容が現行Packageと異なります")
        if not mismatches:
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
    registry: TaskRegistry,
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
