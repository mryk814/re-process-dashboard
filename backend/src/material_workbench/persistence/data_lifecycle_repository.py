from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from material_workbench.contracts.data_lifecycle_contracts import (
    ApprovedTrainingSnapshot,
    CanonicalDatasetRevision,
    ConnectorLifecycleDetail,
    CurationRecipe,
    CurationRecipeCreateInput,
    CurationRun,
    FetchAttempt,
    RawSourceSnapshot,
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
                "payload,captured_at,row_payload_sha256,row_payload_bytes,row_count"
                ") VALUES (?,?,?,?,?,?,?,?,?,?)",
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
                ),
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
                "quality_payload"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?)",
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
                ),
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
                "id,curation_run_id,dataset_digest,payload,approved_at"
                ") VALUES (?,?,?,?,?)",
                (
                    revision.id,
                    revision.curation_run_id,
                    revision.dataset_digest,
                    revision.model_dump_json(),
                    revision.approved_at.isoformat(),
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
                "id,canonical_dataset_revision_id,snapshot_digest,payload,created_at"
                ") VALUES (?,?,?,?,?)",
                (
                    snapshot.id,
                    snapshot.canonical_dataset_revision_id,
                    snapshot.snapshot_digest,
                    snapshot.model_dump_json(),
                    snapshot.created_at.isoformat(),
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

    def detail(self, connector_id: str) -> ConnectorLifecycleDetail:
        connector = self.get_connector(connector_id)
        raw = self.list_raw_snapshots(connector_id)
        with self._connect() as conn:
            run_rows = conn.execute(
                "SELECT run.id,run.payload,run.row_payload_sha256,"
                "run.row_payload_bytes,run.row_count "
                "FROM source_curation_runs run "
                "JOIN raw_source_snapshots raw ON raw.id=run.raw_snapshot_id "
                "WHERE raw.connector_id=? ORDER BY run.created_at,run.id",
                (connector_id,),
            ).fetchall()
            revision_rows = conn.execute(
                "SELECT revision.payload FROM canonical_dataset_approvals revision "
                "JOIN source_curation_runs run ON run.id=revision.curation_run_id "
                "JOIN raw_source_snapshots raw ON raw.id=run.raw_snapshot_id "
                "WHERE raw.connector_id=? "
                "ORDER BY revision.approved_at,revision.id",
                (connector_id,),
            ).fetchall()
            training_rows = conn.execute(
                "SELECT training.payload FROM approved_training_snapshots training "
                "JOIN canonical_dataset_approvals revision "
                "ON revision.id=training.canonical_dataset_revision_id "
                "JOIN source_curation_runs run ON run.id=revision.curation_run_id "
                "JOIN raw_source_snapshots raw ON raw.id=run.raw_snapshot_id "
                "WHERE raw.connector_id=? "
                "ORDER BY training.created_at,training.id",
                (connector_id,),
            ).fetchall()
        runs = tuple(
            self._hydrate_curation_row(row) for row in run_rows
        )
        revisions = tuple(
            CanonicalDatasetRevision.model_validate_json(row["payload"])
            for row in revision_rows
        )
        training = tuple(
            ApprovedTrainingSnapshot.model_validate_json(row["payload"])
            for row in training_rows
        )
        return ConnectorLifecycleDetail(
            connector=connector,
            attempts=self.list_attempts(connector_id),
            raw_snapshots=raw,
            curation_runs=runs,
            canonical_revisions=revisions,
            training_snapshots=training,
        )

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
