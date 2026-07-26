from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from material_workbench.contracts.series_contracts import (
    CanonicalSeriesRevision,
    RawSeriesAsset,
    RawSeriesAssetInput,
    SeriesAssetDetail,
)


class SeriesAssetNotFoundError(LookupError):
    pass


class SeriesRepository:
    def __init__(self, database: str | Path) -> None:
        self.database = str(database)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database)
        conn.row_factory = sqlite3.Row
        return conn

    def create_raw(self, payload: RawSeriesAssetInput) -> RawSeriesAsset:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT payload FROM raw_series_assets WHERE content_digest=?",
                (payload.calculated_digest,),
            ).fetchone()
            if existing is not None:
                return RawSeriesAsset.model_validate_json(existing["payload"])
            created = RawSeriesAsset(
                **payload.model_dump(),
                id=f"series-{uuid.uuid4()}",
                revision=1,
                content_digest=payload.calculated_digest,
                created_at=datetime.now(UTC),
            )
            conn.execute(
                "INSERT INTO raw_series_assets(id,content_digest,payload,created_at) "
                "VALUES (?,?,?,?)",
                (
                    created.id,
                    created.content_digest,
                    created.model_dump_json(),
                    created.created_at.isoformat(),
                ),
            )
        return created

    def list_raw(self) -> tuple[RawSeriesAsset, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM raw_series_assets ORDER BY created_at,id"
            ).fetchall()
        return tuple(
            RawSeriesAsset.model_validate_json(row["payload"]) for row in rows
        )

    def get_raw(self, series_id: str) -> RawSeriesAsset:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM raw_series_assets WHERE id=?",
                (series_id,),
            ).fetchone()
        if row is None:
            raise SeriesAssetNotFoundError("Raw Seriesが見つかりません")
        return RawSeriesAsset.model_validate_json(row["payload"])

    def save_canonical(
        self, revision: CanonicalSeriesRevision
    ) -> CanonicalSeriesRevision:
        raw = self.get_raw(revision.raw_series_id)
        if raw.content_digest != revision.raw_content_digest:
            raise ValueError(
                "Canonical Seriesの参照するRaw digestが保存済みRaw Seriesと一致しません"
            )
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT payload FROM canonical_series_revisions "
                "WHERE raw_series_id=? AND raw_content_digest=? AND recipe_digest=?",
                (
                    revision.raw_series_id,
                    revision.raw_content_digest,
                    revision.recipe_digest,
                ),
            ).fetchone()
            if existing is not None:
                return CanonicalSeriesRevision.model_validate_json(
                    existing["payload"]
                )
            conn.execute(
                "INSERT INTO canonical_series_revisions("
                "id,raw_series_id,raw_content_digest,recipe_digest,"
                "canonical_digest,payload,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    revision.id,
                    revision.raw_series_id,
                    revision.raw_content_digest,
                    revision.recipe_digest,
                    revision.canonical_digest,
                    revision.model_dump_json(),
                    revision.created_at.isoformat(),
                ),
            )
        return revision

    def list_canonical(
        self, series_id: str
    ) -> tuple[CanonicalSeriesRevision, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM canonical_series_revisions "
                "WHERE raw_series_id=? ORDER BY created_at,id",
                (series_id,),
            ).fetchall()
        return tuple(
            CanonicalSeriesRevision.model_validate_json(row["payload"])
            for row in rows
        )

    def get_canonical(self, revision_id: str) -> CanonicalSeriesRevision:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM canonical_series_revisions WHERE id=?",
                (revision_id,),
            ).fetchone()
        if row is None:
            raise SeriesAssetNotFoundError(
                "Canonical Series revisionが見つかりません"
            )
        return CanonicalSeriesRevision.model_validate_json(row["payload"])

    def detail(self, series_id: str) -> SeriesAssetDetail:
        return SeriesAssetDetail(
            raw=self.get_raw(series_id),
            canonical_revisions=self.list_canonical(series_id),
        )
