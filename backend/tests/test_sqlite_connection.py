from __future__ import annotations

import sqlite3

import pytest

from material_workbench.persistence.sqlite_connection import (
    SQLITE_BUSY_TIMEOUT_MS,
    SQLitePolicyError,
    connect_sqlite,
    initialize_sqlite,
    sqlite_connection,
    validate_sqlite_foreign_keys,
)


@pytest.mark.parametrize("memory", [False, True])
def test_connection_policy_and_close(tmp_path, memory: bool) -> None:
    database = ":memory:" if memory else tmp_path / "policy.db"
    initialize_sqlite(database)
    connection = connect_sqlite(database)
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert (
        connection.execute("PRAGMA busy_timeout").fetchone()[0]
        == SQLITE_BUSY_TIMEOUT_MS
    )
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == (
        "memory" if memory else "delete"
    )
    connection.close()

    with sqlite_connection(database) as managed:
        managed.execute("SELECT 1")
    with pytest.raises(sqlite3.ProgrammingError):
        managed.execute("SELECT 1")


def test_foreign_keys_are_enforced_and_legacy_orphans_are_reported(
    tmp_path,
) -> None:
    database = tmp_path / "foreign-keys.db"
    initialize_sqlite(database)
    with sqlite_connection(database) as connection:
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child("
            "id INTEGER PRIMARY KEY,parent_id INTEGER REFERENCES parent(id))"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO child(id,parent_id) VALUES (1,99)"
            )

    raw = sqlite3.connect(database)
    try:
        raw.execute("PRAGMA foreign_keys=OFF")
        raw.execute("INSERT INTO child(id,parent_id) VALUES (1,99)")
        raw.commit()
    finally:
        raw.close()
    with pytest.raises(SQLitePolicyError, match="child rowid=1"):
        validate_sqlite_foreign_keys(database)


def test_initialize_converts_legacy_wal_without_losing_rows(tmp_path) -> None:
    database = tmp_path / "legacy-wal.db"
    raw = sqlite3.connect(database)
    try:
        assert raw.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        raw.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        raw.execute("INSERT INTO evidence VALUES ('preserved')")
        raw.commit()
    finally:
        raw.close()

    initialize_sqlite(database)
    with sqlite_connection(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert connection.execute("SELECT value FROM evidence").fetchone()[0] == "preserved"
