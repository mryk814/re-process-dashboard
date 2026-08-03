from __future__ import annotations

import sqlite3

import pytest

from decision_workbench.persistence.chain_catalog_migration import (
    migrate_chain_catalog,
)
from decision_workbench.persistence.prediction_graph_actual_migration import (
    EXPECTED_COLUMNS,
    MIGRATION_CHECKSUM,
    MIGRATION_ID,
    TABLE,
    PredictionGraphActualMigrationError,
    migrate_prediction_graph_actuals,
)


def _columns(database) -> set[str]:
    with sqlite3.connect(database) as connection:
        return {
            str(row[1])
            for row in connection.execute(f'PRAGMA table_info("{TABLE}")')
        }


def test_prediction_graph_actual_migration_is_additive_and_idempotent(
    tmp_path,
) -> None:
    database = tmp_path / "legacy.db"
    migrate_chain_catalog(database)

    migrate_prediction_graph_actuals(database)
    migrate_prediction_graph_actuals(database)

    assert _columns(database) == EXPECTED_COLUMNS
    with sqlite3.connect(database) as connection:
        marker = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
    assert marker == (MIGRATION_CHECKSUM,)


def test_prediction_graph_actual_migration_rejects_checksum_drift(
    tmp_path,
) -> None:
    database = tmp_path / "checksum.db"
    migrate_prediction_graph_actuals(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE schema_migrations SET checksum='changed' WHERE id=?",
            (MIGRATION_ID,),
        )

    with pytest.raises(
        PredictionGraphActualMigrationError,
        match="checksum",
    ):
        migrate_prediction_graph_actuals(database)


def test_prediction_graph_actual_migration_rejects_marked_table_drift(
    tmp_path,
) -> None:
    database = tmp_path / "column-drift.db"
    migrate_prediction_graph_actuals(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"ALTER TABLE {TABLE} RENAME TO old_actuals")
        connection.execute(
            f"CREATE TABLE {TABLE} (id TEXT PRIMARY KEY, project_id TEXT)"
        )

    with pytest.raises(
        PredictionGraphActualMigrationError,
        match="columns are missing",
    ):
        migrate_prediction_graph_actuals(database)


def test_prediction_graph_actual_migration_rejects_unmarked_table(
    tmp_path,
) -> None:
    database = tmp_path / "unmarked.db"
    migrate_chain_catalog(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f"CREATE TABLE {TABLE} (id TEXT PRIMARY KEY)")

    with pytest.raises(
        PredictionGraphActualMigrationError,
        match="exists without its migration marker",
    ):
        migrate_prediction_graph_actuals(database)
