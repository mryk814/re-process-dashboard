from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Literal

from decision_workbench.contracts.data_lifecycle_contracts import CuratedRow
from decision_workbench.persistence.row_payload_store import (
    RowPayloadError,
    RowPayloadReference,
    RowPayloadStore,
)


LifecycleRowResourceKind = Literal["raw_source_snapshot", "curation_run"]


def create_row_index_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS data_lifecycle_row_index_manifests("
        "resource_kind TEXT NOT NULL,"
        "resource_id TEXT NOT NULL,"
        "schema_version TEXT NOT NULL,"
        "payload_sha256 TEXT NOT NULL CHECK(length(payload_sha256)=64),"
        "row_count INTEGER NOT NULL CHECK(row_count>=0),"
        "accepted_count INTEGER NOT NULL CHECK(accepted_count>=0),"
        "warning_count INTEGER NOT NULL CHECK(warning_count>=0),"
        "quarantined_count INTEGER NOT NULL CHECK(quarantined_count>=0),"
        "blocked_count INTEGER NOT NULL CHECK(blocked_count>=0),"
        "reasoned_count INTEGER NOT NULL CHECK(reasoned_count>=0),"
        "PRIMARY KEY(resource_kind,resource_id)"
        ")"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS data_lifecycle_row_index("
        "resource_kind TEXT NOT NULL,"
        "resource_id TEXT NOT NULL,"
        "ordinal INTEGER NOT NULL CHECK(ordinal>=0),"
        "byte_offset INTEGER NOT NULL CHECK(byte_offset>=0),"
        "byte_length INTEGER NOT NULL CHECK(byte_length>0),"
        "line_sha256 TEXT NOT NULL CHECK(length(line_sha256)=64),"
        "sort_ordinal INTEGER CHECK(sort_ordinal>=0),"
        "status_ordinal INTEGER CHECK(status_ordinal>=0),"
        "reason_ordinal INTEGER CHECK(reason_ordinal>=0),"
        "raw_row_index INTEGER,"
        "row_key TEXT,"
        "status TEXT,"
        "reason_codes TEXT,"
        "PRIMARY KEY(resource_kind,resource_id,ordinal)"
        ")"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_lifecycle_row_index_byte_offset "
        "ON data_lifecycle_row_index(resource_kind,resource_id,byte_offset)"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_lifecycle_row_index_page "
        "ON data_lifecycle_row_index("
        "resource_kind,resource_id,sort_ordinal) "
        "WHERE sort_ordinal IS NOT NULL"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_lifecycle_row_index_curation_status "
        "ON data_lifecycle_row_index("
        "resource_kind,resource_id,status,status_ordinal) "
        "WHERE status_ordinal IS NOT NULL"
    )
    connection.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "idx_lifecycle_row_index_reasoned "
        "ON data_lifecycle_row_index("
        "resource_kind,resource_id,reason_ordinal) "
        "WHERE reason_ordinal IS NOT NULL"
    )


def rebuild_row_index(
    connection: sqlite3.Connection,
    store: RowPayloadStore,
    *,
    resource_kind: LifecycleRowResourceKind,
    resource_id: str,
    reference: RowPayloadReference,
) -> None:
    connection.execute(
        "DELETE FROM data_lifecycle_row_index "
        "WHERE resource_kind=? AND resource_id=?",
        (resource_kind, resource_id),
    )
    connection.execute(
        "DELETE FROM data_lifecycle_row_index_manifests "
        "WHERE resource_kind=? AND resource_id=?",
        (resource_kind, resource_id),
    )
    observed = 0
    status_counts = {
        "accepted": 0,
        "warning": 0,
        "quarantined": 0,
        "blocked": 0,
    }
    reasoned_count = 0
    for ordinal, (offset, raw_line) in enumerate(
        store.iter_verified_lines(reference)
    ):
        raw_row_index: int | None = None
        row_key: str | None = None
        status: str | None = None
        reason_codes: str | None = None
        if resource_kind == "curation_run":
            try:
                parsed = json.loads(raw_line)
                curated = CuratedRow.model_validate(parsed)
            except Exception as exc:
                raise RowPayloadError(
                    "curation row index source is invalid"
                ) from exc
            raw_row_index = curated.raw_row_index
            row_key = curated.row_key
            status = curated.status
            status_counts[status] += 1
            if curated.reason_codes:
                reasoned_count += 1
            reason_codes = json.dumps(
                curated.reason_codes,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        connection.execute(
            "INSERT INTO data_lifecycle_row_index("
            "resource_kind,resource_id,ordinal,byte_offset,byte_length,"
            "line_sha256,sort_ordinal,status_ordinal,reason_ordinal,"
            "raw_row_index,row_key,status,reason_codes"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                resource_kind,
                resource_id,
                ordinal,
                offset,
                len(raw_line),
                hashlib.sha256(raw_line).hexdigest(),
                ordinal if resource_kind == "raw_source_snapshot" else None,
                None,
                None,
                raw_row_index,
                row_key,
                status,
                reason_codes,
            ),
        )
        observed += 1
    if observed != reference.row_count:
        raise RowPayloadError("row payload index count does not match reference")
    if resource_kind == "curation_run":
        connection.execute(
            "CREATE TEMP TABLE lifecycle_ranked_positions AS "
            "SELECT rowid AS target_rowid,"
            "ROW_NUMBER() OVER ("
            "ORDER BY raw_row_index,row_key,ordinal"
            ")-1 AS sort_position,"
            "ROW_NUMBER() OVER ("
            "PARTITION BY status "
            "ORDER BY raw_row_index,row_key,ordinal"
            ")-1 AS status_position,"
            "CASE WHEN reason_codes<>'[]' THEN "
            "SUM(CASE WHEN reason_codes<>'[]' THEN 1 ELSE 0 END) "
            "OVER (ORDER BY raw_row_index,row_key,ordinal)-1 "
            "ELSE NULL END AS reason_position "
            "FROM data_lifecycle_row_index "
            "WHERE resource_kind='curation_run' AND resource_id=?",
            (resource_id,),
        )
        connection.execute(
            "CREATE UNIQUE INDEX lifecycle_ranked_positions_rowid "
            "ON lifecycle_ranked_positions(target_rowid)"
        )
        connection.execute(
            "UPDATE data_lifecycle_row_index AS target "
            "SET sort_ordinal=ranked.sort_position,"
            "status_ordinal=ranked.status_position,"
            "reason_ordinal=ranked.reason_position "
            "FROM lifecycle_ranked_positions AS ranked "
            "WHERE target.rowid=ranked.target_rowid"
        )
        connection.execute("DROP TABLE lifecycle_ranked_positions")
    connection.execute(
        "INSERT INTO data_lifecycle_row_index_manifests("
        "resource_kind,resource_id,schema_version,payload_sha256,row_count,"
        "accepted_count,warning_count,quarantined_count,blocked_count,"
        "reasoned_count"
        ") VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            resource_kind,
            resource_id,
            "lifecycle-row-seek-index/v1",
            reference.sha256,
            reference.row_count,
            status_counts["accepted"],
            status_counts["warning"],
            status_counts["quarantined"],
            status_counts["blocked"],
            reasoned_count,
        ),
    )


def ensure_row_index(
    connection: sqlite3.Connection,
    store: RowPayloadStore,
    *,
    resource_kind: LifecycleRowResourceKind,
    resource_id: str,
    reference: RowPayloadReference,
) -> None:
    manifest = connection.execute(
        "SELECT schema_version,payload_sha256,row_count "
        "FROM data_lifecycle_row_index_manifests "
        "WHERE resource_kind=? AND resource_id=?",
        (resource_kind, resource_id),
    ).fetchone()
    complete = (
        manifest is not None
        and manifest["schema_version"] == "lifecycle-row-seek-index/v1"
        and manifest["payload_sha256"] == reference.sha256
        and int(manifest["row_count"]) == reference.row_count
    )
    if complete:
        return
    rebuild_row_index(
        connection,
        store,
        resource_kind=resource_kind,
        resource_id=resource_id,
        reference=reference,
    )
