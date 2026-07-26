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
)
from material_workbench.persistence.sqlite_connection import sqlite_connection


class LifecycleResourceNotFoundError(LookupError):
    pass


class LifecycleResourceConflictError(ValueError):
    pass


T = TypeVar("T", bound=BaseModel)


class DataLifecycleRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = str(database)

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
                "SELECT payload FROM raw_source_snapshots "
                "WHERE connector_id=? AND content_sha256=? AND selection_digest=?",
                (
                    snapshot.connector_id,
                    snapshot.content_sha256,
                    snapshot.selection_digest,
                ),
            ).fetchone()
            if row is not None:
                return RawSourceSnapshot.model_validate_json(row["payload"]), True
            conn.execute(
                "INSERT INTO raw_source_snapshots("
                "id,connector_id,content_sha256,selection_digest,snapshot_digest,"
                "payload,captured_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    snapshot.id,
                    snapshot.connector_id,
                    snapshot.content_sha256,
                    snapshot.selection_digest,
                    snapshot.snapshot_digest,
                    snapshot.model_dump_json(),
                    snapshot.captured_at.isoformat(),
                ),
            )
        return snapshot, False

    def list_raw_snapshots(self, connector_id: str) -> tuple[RawSourceSnapshot, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM raw_source_snapshots "
                "WHERE connector_id=? ORDER BY captured_at,id",
                (connector_id,),
            ).fetchall()
        return tuple(
            RawSourceSnapshot.model_validate_json(row["payload"]) for row in rows
        )

    def get_raw_snapshot(self, snapshot_id: str) -> RawSourceSnapshot:
        return self._get(
            "raw_source_snapshots",
            snapshot_id,
            RawSourceSnapshot,
            "Raw Snapshot",
        )

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

    def save_curation_run(self, run: CurationRun) -> CurationRun:
        raw = self.get_raw_snapshot(run.raw_snapshot_id)
        recipe = self.get_recipe(run.recipe_id)
        if (
            raw.snapshot_digest != run.raw_snapshot_digest
            or recipe.recipe_digest != run.recipe_digest
        ):
            raise ValueError("Curation RunのRaw／Recipe digestが一致しません")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM source_curation_runs WHERE curation_digest=?",
                (run.curation_digest,),
            ).fetchone()
            if row is not None:
                return CurationRun.model_validate_json(row["payload"])
            conn.execute(
                "INSERT INTO source_curation_runs("
                "id,raw_snapshot_id,recipe_id,profile_digest,curation_digest,"
                "payload,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    run.id,
                    run.raw_snapshot_id,
                    run.recipe_id,
                    run.profile_digest,
                    run.curation_digest,
                    run.model_dump_json(),
                    run.created_at.isoformat(),
                ),
            )
        return run

    def list_curation_runs(self) -> tuple[CurationRun, ...]:
        return self._list("source_curation_runs", CurationRun, "created_at,id")

    def get_curation_run(self, run_id: str) -> CurationRun:
        return self._get(
            "source_curation_runs", run_id, CurationRun, "Curation Run"
        )

    def save_canonical_revision(
        self, revision: CanonicalDatasetRevision
    ) -> CanonicalDatasetRevision:
        run = self.get_curation_run(revision.curation_run_id)
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
        self, snapshot: ApprovedTrainingSnapshot
    ) -> ApprovedTrainingSnapshot:
        revision = self.get_canonical_revision(
            snapshot.canonical_dataset_revision_id
        )
        if revision.dataset_digest != snapshot.dataset_digest:
            raise ValueError("Training SnapshotのDataset digestが一致しません")
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

    def detail(self, connector_id: str) -> ConnectorLifecycleDetail:
        connector = self.get_connector(connector_id)
        raw = self.list_raw_snapshots(connector_id)
        raw_ids = {item.id for item in raw}
        runs = tuple(
            item
            for item in self.list_curation_runs()
            if item.raw_snapshot_id in raw_ids
        )
        run_ids = {item.id for item in runs}
        revisions = tuple(
            item
            for item in self.list_canonical_revisions()
            if item.curation_run_id in run_ids
        )
        revision_ids = {item.id for item in revisions}
        training = tuple(
            item
            for item in self.list_training_snapshots()
            if item.canonical_dataset_revision_id in revision_ids
        )
        return ConnectorLifecycleDetail(
            connector=connector,
            attempts=self.list_attempts(connector_id),
            raw_snapshots=raw,
            curation_runs=runs,
            canonical_revisions=revisions,
            training_snapshots=training,
        )

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
