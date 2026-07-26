from __future__ import annotations

import csv
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from material_workbench.developer_experience.schemas import (
    DeveloperCheck,
    RuntimeDiagnosticsReport,
)
from material_workbench.persistence.candidate_migration import (
    CANDIDATE_SAFETY_MIGRATION_ID,
    MIGRATION_ID as CANDIDATE_MIGRATION_ID,
)
from material_workbench.persistence.lineage_review_migration import (
    MIGRATION_ID as LINEAGE_REVIEW_MIGRATION_ID,
)
from material_workbench.persistence.project_lifecycle_migration import (
    MIGRATION_ID as PROJECT_LIFECYCLE_MIGRATION_ID,
)
from material_workbench.persistence.sqlite_connection import (
    SQLITE_BUSY_TIMEOUT_MS,
    sqlite_connection,
)
from material_workbench.persistence.store import Store
from material_workbench.persistence.workspace_catalog import WorkspaceCatalog
from material_workbench.persistence.workspace_catalog_migration import (
    MIGRATION_ID as WORKSPACE_CATALOG_MIGRATION_ID,
)
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.contracts.subsystem_availability import (
    SubsystemAvailabilityRegistry,
)


EXPECTED_MIGRATIONS = {
    CANDIDATE_MIGRATION_ID,
    CANDIDATE_SAFETY_MIGRATION_ID,
    WORKSPACE_CATALOG_MIGRATION_ID,
    LINEAGE_REVIEW_MIGRATION_ID,
    PROJECT_LIFECYCLE_MIGRATION_ID,
}
SECOM_STRESS_SOURCE = Path("data/source/external/secom_stress.csv")


def _database_check(store: Store) -> DeveloperCheck:
    try:
        with sqlite_connection(store.path) as connection:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
            foreign_keys = int(connection.execute("PRAGMA foreign_keys").fetchone()[0])
            busy_timeout = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
            journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            synchronous = int(connection.execute("PRAGMA synchronous").fetchone()[0])
            foreign_key_violations = connection.execute(
                "PRAGMA foreign_key_check"
            ).fetchall()
            applied = {
                str(row[0])
                for row in connection.execute("SELECT id FROM schema_migrations")
            }
        missing = sorted(EXPECTED_MIGRATIONS - applied)
    except (OSError, sqlite3.Error) as exc:
        return DeveloperCheck(
            id="database",
            section="runtime",
            title="Database migration",
            severity="error",
            summary="DBの整合性を確認できませんでした。",
            cause=str(exc),
            impact="Project・候補・固定参照を安全に読み込めません。",
        )
    policy = {
        "foreign_keys": foreign_keys,
        "busy_timeout": busy_timeout,
        "journal_mode": journal_mode,
        "synchronous": synchronous,
        "foreign_key_violations": len(foreign_key_violations),
    }
    healthy = (
        quick_check == "ok"
        and not missing
        and foreign_keys == 1
        and busy_timeout == SQLITE_BUSY_TIMEOUT_MS
        and journal_mode == "delete"
        and synchronous == 2
        and not foreign_key_violations
    )
    return DeveloperCheck(
        id="database",
        section="runtime",
        title="Database migration",
        severity="ok" if healthy else "error",
        summary=(
            "DB整合性、migration、SQLite接続ポリシーを確認しました。"
            if healthy
            else "DBまたはSQLite接続ポリシーに不整合があります。"
        ),
        impact=None if healthy else "一部の保存データを現在の契約で読めない可能性があります。",
        details={
            "quick_check": quick_check,
            "applied": sorted(applied),
            "missing": missing,
            "policy": policy,
        },
    )


def _secom_stress_fixture_check(source: Path = SECOM_STRESS_SOURCE) -> DeveloperCheck:
    try:
        with source.open("r", encoding="utf-8", newline="") as stream:
            reader = csv.DictReader(stream)
            sensor_columns = [
                name for name in (reader.fieldnames or [])
                if name.startswith("sensor_")
            ]
            rows = list(reader)
        label_counts = {
            label: sum(row.get("yield_label") == label for row in rows)
            for label in ("pass", "fail")
        }
    except (OSError, csv.Error) as exc:
        return DeveloperCheck(
            id="secom-stress-fixture",
            section="developer-fixtures",
            title="SECOM stress fixture",
            severity="error",
            summary="SECOM検証データを読み込めません。",
            cause=str(exc),
            impact="欠損・高次元・クラス不均衡に対する開発者診断を再現できません。",
            details={"source": str(source)},
        )

    valid = (
        len(rows) == 1567
        and len(sensor_columns) == 590
        and label_counts == {"pass": 1463, "fail": 104}
    )
    return DeveloperCheck(
        id="secom-stress-fixture",
        section="developer-fixtures",
        title="SECOM stress fixture",
        severity="ok" if valid else "error",
        summary=(
            "実測分類Taskを再現できます（1,567行・590センサ・fail 6.6%）。"
            if valid
            else "SECOM検証データが期待する契約と一致しません。"
        ),
        impact=(
            "候補編集は層化fold内で安定した代表12センサに限定し、匿名センサを因果解釈しません。"
            if valid
            else "開発者向けの前処理・品質診断が誤った条件で評価されます。"
        ),
        details={
            "source": str(source),
            "rows": len(rows),
            "sensor_features": len(sensor_columns),
            "label_counts": label_counts,
            "product_boundary": "classification task enabled with 12 representative sensors",
        },
    )


def run_runtime_diagnostics(
    *,
    store: Store,
    registry: TaskRegistry,
    catalog: WorkspaceCatalog,
    resolver: ProjectRuntimeResolver,
    subsystem_registry: SubsystemAvailabilityRegistry,
) -> RuntimeDiagnosticsReport:
    checks = [_database_check(store)]
    projects = store.list_projects()
    resolution_errors: dict[str, str] = {}
    runtime_details: dict[str, object] = {}
    archived: dict[str, list[str]] = {}

    for project in projects:
        try:
            resolved = resolver.resolve(project)
            runtime_details[project.id] = {
                "task_id": project.task_id,
                "package_id": resolved.runtime.model_package.manifest.package_id,
                "runtime_types": sorted({
                    predictor.runtime_type
                    for predictor in resolved.runtime.model_package.manifest.predictors
                }),
                "dataset_source_sha256": resolved.runtime.data.source_sha256,
            }
        except Exception as exc:  # runtime boundary: report every broken project
            resolution_errors[project.id] = str(exc)

        archived_references: list[str] = []
        view = (
            catalog.get_dataset_view_revision(project.dataset_view_revision_id, include_archived=True)
            if project.dataset_view_revision_id
            else None
        )
        if view and view.archived_at:
            archived_references.append("Dataset View")
        if view:
            for member in view.members:
                dataset = catalog.get_dataset_revision(member.dataset_revision_id, include_archived=True)
                if dataset and dataset.archived_at:
                    archived_references.append(f"Dataset Revision {dataset.id}")
                if dataset:
                    asset = catalog.get_data_asset(dataset.data_asset_id, include_archived=True)
                    profile = catalog.get_profile_revision(dataset.profile_revision_id, include_archived=True)
                    if asset and asset.archived_at:
                        archived_references.append(f"Data Asset {asset.id}")
                    if profile and profile.archived_at:
                        archived_references.append(f"Profile Revision {profile.id}")
        package = (
            catalog.get_model_package_ref(project.model_package_ref_id, include_archived=True)
            if project.model_package_ref_id
            else None
        )
        if package and package.archived_at:
            archived_references.append(f"Model Package {package.id}")
        if archived_references:
            archived[project.id] = archived_references

    checks.append(DeveloperCheck(
        id="project-references",
        section="runtime",
        title="Projectの固定参照",
        severity="error" if resolution_errors else "ok",
        summary="全ProjectのDataset・Profile・Packageを解決できます。" if not resolution_errors else "解決できないProject参照があります。",
        cause=None if not resolution_errors else " / ".join(f"{key}: {value}" for key, value in resolution_errors.items()),
        impact=None if not resolution_errors else "該当Projectでは探索・推論を実行できません。",
        details={"resolved": runtime_details, "errors": resolution_errors},
    ))
    checks.append(DeveloperCheck(
        id="archived-resources",
        section="runtime",
        title="Archived／missing resource",
        severity="warning" if archived else "ok",
        summary="Archive済み参照があります。" if archived else "現在参照中のリソースは利用可能です。",
        impact="履歴は表示できますが、新しいProjectの参照候補には使えません。" if archived else None,
        details={"projects": archived},
    ))

    capabilities = {
        task_id: registry.contract_for(task_id).runtime_capability.model_dump(mode="json")
        for task_id in registry.task_ids
    }
    checks.append(DeveloperCheck(
        id="runtime-capabilities",
        section="runtime",
        title="Runtime capability",
        severity="ok",
        summary="登録TaskのRuntime capabilityを読み込めます。",
        details=capabilities,
    ))
    desktop_sidecar = bool(os.getenv("WORKBENCH_LAUNCH_TOKEN"))
    checks.append(DeveloperCheck(
        id="sidecar",
        section="runtime",
        title="API／sidecar状態",
        severity="ok",
        summary="Desktop sidecarとして稼働中です。" if desktop_sidecar else "ローカルAPIとして稼働中です。",
        details={"mode": "desktop-sidecar" if desktop_sidecar else "local-api"},
    ))
    optional_subsystems = subsystem_registry.list()
    unavailable_subsystems = [
        item for item in optional_subsystems if item.status == "unavailable"
    ]
    checks.append(DeveloperCheck(
        id="optional-subsystems",
        section="runtime",
        title="Optional subsystem availability",
        severity="warning" if unavailable_subsystems else "ok",
        summary=(
            f"{len(unavailable_subsystems)}件の機能を隔離して起動しています。"
            if unavailable_subsystems
            else "Transform・Chain・評価成果物を利用できます。"
        ),
        cause=(
            " / ".join(
                f"{item.subsystem_id}: {item.cause}"
                for item in unavailable_subsystems
            )
            if unavailable_subsystems
            else None
        ),
        impact=(
            " / ".join(item.impact for item in unavailable_subsystems)
            if unavailable_subsystems
            else None
        ),
        details={
            "items": [
                item.model_dump(mode="json") for item in optional_subsystems
            ]
        },
    ))
    checks.append(_secom_stress_fixture_check())

    status = (
        "error"
        if any(check.severity == "error" for check in checks)
        else "warning"
        if any(check.severity == "warning" for check in checks)
        else "ok"
    )
    return RuntimeDiagnosticsReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        status=status,
        checks=checks,
        project_count=len(projects),
        task_ids=list(registry.task_ids),
    )
