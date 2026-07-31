from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from decision_workbench.persistence.workspace_catalog import (
    WorkspaceCatalog,
    model_package_reference_labels,
)


def inspect_package_registrations(database: str | Path) -> list[dict[str, Any]]:
    path = Path(database).expanduser().resolve()
    if not path.exists():
        return []
    connection = sqlite3.connect(
        f"{path.as_uri()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "model_package_refs" not in tables:
            return []
        rows = connection.execute(
            "SELECT * FROM model_package_refs "
            "ORDER BY archived_at IS NOT NULL,task_id,package_id,created_at,id"
        ).fetchall()
        return [
            {
                "id": row["id"],
                "package_id": row["package_id"],
                "task_id": row["task_id"],
                "task_contract_digest": row["task_contract_digest"],
                "manifest_digest": row["manifest_digest"],
                "locator": row["locator"],
                "active": row["archived_at"] is None,
                "archived_at": row["archived_at"],
                "referenced_by": model_package_reference_labels(connection, row),
            }
            for row in rows
        ]
    finally:
        connection.close()


def deactivate_package_registration(
    database: str | Path,
    *,
    reference_id: str,
    reason: str,
) -> dict[str, Any]:
    catalog = WorkspaceCatalog(database)
    updated = catalog.deactivate_model_package_ref_for_maintenance(
        reference_id,
        reason=reason,
    )
    if updated is None:
        raise LookupError(f"Model Package登録が見つかりません: {reference_id}")
    event = catalog.list_workspace_maintenance_events()[-1]
    return {
        "status": "deactivated",
        "package_ref": updated.model_dump(mode="json"),
        "audit_event": event,
    }
