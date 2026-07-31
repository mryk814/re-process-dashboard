from __future__ import annotations

import json
import hashlib
import os
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from decision_workbench.contracts.data_lifecycle_contracts import (
    CurationRun,
    RawSourceSnapshot,
)
from decision_workbench.persistence.data_lifecycle_payload_storage import (
    QuarantinedPayloadReference,
    StoredLifecycleRowResource,
    store_curation_run,
    store_raw_snapshot,
)
from decision_workbench.persistence.row_payload_store import RowPayloadStore
from decision_workbench.persistence.sqlite_connection import connect_sqlite


MIGRATION_ID = "source-data-lifecycle-row-payload-v2"
MIGRATION_CHECKSUM = "canonical-ndjson-row-payload-guarded-v1"


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    }


def _add_columns(connection: sqlite3.Connection) -> None:
    additions = {
        "raw_source_snapshots": {
            "row_payload_sha256": "TEXT",
            "row_payload_bytes": "INTEGER",
            "row_count": "INTEGER",
        },
        "source_curation_runs": {
            "row_payload_sha256": "TEXT",
            "row_payload_bytes": "INTEGER",
            "row_count": "INTEGER",
            "quality_payload": "TEXT",
        },
    }
    for table, columns in additions.items():
        existing = _columns(connection, table)
        for column, declaration in columns.items():
            if column not in existing:
                connection.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN "{column}" {declaration}'
                )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS data_lifecycle_payload_findings ("
        "resource_kind TEXT NOT NULL,resource_id TEXT NOT NULL,"
        "reason TEXT NOT NULL,detected_at TEXT NOT NULL,"
        "PRIMARY KEY(resource_kind,resource_id))"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_raw_source_snapshots_connector_time "
        "ON raw_source_snapshots(connector_id,captured_at,id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_fetch_attempts_connector_time "
        "ON source_fetch_attempts(connector_id,started_at,id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_source_curation_runs_raw_time "
        "ON source_curation_runs(raw_snapshot_id,created_at,id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_canonical_approvals_run_time "
        "ON canonical_dataset_approvals(curation_run_id,approved_at,id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_training_snapshots_revision_time "
        "ON approved_training_snapshots("
        "canonical_dataset_revision_id,created_at,id)"
    )


def _unavailable_payload(
    *,
    resource_kind: str,
    resource_id: str,
    reason: str,
    quarantined_payload: QuarantinedPayloadReference,
) -> str:
    return StoredLifecycleRowResource(
        resource_kind=resource_kind,  # type: ignore[arg-type]
        resource_id=resource_id,
        resource={"id": resource_id},
        row_payload=None,
        unavailable_reason=reason,
        quarantined_payload=quarantined_payload,
    ).model_dump_json()


def _record_finding(
    connection: sqlite3.Connection,
    *,
    resource_kind: str,
    resource_id: str,
    reason: str,
) -> None:
    connection.execute(
        "INSERT INTO data_lifecycle_payload_findings("
        "resource_kind,resource_id,reason,detected_at"
        ") VALUES (?,?,?,?) "
        "ON CONFLICT(resource_kind,resource_id) DO UPDATE SET "
        "reason=excluded.reason,detected_at=excluded.detected_at",
        (resource_kind, resource_id, reason, datetime.now(UTC).isoformat()),
    )


def _quarantine_legacy_payload(
    store: RowPayloadStore,
    *,
    resource_kind: str,
    stored: str,
) -> QuarantinedPayloadReference:
    encoded = stored.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    root = store.database.parent / "row-payloads" / "quarantine" / resource_kind
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{digest}.json"
    if destination.exists():
        if destination.read_bytes() != encoded:
            raise RuntimeError("Legacy payload quarantine digest collision")
    else:
        temporary = root / f".{digest}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as output:
                output.write(encoded)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    relative = destination.relative_to(store.database.parent).as_posix()
    return QuarantinedPayloadReference(
        sha256=digest,
        size_bytes=len(encoded),
        path=relative,
    )


def _migrate_raw(
    connection: sqlite3.Connection,
    store: RowPayloadStore,
) -> None:
    rows = connection.execute(
        "SELECT id,payload FROM raw_source_snapshots ORDER BY captured_at,id"
    )
    for row in rows:
        resource_id = str(row["id"])
        stored = str(row["payload"])
        try:
            wrapper = StoredLifecycleRowResource.model_validate_json(stored)
        except ValidationError:
            wrapper = None
        if wrapper is not None:
            reference = wrapper.row_payload
            connection.execute(
                "UPDATE raw_source_snapshots SET "
                "row_payload_sha256=?,row_payload_bytes=?,row_count=? WHERE id=?",
                (
                    reference.sha256 if reference else None,
                    reference.size_bytes if reference else None,
                    reference.row_count if reference else None,
                    resource_id,
                ),
            )
            continue
        try:
            snapshot = RawSourceSnapshot.model_validate_json(stored)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            quarantined = _quarantine_legacy_payload(
                store,
                resource_kind="raw_source_snapshot",
                stored=stored,
            )
            reason = (
                f"legacy Raw Snapshot is invalid: {type(exc).__name__}; "
                f"quarantined_sha256={quarantined.sha256}; "
                f"path={quarantined.path}"
            )
            connection.execute(
                "UPDATE raw_source_snapshots SET payload=? WHERE id=?",
                (
                    _unavailable_payload(
                        resource_kind="raw_source_snapshot",
                        resource_id=resource_id,
                        reason=reason,
                        quarantined_payload=quarantined,
                    ),
                    resource_id,
                ),
            )
            _record_finding(
                connection,
                resource_kind="raw_source_snapshot",
                resource_id=resource_id,
                reason=reason,
            )
            continue
        payload, reference = store_raw_snapshot(snapshot, store)
        connection.execute(
            "UPDATE raw_source_snapshots SET "
            "payload=?,row_payload_sha256=?,row_payload_bytes=?,row_count=? "
            "WHERE id=?",
            (
                payload,
                reference.sha256,
                reference.size_bytes,
                reference.row_count,
                resource_id,
            ),
        )


def _migrate_curation(
    connection: sqlite3.Connection,
    store: RowPayloadStore,
) -> None:
    rows = connection.execute(
        "SELECT id,payload FROM source_curation_runs ORDER BY created_at,id"
    )
    for row in rows:
        resource_id = str(row["id"])
        stored = str(row["payload"])
        try:
            wrapper = StoredLifecycleRowResource.model_validate_json(stored)
        except ValidationError:
            wrapper = None
        if wrapper is not None:
            reference = wrapper.row_payload
            quality = wrapper.resource.get("quality")
            connection.execute(
                "UPDATE source_curation_runs SET "
                "row_payload_sha256=?,row_payload_bytes=?,row_count=?,quality_payload=? "
                "WHERE id=?",
                (
                    reference.sha256 if reference else None,
                    reference.size_bytes if reference else None,
                    reference.row_count if reference else None,
                    json.dumps(
                        quality,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    if quality
                    else None,
                    resource_id,
                ),
            )
            continue
        try:
            run = CurationRun.model_validate_json(stored)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            quarantined = _quarantine_legacy_payload(
                store,
                resource_kind="curation_run",
                stored=stored,
            )
            reason = (
                f"legacy Curation Run is invalid: {type(exc).__name__}; "
                f"quarantined_sha256={quarantined.sha256}; "
                f"path={quarantined.path}"
            )
            connection.execute(
                "UPDATE source_curation_runs SET payload=? WHERE id=?",
                (
                    _unavailable_payload(
                        resource_kind="curation_run",
                        resource_id=resource_id,
                        reason=reason,
                        quarantined_payload=quarantined,
                    ),
                    resource_id,
                ),
            )
            _record_finding(
                connection,
                resource_kind="curation_run",
                resource_id=resource_id,
                reason=reason,
            )
            continue
        payload, reference = store_curation_run(run, store)
        connection.execute(
            "UPDATE source_curation_runs SET "
            "payload=?,row_payload_sha256=?,row_payload_bytes=?,row_count=?,"
            "quality_payload=? WHERE id=?",
            (
                payload,
                reference.sha256,
                reference.size_bytes,
                reference.row_count,
                json.dumps(run.quality.model_dump(mode="json"), ensure_ascii=False),
                resource_id,
            ),
        )


def _add_guards(connection: sqlite3.Connection) -> None:
    for table, resource_kind, record_kind in (
        (
            "raw_source_snapshots",
            "raw_source_snapshot",
            "raw-json-record/v1",
        ),
        ("source_curation_runs", "curation_run", "curated-row/v1"),
    ):
        def invariant(prefix: str) -> str:
            payload = f"{prefix}.payload"
            return (
                f"json_valid({payload})=1 "
                f"AND json_extract({payload},'$.schema_version')="
                "'lifecycle-row-storage/v1' "
                f"AND json_type({payload},'$.resource')='object' "
                f"AND json_type({payload},'$.resource.rows') IS NULL "
                f"AND json_extract({payload},'$.resource_kind')="
                f"'{resource_kind}' "
                f"AND json_extract({payload},'$.resource_id')={prefix}.id "
                "AND (("
                f"json_type({payload},'$.unavailable_reason')='null' "
                f"AND json_type({payload},'$.quarantined_payload')='null' "
                f"AND json_type({payload},'$.row_payload')='object' "
                f"AND json_extract({payload},'$.row_payload.schema_version')="
                "'row-payload-ref/v1' "
                f"AND json_extract({payload},'$.row_payload.record_kind')="
                f"'{record_kind}' "
                f"AND json_extract({payload},'$.row_payload.media_type')="
                "'application/x-ndjson' "
                f"AND json_type({payload},'$.row_payload.sha256')='text' "
                f"AND length(json_extract({payload},'$.row_payload.sha256'))=64 "
                f"AND json_extract({payload},'$.row_payload.sha256') "
                "NOT GLOB '*[^0-9a-f]*' "
                f"AND json_type({payload},'$.row_payload.size_bytes')='integer' "
                f"AND json_extract({payload},'$.row_payload.size_bytes')>=0 "
                f"AND json_type({payload},'$.row_payload.row_count')='integer' "
                f"AND json_extract({payload},'$.row_payload.row_count')>=0 "
                f"AND json_extract({payload},'$.row_payload.sha256')="
                f"{prefix}.row_payload_sha256 "
                f"AND json_extract({payload},'$.row_payload.size_bytes')="
                f"{prefix}.row_payload_bytes "
                f"AND json_extract({payload},'$.row_payload.row_count')="
                f"{prefix}.row_count "
                f"AND {prefix}.row_payload_sha256 IS NOT NULL "
                f"AND {prefix}.row_payload_bytes IS NOT NULL "
                f"AND {prefix}.row_count IS NOT NULL"
                ") OR ("
                f"json_type({payload},'$.unavailable_reason')='text' "
                f"AND json_type({payload},'$.row_payload')='null' "
                f"AND json_type({payload},'$.quarantined_payload')='object' "
                f"AND json_extract({payload},'$.quarantined_payload.schema_version')="
                "'quarantined-row-payload/v1' "
                f"AND json_type({payload},'$.quarantined_payload.sha256')='text' "
                f"AND length(json_extract({payload},'$.quarantined_payload.sha256'))=64 "
                f"AND json_extract({payload},'$.quarantined_payload.sha256') "
                "NOT GLOB '*[^0-9a-f]*' "
                f"AND json_type({payload},'$.quarantined_payload.size_bytes')='integer' "
                f"AND json_extract({payload},'$.quarantined_payload.size_bytes')>=0 "
                f"AND json_type({payload},'$.quarantined_payload.path')='text' "
                f"AND json_extract({payload},'$.quarantined_payload.path')="
                f"'row-payloads/quarantine/{resource_kind}/' || "
                f"json_extract({payload},'$.quarantined_payload.sha256') || '.json' "
                f"AND {prefix}.row_payload_sha256 IS NULL "
                f"AND {prefix}.row_payload_bytes IS NULL "
                f"AND {prefix}.row_count IS NULL"
                "))"
            )

        for operation in ("INSERT", "UPDATE"):
            trigger = f"guard_{table}_{operation.lower()}_row_payload"
            connection.execute(f'DROP TRIGGER IF EXISTS "{trigger}"')
            connection.execute(
                f'CREATE TRIGGER IF NOT EXISTS "{trigger}" '
                f"BEFORE {operation} ON {table} "
                f"WHEN COALESCE(NOT ({invariant('NEW')}),1) "
                "BEGIN SELECT RAISE(ABORT,'invalid lifecycle row payload reference'); END"
            )
        invalid = connection.execute(
            f'SELECT id FROM "{table}" '
            f"WHERE COALESCE(NOT ({invariant(table)}),1) LIMIT 1"
        ).fetchone()
        if invalid is not None:
            raise RuntimeError(
                f"Invalid lifecycle row payload reference: {table}/{invalid['id']}"
            )


def migrate_data_lifecycle_payloads(database: str | Path) -> None:
    connection = connect_sqlite(database)
    try:
        row = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if row is not None and row["checksum"] != MIGRATION_CHECKSUM:
            raise RuntimeError("Data lifecycle payload migration checksum mismatch")
        _add_columns(connection)
        store = RowPayloadStore(database)
        _migrate_raw(connection, store)
        _migrate_curation(connection, store)
        _add_guards(connection)
        if row is None:
            connection.execute(
                "INSERT INTO schema_migrations(id,checksum,applied_at) "
                "VALUES (?,?,?)",
                (
                    MIGRATION_ID,
                    MIGRATION_CHECKSUM,
                    datetime.now(UTC).isoformat(),
                ),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
