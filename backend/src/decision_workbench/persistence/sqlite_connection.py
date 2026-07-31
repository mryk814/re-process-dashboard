from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SQLITE_BUSY_TIMEOUT_MS = 5_000
SQLITE_JOURNAL_MODE = "delete"
SQLITE_SYNCHRONOUS = "FULL"


class SQLitePolicyError(RuntimeError):
    pass


def initialize_sqlite(database: str | Path) -> None:
    """Persist the database-wide journal policy once during startup."""

    path = str(database)
    connection = sqlite3.connect(
        path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
    )
    try:
        journal_mode = str(
            connection.execute(
                f"PRAGMA journal_mode = {SQLITE_JOURNAL_MODE}"
            ).fetchone()[0]
        ).lower()
        expected = "memory" if path == ":memory:" else SQLITE_JOURNAL_MODE
        if journal_mode != expected:
            raise SQLitePolicyError(
                "SQLite journal modeを固定できませんでした: "
                f"expected={expected}, actual={journal_mode}"
            )
    finally:
        connection.close()


def validate_sqlite_foreign_keys(database: str | Path) -> None:
    """Reject latent legacy orphans after every schema migration has run."""

    connection = connect_sqlite(database)
    try:
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        connection.close()
    if violations:
        details = ", ".join(
            f"{row[0]} rowid={row[1]} -> {row[2]} fk={row[3]}"
            for row in violations[:10]
        )
        raise SQLitePolicyError(
            "既存DBに外部キー孤児があります。自動削除せず起動を停止します: "
            + details
        )


def connect_sqlite(
    database: str | Path,
    *,
    row_factory: bool = True,
) -> sqlite3.Connection:
    """Open a production SQLite connection with one explicit policy."""

    path = str(database)
    connection = sqlite3.connect(
        path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1_000,
    )
    try:
        if row_factory:
            connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}"
        )
        connection.execute(f"PRAGMA synchronous = {SQLITE_SYNCHRONOUS}")
        journal_mode = str(
            connection.execute("PRAGMA journal_mode").fetchone()[0]
        ).lower()
        expected_journal_modes = (
            {"memory"}
            if path == ":memory:"
            else {SQLITE_JOURNAL_MODE}
        )
        if journal_mode not in expected_journal_modes:
            raise SQLitePolicyError(
                "SQLite journal modeを固定できませんでした: "
                f"expected={sorted(expected_journal_modes)}, actual={journal_mode}"
            )
        if int(
            connection.execute("PRAGMA foreign_keys").fetchone()[0]
        ) != 1:
            raise SQLitePolicyError("SQLite foreign_keysを有効化できませんでした")
        if int(
            connection.execute("PRAGMA busy_timeout").fetchone()[0]
        ) != SQLITE_BUSY_TIMEOUT_MS:
            raise SQLitePolicyError("SQLite busy_timeoutを固定できませんでした")
        if int(connection.execute("PRAGMA synchronous").fetchone()[0]) != 2:
            raise SQLitePolicyError("SQLite synchronousをFULLに固定できませんでした")
        return connection
    except Exception:
        connection.close()
        raise


@contextmanager
def sqlite_connection(
    database: str | Path,
    *,
    row_factory: bool = True,
) -> Iterator[sqlite3.Connection]:
    """Yield a policy-compliant connection and always close its handle."""

    connection = connect_sqlite(database, row_factory=row_factory)
    try:
        with connection:
            yield connection
    finally:
        connection.close()
