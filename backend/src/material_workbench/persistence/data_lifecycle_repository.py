from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel

from material_workbench.contracts.data_lifecycle_contracts import (
    ApprovedTrainingSnapshot,
    ApprovedTrainingSnapshotDetail,
    ApprovedTrainingSnapshotSummary,
    CanonicalDatasetRevision,
    CanonicalDatasetRevisionSummary,
    ConnectorLifecycleSummary,
    CurationRunRowPage,
    CurationRunSummary,
    CuratedRow,
    CurationRecipe,
    CurationRecipeCreateInput,
    CurationRun,
    FetchAttempt,
    RawSourceSnapshot,
    RawSnapshotRowPage,
    RawSourceSnapshotSummary,
    SourceConnector,
    SourceConnectorCreateInput,
    TrainingSnapshotCreateInput,
)
from material_workbench.persistence.sqlite_connection import sqlite_connection
from material_workbench.persistence.data_lifecycle_payload_storage import (
    LifecyclePayloadUnavailableError,
    hydrate_curation_run,
    hydrate_raw_snapshot,
    store_curation_run,
    store_raw_snapshot,
)
from material_workbench.persistence.row_payload_store import RowPayloadStore
from material_workbench.persistence.row_payload_store import RowPayloadReference
from material_workbench.persistence.data_lifecycle_row_index import (
    rebuild_row_index,
)
from material_workbench.persistence.data_lifecycle_summaries import (
    summarize_canonical,
    summarize_curation,
    summarize_raw,
    summarize_training,
)


class LifecycleResourceNotFoundError(LookupError):
    pass


class LifecycleResourceConflictError(ValueError):
    pass


T = TypeVar("T", bound=BaseModel)


class DataLifecycleRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = str(database)
        self.row_payloads = RowPayloadStore(database)

    def _connect(self):
        return sqlite_connection(self.database)

    def create_connector(self, payload: SourceConnectorCreateInput) -> SourceConnector:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM source_connectors WHERE configuration_digest=?",
                (payload.calculated_configuration_digest,),
            ).fetchone()
            if row is not None:
                return SourceConnector.model_validate_json(row["payload"])
            connector = SourceConnector(
                **payload.model_dump(),
                id=f"connector-{uuid.uuid4()}",
                configuration_digest=payload.calculated_configuration_digest,
                created_at=datetime.now(UTC),
            )
            conn.execute(
                "INSERT INTO source_connectors(id,configuration_digest,payload,created_at) "
                "VALUES (?,?,?,?)",
                (
                    connector.id,
                    connector.configuration_digest,
                    connector.model_dump_json(),
                    connector.created_at.isoformat(),
                ),
            )
        return connector

    def list_connectors(self) -> tuple[SourceConnector, ...]:
        return self._list("source_connectors", SourceConnector, "created_at,id")

    def get_connector(self, connector_id: str) -> SourceConnector:
        return self._get(
            "source_connectors", connector_id, SourceConnector, "Connector"
        )

    def save_attempt(self, attempt: FetchAttempt) -> FetchAttempt:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO source_fetch_attempts("
                "id,connector_id,status,snapshot_id,payload,started_at"
                ") VALUES (?,?,?,?,?,?)",
                (
                    attempt.id,
                    attempt.connector_id,
                    attempt.status,
                    attempt.snapshot_id,
                    attempt.model_dump_json(),
                    attempt.started_at.isoformat(),
                ),
            )
        return attempt

    def list_attempts(self, connector_id: str) -> tuple[FetchAttempt, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM source_fetch_attempts "
                "WHERE connector_id=? ORDER BY started_at,id",
                (connector_id,),
            ).fetchall()
        return tuple(FetchAttempt.model_validate_json(row["payload"]) for row in rows)

    def save_raw_snapshot(
        self, snapshot: RawSourceSnapshot
    ) -> tuple[RawSourceSnapshot, bool]:
        connector = self.get_connector(snapshot.connector_id)
        if (
            connector.configuration_digest
            != snapshot.connector_configuration_digest
            or connector.selection.digest != snapshot.selection_digest
        ):
            raise ValueError("Raw SnapshotのConnector参照digestが一致しません")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,payload,row_payload_sha256,row_payload_bytes,row_count "
                "FROM raw_source_snapshots "
                "WHERE connector_id=? AND content_sha256=? AND selection_digest=?",
                (
                    snapshot.connector_id,
                    snapshot.content_sha256,
                    snapshot.selection_digest,
                ),
            ).fetchone()
            if row is not None:
                return self._hydrate_raw_row(row), True
            stored_payload, reference = store_raw_snapshot(
                snapshot,
                self.row_payloads,
            )
            conn.execute(
                "INSERT INTO raw_source_snapshots("
                "id,connector_id,content_sha256,selection_digest,snapshot_digest,"
                "payload,captured_at,row_payload_sha256,row_payload_bytes,row_count,"
                "summary_payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot.id,
                    snapshot.connector_id,
                    snapshot.content_sha256,
                    snapshot.selection_digest,
                    snapshot.snapshot_digest,
                    stored_payload,
                    snapshot.captured_at.isoformat(),
                    reference.sha256,
                    reference.size_bytes,
                    reference.row_count,
                    summarize_raw(snapshot).model_dump_json(),
                ),
            )
            rebuild_row_index(
                conn,
                self.row_payloads,
                resource_kind="raw_source_snapshot",
                resource_id=snapshot.id,
                reference=reference,
            )
        return snapshot, False

    def list_raw_snapshots(self, connector_id: str) -> tuple[RawSourceSnapshot, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,payload,row_payload_sha256,row_payload_bytes,row_count "
                "FROM raw_source_snapshots "
                "WHERE connector_id=? ORDER BY captured_at,id",
                (connector_id,),
            ).fetchall()
        return tuple(self._hydrate_raw_row(row) for row in rows)

    def get_raw_snapshot(self, snapshot_id: str) -> RawSourceSnapshot:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,payload,row_payload_sha256,row_payload_bytes,row_count "
                "FROM raw_source_snapshots WHERE id=?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            raise LifecycleResourceNotFoundError("Raw Snapshotが見つかりません")
        return self._hydrate_raw_row(row)

    def create_recipe(self, payload: CurationRecipeCreateInput) -> CurationRecipe:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM curation_recipes "
                "WHERE recipe_id=? AND version=?",
                (payload.recipe_id, payload.version),
            ).fetchone()
            if row is not None:
                existing = CurationRecipe.model_validate_json(row["payload"])
                if existing.recipe_digest != payload.calculated_recipe_digest:
                    raise LifecycleResourceConflictError(
                        "同じRecipe IDとversionに異なる定義を登録できません"
                    )
                return existing
            recipe = CurationRecipe(
                **payload.model_dump(),
                id=f"curation-recipe-{uuid.uuid4()}",
                recipe_digest=payload.calculated_recipe_digest,
                created_at=datetime.now(UTC),
            )
            conn.execute(
                "INSERT INTO curation_recipes("
                "id,recipe_id,version,recipe_digest,payload,created_at"
                ") VALUES (?,?,?,?,?,?)",
                (
                    recipe.id,
                    recipe.recipe_id,
                    recipe.version,
                    recipe.recipe_digest,
                    recipe.model_dump_json(),
                    recipe.created_at.isoformat(),
                ),
            )
        return recipe

    def list_recipes(self) -> tuple[CurationRecipe, ...]:
        return self._list(
            "curation_recipes", CurationRecipe, "recipe_id,version"
        )

    def get_recipe(self, recipe_id: str) -> CurationRecipe:
        return self._get(
            "curation_recipes", recipe_id, CurationRecipe, "Curation Recipe"
        )

    def save_curation_run(
        self,
        run: CurationRun,
        *,
        raw: RawSourceSnapshot | None = None,
        recipe: CurationRecipe | None = None,
    ) -> CurationRun:
        raw = raw or self.get_raw_snapshot(run.raw_snapshot_id)
        recipe = recipe or self.get_recipe(run.recipe_id)
        if (
            raw.snapshot_digest != run.raw_snapshot_digest
            or recipe.recipe_digest != run.recipe_digest
        ):
            raise ValueError("Curation RunのRaw／Recipe digestが一致しません")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,payload,row_payload_sha256,row_payload_bytes,row_count "
                "FROM source_curation_runs WHERE curation_digest=?",
                (run.curation_digest,),
            ).fetchone()
            if row is not None:
                return self._hydrate_curation_row(row)
            stored_payload, reference = store_curation_run(
                run,
                self.row_payloads,
            )
            conn.execute(
                "INSERT INTO source_curation_runs("
                "id,raw_snapshot_id,recipe_id,profile_digest,curation_digest,"
                "payload,created_at,row_payload_sha256,row_payload_bytes,row_count,"
                "quality_payload,summary_payload"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.raw_snapshot_id,
                    run.recipe_id,
                    run.profile_digest,
                    run.curation_digest,
                    stored_payload,
                    run.created_at.isoformat(),
                    reference.sha256,
                    reference.size_bytes,
                    reference.row_count,
                    run.quality.model_dump_json(),
                    summarize_curation(run).model_dump_json(),
                ),
            )
            rebuild_row_index(
                conn,
                self.row_payloads,
                resource_kind="curation_run",
                resource_id=run.id,
                reference=reference,
            )
        return run

    def list_curation_runs(self) -> tuple[CurationRun, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id,payload,row_payload_sha256,row_payload_bytes,row_count "
                "FROM source_curation_runs ORDER BY created_at,id"
            ).fetchall()
        return tuple(self._hydrate_curation_row(row) for row in rows)

    def get_curation_run(self, run_id: str) -> CurationRun:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,payload,row_payload_sha256,row_payload_bytes,row_count "
                "FROM source_curation_runs WHERE id=?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise LifecycleResourceNotFoundError("Curation Runが見つかりません")
        return self._hydrate_curation_row(row)

    def previous_curation_run(
        self,
        *,
        connector_id: str,
        recipe_id: str,
        excluding_snapshot_id: str,
    ) -> CurationRun | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run.id,run.payload,run.row_payload_sha256,"
                "run.row_payload_bytes,run.row_count "
                "FROM source_curation_runs run "
                "JOIN raw_source_snapshots raw ON raw.id=run.raw_snapshot_id "
                "WHERE raw.connector_id=? AND run.recipe_id=? "
                "AND run.raw_snapshot_id<>? "
                "ORDER BY run.created_at DESC,run.id DESC LIMIT 1",
                (connector_id, recipe_id, excluding_snapshot_id),
            ).fetchone()
        return None if row is None else self._hydrate_curation_row(row)

    def save_canonical_revision(
        self,
        revision: CanonicalDatasetRevision,
        *,
        run: CurationRun | None = None,
    ) -> CanonicalDatasetRevision:
        run = run or self.get_curation_run(revision.curation_run_id)
        if (
            run.curation_digest != revision.curation_digest
            or run.raw_snapshot_digest != revision.raw_snapshot_digest
            or run.recipe_digest != revision.recipe_digest
            or run.profile_digest != revision.profile_digest
        ):
            raise ValueError("Canonical Dataset Revisionの参照digestが一致しません")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM canonical_dataset_approvals "
                "WHERE dataset_digest=?",
                (revision.dataset_digest,),
            ).fetchone()
            if row is not None:
                return CanonicalDatasetRevision.model_validate_json(row["payload"])
            conn.execute(
                "INSERT INTO canonical_dataset_approvals("
                "id,curation_run_id,dataset_digest,payload,approved_at,summary_payload"
                ") VALUES (?,?,?,?,?,?)",
                (
                    revision.id,
                    revision.curation_run_id,
                    revision.dataset_digest,
                    revision.model_dump_json(),
                    revision.approved_at.isoformat(),
                    summarize_canonical(revision).model_dump_json(),
                ),
            )
        return revision

    def list_canonical_revisions(self) -> tuple[CanonicalDatasetRevision, ...]:
        return self._list(
            "canonical_dataset_approvals",
            CanonicalDatasetRevision,
            "approved_at,id",
        )

    def get_canonical_revision(
        self, revision_id: str
    ) -> CanonicalDatasetRevision:
        return self._get(
            "canonical_dataset_approvals",
            revision_id,
            CanonicalDatasetRevision,
            "Canonical Dataset Revision",
        )

    def save_training_snapshot(
        self,
        snapshot: ApprovedTrainingSnapshot,
        *,
        revision: CanonicalDatasetRevision | None = None,
        run: CurationRun | None = None,
    ) -> ApprovedTrainingSnapshot:
        validated_context = revision is not None and run is not None
        revision = revision or self.get_canonical_revision(
            snapshot.canonical_dataset_revision_id
        )
        if revision.dataset_digest != snapshot.dataset_digest:
            raise ValueError("Training SnapshotのDataset digestが一致しません")
        if (
            snapshot.schema_version == "approved-training-snapshot/v2"
            and not validated_context
        ):
            from material_workbench.domain.data_lifecycle import (
                build_training_snapshot,
            )

            assert snapshot.split is not None
            run = run or self.get_curation_run(revision.curation_run_id)
            rebuilt = build_training_snapshot(
                revision,
                run,
                TrainingSnapshotCreateInput(
                    actor=snapshot.actor,
                    purpose=snapshot.purpose,
                    targets=tuple(
                        {
                            "target_key": cohort.target_key,
                            "field": cohort.target_field,
                        }
                        for cohort in snapshot.target_cohorts
                    ),
                    split=snapshot.split,
                    selection_policy=snapshot.selection_policy,
                ),
                created_at=snapshot.created_at,
            )
            if rebuilt.snapshot_digest != snapshot.snapshot_digest:
                raise ValueError(
                    "Training Snapshotが親Curation Runから再現できません"
                )
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM approved_training_snapshots "
                "WHERE snapshot_digest=?",
                (snapshot.snapshot_digest,),
            ).fetchone()
            if row is not None:
                return ApprovedTrainingSnapshot.model_validate_json(row["payload"])
            conn.execute(
                "INSERT INTO approved_training_snapshots("
                "id,canonical_dataset_revision_id,snapshot_digest,payload,created_at,"
                "summary_payload) VALUES (?,?,?,?,?,?)",
                (
                    snapshot.id,
                    snapshot.canonical_dataset_revision_id,
                    snapshot.snapshot_digest,
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
                    summarize_training(
                        snapshot,
                        revision=revision,
                        run=run,
                    ).model_dump_json(),
                ),
            )
        return snapshot

    def list_training_snapshots(self) -> tuple[ApprovedTrainingSnapshot, ...]:
        return self._list(
            "approved_training_snapshots",
            ApprovedTrainingSnapshot,
            "created_at,id",
        )

    def get_training_snapshot(
        self, snapshot_id: str
    ) -> ApprovedTrainingSnapshot:
        return self._get(
            "approved_training_snapshots",
            snapshot_id,
            ApprovedTrainingSnapshot,
            "Training Snapshot",
        )

    def training_snapshot_detail(
        self, snapshot_id: str
    ) -> ApprovedTrainingSnapshotDetail:
        snapshot = self.get_training_snapshot(snapshot_id)
        return ApprovedTrainingSnapshotDetail(
            snapshot=snapshot,
            summary=self._rebuild_training_summary(snapshot),
        )

    def _rebuild_training_summary(
        self,
        snapshot: ApprovedTrainingSnapshot,
    ) -> ApprovedTrainingSnapshotSummary:
        revision = self.get_canonical_revision(
            snapshot.canonical_dataset_revision_id
        )
        return summarize_training(
            snapshot,
            revision=revision,
            run=self.get_curation_run(revision.curation_run_id),
        )

    def detail(self, connector_id: str) -> ConnectorLifecycleSummary:
        connector = self.get_connector(connector_id)
        with self._connect() as conn:
            raw_rows = conn.execute(
                "SELECT id,summary_payload FROM raw_source_snapshots "
                "WHERE connector_id=? ORDER BY captured_at,id",
                (connector_id,),
            ).fetchall()
            run_rows = conn.execute(
                "SELECT run.id,run.summary_payload "
                "FROM source_curation_runs run "
                "JOIN raw_source_snapshots raw ON raw.id=run.raw_snapshot_id "
                "WHERE raw.connector_id=? ORDER BY run.created_at,run.id",
                (connector_id,),
            ).fetchall()
            revision_rows = conn.execute(
                "SELECT revision.id,revision.summary_payload "
                "FROM canonical_dataset_approvals revision "
                "JOIN source_curation_runs run ON run.id=revision.curation_run_id "
                "JOIN raw_source_snapshots raw ON raw.id=run.raw_snapshot_id "
                "WHERE raw.connector_id=? "
                "ORDER BY revision.approved_at,revision.id",
                (connector_id,),
            ).fetchall()
            training_rows = conn.execute(
                "SELECT training.id,training.payload,training.summary_payload "
                "FROM approved_training_snapshots training "
                "JOIN canonical_dataset_approvals revision "
                "ON revision.id=training.canonical_dataset_revision_id "
                "JOIN source_curation_runs run ON run.id=revision.curation_run_id "
                "JOIN raw_source_snapshots raw ON raw.id=run.raw_snapshot_id "
                "WHERE raw.connector_id=? "
                "ORDER BY training.created_at,training.id",
                (connector_id,),
            ).fetchall()
        for row in (*raw_rows, *run_rows, *revision_rows, *training_rows):
            if row["summary_payload"] is None:
                raise LifecyclePayloadUnavailableError(
                    "lifecycle_summary",
                    str(row["id"]),
                    "summary projection is unavailable",
                )
        raw = tuple(
            RawSourceSnapshotSummary.model_validate_json(row["summary_payload"])
            for row in raw_rows
        )
        runs = tuple(
            CurationRunSummary.model_validate_json(row["summary_payload"])
            for row in run_rows
        )
        revisions = tuple(
            CanonicalDatasetRevisionSummary.model_validate_json(
                row["summary_payload"]
            )
            for row in revision_rows
        )
        training_summaries: list[ApprovedTrainingSnapshotSummary] = []
        audit_fields = {
            "selection_policy",
            "selection_policy_digest",
            "approved_row_count",
            "included_row_count",
            "excluded_row_count",
            "policy_excluded_row_count",
            "reason_counting",
            "exclusion_reasons",
        }
        for row in training_rows:
            summary_payload = json.loads(str(row["summary_payload"]))
            if audit_fields <= summary_payload.keys():
                training_summaries.append(
                    ApprovedTrainingSnapshotSummary.model_validate(
                        summary_payload
                    )
                )
                continue
            snapshot = ApprovedTrainingSnapshot.model_validate_json(
                row["payload"]
            )
            training_summaries.append(
                self._rebuild_training_summary(snapshot)
            )
        training = tuple(training_summaries)
        return ConnectorLifecycleSummary(
            connector=connector,
            attempts=self.list_attempts(connector_id),
            raw_snapshots=raw,
            curation_runs=runs,
            canonical_revisions=revisions,
            training_snapshots=training,
        )

    def raw_row_page(
        self,
        snapshot_id: str,
        *,
        offset: int,
        limit: int,
    ) -> RawSnapshotRowPage:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT raw.id,raw.connector_id,raw.snapshot_digest,"
                "raw.row_payload_sha256,raw.row_payload_bytes,raw.row_count,"
                "manifest.schema_version AS index_schema_version,"
                "manifest.payload_sha256 AS index_payload_sha256,"
                "manifest.row_count AS index_row_count "
                "FROM raw_source_snapshots raw "
                "LEFT JOIN data_lifecycle_row_index_manifests manifest "
                "ON manifest.resource_kind='raw_source_snapshot' "
                "AND manifest.resource_id=raw.id "
                "WHERE raw.id=?",
                (snapshot_id,),
            ).fetchone()
            index_rows = (
                ()
                if row is None
                else conn.execute(
                    "SELECT ordinal,sort_ordinal AS page_ordinal,"
                    "byte_offset,byte_length,line_sha256 "
                    "FROM data_lifecycle_row_index "
                    "WHERE resource_kind='raw_source_snapshot' "
                    "AND resource_id=? AND sort_ordinal>=? "
                    "ORDER BY sort_ordinal LIMIT ?",
                    (snapshot_id, offset, limit),
                ).fetchall()
            )
        if row is None:
            raise LifecycleResourceNotFoundError("Raw Snapshotが見つかりません")
        reference = self._indexed_reference(row, "raw-json-record/v1")
        if reference is None:
            raise LifecyclePayloadUnavailableError(
                "raw_source_snapshot",
                snapshot_id,
                "payload reference is unavailable",
            )
        if (
            row["index_schema_version"] != "lifecycle-row-seek-index/v1"
            or row["index_payload_sha256"] != reference.sha256
            or row["index_row_count"] != reference.row_count
        ):
            raise self._index_unavailable(
                "raw_source_snapshot",
                snapshot_id,
                "row payload index manifest does not match its CAS reference",
            )
        expected_page_count = min(
            limit, max(0, reference.row_count - offset)
        )
        if len(index_rows) != expected_page_count:
            raise self._index_unavailable(
                "raw_source_snapshot",
                snapshot_id,
                "row payload page index is incomplete",
            )
        try:
            page_positions = [
                int(index["page_ordinal"]) for index in index_rows
            ]
        except (TypeError, ValueError):
            page_positions = []
        if page_positions != list(range(offset, offset + expected_page_count)):
            raise self._index_unavailable(
                "raw_source_snapshot",
                snapshot_id,
                "row payload page positions are not contiguous",
            )
        if any(
            int(index["ordinal"]) != int(index["page_ordinal"])
            for index in index_rows
        ):
            raise self._index_unavailable(
                "raw_source_snapshot",
                snapshot_id,
                "row payload source order does not match its index",
            )
        try:
            rows = self.row_payloads.read_ranges(
                reference,
                (
                    (
                        int(index["byte_offset"]),
                        int(index["byte_length"]),
                        str(index["line_sha256"]),
                    )
                    for index in index_rows
                ),
            )
        except Exception as exc:
            if isinstance(exc, LifecyclePayloadUnavailableError):
                raise
            error = LifecyclePayloadUnavailableError(
                "raw_source_snapshot", snapshot_id, str(exc)
            )
            self._record_payload_finding(error)
            raise error from exc
        return RawSnapshotRowPage(
            resource_id=snapshot_id,
            connector_id=str(row["connector_id"]),
            snapshot_digest=str(row["snapshot_digest"]),
            offset=offset,
            limit=limit,
            total=reference.row_count,
            has_more=offset + len(rows) < reference.row_count,
            rows=rows,
        )

    def curation_row_page(
        self,
        run_id: str,
        *,
        offset: int,
        limit: int,
        status: Literal[
            "accepted", "warning", "quarantined", "blocked"
        ] | None = None,
        reasoned_only: bool = False,
    ) -> CurationRunRowPage:
        if status is not None and reasoned_only:
            raise ValueError("status and reasoned_only cannot be combined")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT run.id,raw.connector_id,run.raw_snapshot_id,"
                "run.curation_digest,raw.snapshot_digest AS raw_snapshot_digest,"
                "run.row_payload_sha256,run.row_payload_bytes,run.row_count,"
                "manifest.schema_version AS index_schema_version,"
                "manifest.payload_sha256 AS index_payload_sha256,"
                "manifest.row_count AS index_row_count,"
                "manifest.accepted_count,manifest.warning_count,"
                "manifest.quarantined_count,manifest.blocked_count,"
                "manifest.reasoned_count "
                "FROM source_curation_runs run "
                "JOIN raw_source_snapshots raw ON raw.id=run.raw_snapshot_id "
                "LEFT JOIN data_lifecycle_row_index_manifests manifest "
                "ON manifest.resource_kind='curation_run' "
                "AND manifest.resource_id=run.id "
                "WHERE run.id=?",
                (run_id,),
            ).fetchone()
            total = 0
            if row is not None:
                if reasoned_only:
                    total = int(row["reasoned_count"] or 0)
                elif status is not None:
                    total = int(row[f"{status}_count"] or 0)
                else:
                    total = int(row["index_row_count"] or 0)
            expected_page_count = min(limit, max(0, total - offset))
            include_previous = 0 < offset <= total
            query_offset = offset - 1 if include_previous else offset
            indexed_page_count = expected_page_count + int(include_previous)
            status_clause = " AND status=?" if status is not None else ""
            parameters: tuple[object, ...] = (
                (run_id, status) if status is not None else (run_id,)
            )
            page_clause = (
                " AND reason_ordinal>=? ORDER BY reason_ordinal LIMIT ?"
                if reasoned_only
                else (
                    " AND status_ordinal>=? "
                    "ORDER BY status_ordinal LIMIT ?"
                    if status is not None
                    else " AND sort_ordinal>=? "
                    "ORDER BY sort_ordinal LIMIT ?"
                )
            )
            page_ordinal = (
                "reason_ordinal"
                if reasoned_only
                else "status_ordinal" if status is not None else "sort_ordinal"
            )
            index_rows = (
                ()
                if row is None
                else conn.execute(
                    f"SELECT ordinal,{page_ordinal} AS page_ordinal,"
                    "byte_offset,byte_length,line_sha256,status,"
                    "raw_row_index,row_key,reason_codes "
                    "FROM data_lifecycle_row_index "
                    "WHERE resource_kind='curation_run' AND resource_id=?"
                    + status_clause
                    + page_clause,
                    (*parameters, query_offset, indexed_page_count),
                ).fetchall()
            )
        if row is None:
            raise LifecycleResourceNotFoundError("Curation Runが見つかりません")
        reference = self._indexed_reference(row, "curated-row/v1")
        if reference is None:
            raise LifecyclePayloadUnavailableError(
                "curation_run", run_id, "payload reference is unavailable"
            )
        if (
            row["index_schema_version"] != "lifecycle-row-seek-index/v1"
            or row["index_payload_sha256"] != reference.sha256
            or row["index_row_count"] != reference.row_count
        ):
            raise self._index_unavailable(
                "curation_run",
                run_id,
                "row payload index manifest does not match its CAS reference",
            )
        if len(index_rows) != indexed_page_count:
            raise self._index_unavailable(
                "curation_run", run_id, "row payload page index is incomplete"
            )
        try:
            page_positions = [
                int(index["page_ordinal"]) for index in index_rows
            ]
        except (TypeError, ValueError):
            page_positions = []
        if page_positions != list(
            range(query_offset, query_offset + indexed_page_count)
        ):
            raise self._index_unavailable(
                "curation_run",
                run_id,
                "row payload page positions are not contiguous",
            )
        try:
            payload_rows = self.row_payloads.read_ranges(
                reference,
                (
                    (
                        int(index["byte_offset"]),
                        int(index["byte_length"]),
                        str(index["line_sha256"]),
                    )
                    for index in index_rows
                ),
            )
            indexed_rows = tuple(
                CuratedRow.model_validate(item) for item in payload_rows
            )
        except Exception as exc:
            if isinstance(exc, LifecyclePayloadUnavailableError):
                raise
            error = LifecyclePayloadUnavailableError(
                "curation_run", run_id, str(exc)
            )
            self._record_payload_finding(error)
            raise error from exc
        try:
            metadata_matches = all(
                row_item.status == index["status"]
                and row_item.raw_row_index == index["raw_row_index"]
                and row_item.row_key == index["row_key"]
                and list(row_item.reason_codes)
                == json.loads(index["reason_codes"])
                for row_item, index in zip(indexed_rows, index_rows)
            )
        except (TypeError, json.JSONDecodeError):
            metadata_matches = False
        filter_matches = (
            all(row_item.status == status for row_item in indexed_rows)
            if status is not None
            else True
        )
        reason_filter_matches = (
            all(row_item.reason_codes for row_item in indexed_rows)
            if reasoned_only
            else True
        )
        if not (
            metadata_matches and filter_matches and reason_filter_matches
        ):
            raise self._index_unavailable(
                "curation_run",
                run_id,
                "row payload page metadata does not match its index",
            )
        stable_keys = [
            (
                row_item.raw_row_index,
                row_item.row_key,
                int(index["ordinal"]),
            )
            for row_item, index in zip(indexed_rows, index_rows)
        ]
        if any(
            left >= right
            for left, right in zip(stable_keys, stable_keys[1:])
        ):
            raise self._index_unavailable(
                "curation_run",
                run_id,
                "row payload stable sort does not match its index",
            )
        rows = indexed_rows[1:] if include_previous else indexed_rows
        return CurationRunRowPage(
            resource_id=run_id,
            connector_id=str(row["connector_id"]),
            raw_snapshot_id=str(row["raw_snapshot_id"]),
            raw_snapshot_digest=str(row["raw_snapshot_digest"]),
            curation_digest=str(row["curation_digest"]),
            offset=offset,
            limit=limit,
            total=total,
            has_more=offset + len(rows) < total,
            status_filter=status,
            reasoned_only=reasoned_only,
            rows=rows,
        )

    def _index_unavailable(
        self,
        resource_kind: str,
        resource_id: str,
        reason: str,
    ) -> LifecyclePayloadUnavailableError:
        error = LifecyclePayloadUnavailableError(
            resource_kind, resource_id, reason
        )
        self._record_payload_finding(error)
        return error

    def _record_payload_finding(
        self,
        error: LifecyclePayloadUnavailableError,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO data_lifecycle_payload_findings("
                "resource_kind,resource_id,reason,detected_at"
                ") VALUES (?,?,?,?) "
                "ON CONFLICT(resource_kind,resource_id) DO UPDATE SET "
                "reason=excluded.reason,detected_at=excluded.detected_at",
                (
                    error.resource_kind,
                    error.resource_id,
                    error.reason,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def _clear_payload_finding(
        self,
        resource_kind: str,
        resource_id: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM data_lifecycle_payload_findings "
                "WHERE resource_kind=? AND resource_id=?",
                (resource_kind, resource_id),
            )

    @staticmethod
    def _indexed_reference(row, record_kind: str) -> RowPayloadReference | None:
        values = (
            row["row_payload_sha256"],
            row["row_payload_bytes"],
            row["row_count"],
        )
        if all(value is None for value in values):
            return None
        if any(value is None for value in values):
            raise LifecyclePayloadUnavailableError(
                "row_resource",
                str(row["id"]),
                "indexed payload reference is incomplete",
            )
        return RowPayloadReference(
            record_kind=record_kind,
            sha256=str(values[0]),
            size_bytes=int(values[1]),
            row_count=int(values[2]),
        )

    def _hydrate_raw_row(self, row) -> RawSourceSnapshot:
        return self._hydrate_raw(
            str(row["payload"]),
            expected_reference=self._indexed_reference(
                row, "raw-json-record/v1"
            ),
            expected_resource_id=str(row["id"]),
        )

    def _hydrate_raw(
        self,
        stored: str,
        *,
        expected_reference: RowPayloadReference | None = None,
        expected_resource_id: str | None = None,
    ) -> RawSourceSnapshot:
        try:
            snapshot = hydrate_raw_snapshot(
                stored,
                self.row_payloads,
                expected_reference=expected_reference,
                expected_resource_id=expected_resource_id,
            )
        except LifecyclePayloadUnavailableError as exc:
            self._record_payload_finding(exc)
            raise
        self._clear_payload_finding("raw_source_snapshot", snapshot.id)
        return snapshot

    def _hydrate_curation_row(self, row) -> CurationRun:
        return self._hydrate_curation(
            str(row["payload"]),
            expected_reference=self._indexed_reference(
                row, "curated-row/v1"
            ),
            expected_resource_id=str(row["id"]),
        )

    def _hydrate_curation(
        self,
        stored: str,
        *,
        expected_reference: RowPayloadReference | None = None,
        expected_resource_id: str | None = None,
    ) -> CurationRun:
        try:
            run = hydrate_curation_run(
                stored,
                self.row_payloads,
                expected_reference=expected_reference,
                expected_resource_id=expected_resource_id,
            )
        except LifecyclePayloadUnavailableError as exc:
            self._record_payload_finding(exc)
            raise
        self._clear_payload_finding("curation_run", run.id)
        return run

    def _get(
        self,
        table: str,
        resource_id: str,
        model: type[T],
        label: str,
    ) -> T:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT payload FROM {table} WHERE id=?",  # table is internal allow-list
                (resource_id,),
            ).fetchone()
        if row is None:
            raise LifecycleResourceNotFoundError(f"{label}が見つかりません")
        return model.model_validate_json(row["payload"])

    def _list(self, table: str, model: type[T], order: str) -> tuple[T, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM {table} ORDER BY {order}"  # internal allow-list
            ).fetchall()
        return tuple(model.model_validate_json(row["payload"]) for row in rows)
