from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from material_workbench.contracts.data_lifecycle_contracts import (
    ApprovedTrainingSnapshot,
    CanonicalDatasetRevision,
    CurationRunSummary,
    RawSourceSnapshotSummary,
)
from material_workbench.persistence.data_lifecycle_payload_storage import (
    StoredLifecycleRowResource,
)
from material_workbench.persistence.data_lifecycle_summaries import (
    summarize_canonical,
    summarize_training,
)
from material_workbench.persistence.sqlite_connection import connect_sqlite
from material_workbench.persistence.data_lifecycle_row_index import (
    create_row_index_schema,
    ensure_row_index,
)
from material_workbench.persistence.row_payload_store import (
    RowPayloadError,
    RowPayloadReference,
    RowPayloadStore,
)


MIGRATION_ID = "source-data-lifecycle-detail-summary-v1"
MIGRATION_CHECKSUM = "connector-summary-projections-and-seek-manifest-v4"


def migrate_data_lifecycle_summaries(database: str | Path) -> None:
    connection = connect_sqlite(database)
    try:
        existing = connection.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if existing is not None and existing["checksum"] != MIGRATION_CHECKSUM:
            raise RuntimeError("Data lifecycle summary migration checksum mismatch")
        create_row_index_schema(connection)
        for table in (
            "raw_source_snapshots",
            "source_curation_runs",
            "canonical_dataset_approvals",
            "approved_training_snapshots",
        ):
            columns = {
                str(row["name"])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            }
            if "summary_payload" not in columns:
                connection.execute(
                    f'ALTER TABLE "{table}" ADD COLUMN summary_payload TEXT'
                )

        for row in connection.execute(
            "SELECT id,payload FROM raw_source_snapshots "
            "WHERE summary_payload IS NULL ORDER BY id"
        ):
            wrapper = StoredLifecycleRowResource.model_validate_json(
                row["payload"]
            )
            if wrapper.unavailable_reason is not None:
                continue
            summary = RawSourceSnapshotSummary.model_validate(
                {
                    key: wrapper.resource[key]
                    for key in RawSourceSnapshotSummary.model_fields
                }
            )
            connection.execute(
                "UPDATE raw_source_snapshots SET summary_payload=? WHERE id=?",
                (summary.model_dump_json(), row["id"]),
            )
        store = RowPayloadStore(database)
        for table, resource_kind, record_kind in (
            ("raw_source_snapshots", "raw_source_snapshot", "raw-json-record/v1"),
            ("source_curation_runs", "curation_run", "curated-row/v1"),
        ):
            for row in connection.execute(
                f"SELECT id,row_payload_sha256,row_payload_bytes,row_count "
                f'FROM "{table}" WHERE row_payload_sha256 IS NOT NULL ORDER BY id'
            ):
                reference = RowPayloadReference(
                    record_kind=record_kind,
                    sha256=row["row_payload_sha256"],
                    size_bytes=row["row_payload_bytes"],
                    row_count=row["row_count"],
                )
                try:
                    ensure_row_index(
                        connection,
                        store,
                        resource_kind=resource_kind,
                        resource_id=str(row["id"]),
                        reference=reference,
                    )
                except RowPayloadError as exc:
                    connection.execute(
                        "DELETE FROM data_lifecycle_row_index "
                        "WHERE resource_kind=? AND resource_id=?",
                        (resource_kind, row["id"]),
                    )
                    connection.execute(
                        "DELETE FROM data_lifecycle_row_index_manifests "
                        "WHERE resource_kind=? AND resource_id=?",
                        (resource_kind, row["id"]),
                    )
                    connection.execute(
                        "INSERT INTO data_lifecycle_payload_findings("
                        "resource_kind,resource_id,reason,detected_at"
                        ") VALUES (?,?,?,?) "
                        "ON CONFLICT(resource_kind,resource_id) DO UPDATE SET "
                        "reason=excluded.reason,detected_at=excluded.detected_at",
                        (
                            resource_kind,
                            row["id"],
                            str(exc),
                            datetime.now(UTC).isoformat(),
                        ),
                    )
        for row in connection.execute(
            "SELECT id,payload,row_count FROM source_curation_runs "
            "WHERE summary_payload IS NULL ORDER BY id"
        ):
            wrapper = StoredLifecycleRowResource.model_validate_json(
                row["payload"]
            )
            if wrapper.unavailable_reason is not None:
                continue
            summary = CurationRunSummary.model_validate(
                {
                    **{
                        key: wrapper.resource[key]
                        for key in CurationRunSummary.model_fields
                        if key != "row_count"
                    },
                    "row_count": row["row_count"],
                }
            )
            connection.execute(
                "UPDATE source_curation_runs SET summary_payload=? WHERE id=?",
                (summary.model_dump_json(), row["id"]),
            )
        for row in connection.execute(
            "SELECT id,payload FROM canonical_dataset_approvals "
            "WHERE summary_payload IS NULL ORDER BY id"
        ):
            summary = summarize_canonical(
                CanonicalDatasetRevision.model_validate_json(row["payload"])
            )
            connection.execute(
                "UPDATE canonical_dataset_approvals SET summary_payload=? WHERE id=?",
                (summary.model_dump_json(), row["id"]),
            )
        for row in connection.execute(
            "SELECT id,payload FROM approved_training_snapshots "
            "WHERE summary_payload IS NULL ORDER BY id"
        ):
            summary = summarize_training(
                ApprovedTrainingSnapshot.model_validate_json(row["payload"])
            )
            connection.execute(
                "UPDATE approved_training_snapshots SET summary_payload=? WHERE id=?",
                (summary.model_dump_json(), row["id"]),
            )
        if existing is None:
            connection.execute(
                "INSERT INTO schema_migrations(id,checksum,applied_at) "
                "VALUES (?,?,?)",
                (MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now(UTC).isoformat()),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
