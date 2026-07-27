from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from material_workbench.app import create_app
from material_workbench.persistence.workspace_catalog import CatalogConflictError


def test_workspace_catalog_conflict_stops_startup_and_known_good_db_recovers(
    tmp_path: Path,
    app_resources,
) -> None:
    database = tmp_path / "workbench.db"
    backup = tmp_path / "known-good.db"
    data_library = tmp_path / "data-library"

    with TestClient(
        create_app(
            db_path=database,
            data_library_path=data_library,
            _resources=app_resources,
        )
    ) as healthy:
        assert healthy.get("/api/health").json()["ok"] is True
    shutil.copy2(database, backup)

    with sqlite3.connect(database) as connection:
        package = connection.execute(
            "SELECT id FROM model_package_refs "
            "WHERE archived_at IS NULL ORDER BY id LIMIT 1"
        ).fetchone()
        assert package is not None
        connection.execute(
            "UPDATE model_package_refs SET task_contract_digest=? WHERE id=?",
            ("sha256:" + "0" * 64, package[0]),
        )

    with pytest.raises(CatalogConflictError, match="別内容で登録済み"):
        with TestClient(
            create_app(
                db_path=database,
                data_library_path=data_library,
                _resources=app_resources,
            )
        ):
            pass

    shutil.copy2(backup, database)
    with TestClient(
        create_app(
            db_path=database,
            data_library_path=data_library,
            _resources=app_resources,
        )
    ) as recovered:
        assert recovered.get("/api/health").json()["ok"] is True
