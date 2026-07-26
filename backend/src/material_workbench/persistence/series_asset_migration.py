from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from material_workbench.persistence.sqlite_connection import connect_sqlite

from material_workbench.contracts.series_contracts import (
    CoordinateUnitConversion,
    ElapsedOriginNormalization,
    IdenticalDuplicateMerge,
    RawSeriesAsset,
    RawSeriesAssetInput,
    RawSeriesPoint,
    RawSeriesProvenance,
    SeriesNormalizationRecipe,
    StableSortNormalization,
    ValueUnitConversion,
)
from material_workbench.domain.series_curation import canonicalize_series


MIGRATION_ID = "variable-series-assets-v1"
MIGRATION_CHECKSUM = "immutable-raw-canonical-series-v1"


class SeriesAssetMigrationError(RuntimeError):
    pass


def _examples() -> tuple[tuple[RawSeriesAsset, SeriesNormalizationRecipe], ...]:
    captured = datetime(2026, 7, 1, tzinfo=UTC)
    heat_input = RawSeriesAssetInput(
        name="例: 焼鈍温度履歴",
        series_kind="heat_history",
        coordinate_name="経過時間",
        coordinate_unit="min",
        value_name="温度",
        value_unit="K",
        points=tuple(
            RawSeriesPoint(
                coordinate=coordinate,
                value=value,
                source_position=index,
                source_row=index + 2,
            )
            for index, (coordinate, value) in enumerate(
                ((0, 298.15), (1.5, 973.15), (3.0, 1093.15), (5.5, 1093.15), (9.0, 373.15))
            )
        ),
        provenance=RawSeriesProvenance(
            source_kind="demo",
            source_locator="packaged-example/annealing-route",
            source_digest="sha256:demo-annealing-route-v1",
            sheet_name="温度履歴",
            captured_at=captured,
        ),
    )
    heat = RawSeriesAsset(
        **heat_input.model_dump(),
        id="series-demo-heat-route",
        revision=1,
        content_digest=heat_input.calculated_digest,
        created_at=captured,
    )
    heat_recipe = SeriesNormalizationRecipe(
        recipe_id="heat-series-si-v1",
        version="1.0.0",
        steps=(
            CoordinateUnitConversion(kind="convert_coordinate_unit", to_unit="s"),
            ValueUnitConversion(kind="convert_value_unit", to_unit="°C"),
            ElapsedOriginNormalization(kind="elapsed_origin"),
            StableSortNormalization(kind="stable_sort"),
            IdenticalDuplicateMerge(kind="merge_identical_duplicates"),
        ),
    )
    degradation_input = RawSeriesAssetInput(
        name="例: 電池容量劣化曲線",
        series_kind="degradation_curve",
        coordinate_name="サイクル数",
        coordinate_unit="cycle",
        value_name="容量保持率",
        value_unit="%",
        points=tuple(
            RawSeriesPoint(
                coordinate=coordinate,
                value=value,
                source_position=index,
                source_row=index + 2,
            )
            for index, (coordinate, value) in enumerate(
                ((0, 100.0), (25, 98.4), (60, 95.7), (110, 91.3), (180, 86.0), (260, 79.8))
            )
        ),
        provenance=RawSeriesProvenance(
            source_kind="demo",
            source_locator="packaged-example/battery-degradation",
            source_digest="sha256:demo-battery-degradation-v1",
            sheet_name="容量履歴",
            captured_at=captured,
        ),
    )
    degradation = RawSeriesAsset(
        **degradation_input.model_dump(),
        id="series-demo-battery-degradation",
        revision=1,
        content_digest=degradation_input.calculated_digest,
        created_at=captured,
    )
    degradation_recipe = SeriesNormalizationRecipe(
        recipe_id="degradation-series-v1",
        version="1.0.0",
        steps=(
            StableSortNormalization(kind="stable_sort"),
            IdenticalDuplicateMerge(kind="merge_identical_duplicates"),
        ),
    )
    return (
        (heat, heat_recipe),
        (degradation, degradation_recipe),
    )


def migrate_series_assets(database: str | Path) -> None:
    conn = connect_sqlite(database)
    try:
        conn.execute("BEGIN IMMEDIATE")
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations WHERE id=?",
            (MIGRATION_ID,),
        ).fetchone()
        if marker is not None:
            if marker[0] != MIGRATION_CHECKSUM:
                raise SeriesAssetMigrationError(
                    "series asset migration checksum does not match"
                )
            expected = {
                "raw_series_assets": {
                    "id", "content_digest", "payload", "created_at",
                },
                "canonical_series_revisions": {
                    "id", "raw_series_id", "raw_content_digest",
                    "recipe_digest", "canonical_digest", "payload", "created_at",
                },
            }
            for table, columns in expected.items():
                actual = {
                    str(row[1])
                    for row in conn.execute(f"PRAGMA table_info({table})")
                }
                if columns - actual:
                    raise SeriesAssetMigrationError(
                        f"{table} is incomplete"
                    )
            conn.commit()
            return
        for table in ("raw_series_assets", "canonical_series_revisions"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is not None:
                raise SeriesAssetMigrationError(
                    f"{table} exists without its migration marker"
                )
        conn.execute(
            "CREATE TABLE raw_series_assets ("
            "id TEXT PRIMARY KEY,"
            "content_digest TEXT NOT NULL UNIQUE,"
            "payload TEXT NOT NULL,"
            "created_at TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "CREATE TABLE canonical_series_revisions ("
            "id TEXT PRIMARY KEY,"
            "raw_series_id TEXT NOT NULL REFERENCES raw_series_assets(id),"
            "raw_content_digest TEXT NOT NULL,"
            "recipe_digest TEXT NOT NULL,"
            "canonical_digest TEXT NOT NULL UNIQUE,"
            "payload TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "UNIQUE(raw_series_id,raw_content_digest,recipe_digest)"
            ")"
        )
        conn.execute(
            "CREATE INDEX idx_canonical_series_raw "
            "ON canonical_series_revisions(raw_series_id,created_at)"
        )
        for raw, recipe in _examples():
            canonical = canonicalize_series(raw, recipe)
            conn.execute(
                "INSERT INTO raw_series_assets(id,content_digest,payload,created_at) "
                "VALUES (?,?,?,?)",
                (
                    raw.id,
                    raw.content_digest,
                    raw.model_dump_json(),
                    raw.created_at.isoformat(),
                ),
            )
            conn.execute(
                "INSERT INTO canonical_series_revisions("
                "id,raw_series_id,raw_content_digest,recipe_digest,"
                "canonical_digest,payload,created_at"
                ") VALUES (?,?,?,?,?,?,?)",
                (
                    canonical.id,
                    canonical.raw_series_id,
                    canonical.raw_content_digest,
                    canonical.recipe_digest,
                    canonical.canonical_digest,
                    canonical.model_dump_json(),
                    canonical.created_at.isoformat(),
                ),
            )
        conn.execute(
            "INSERT INTO schema_migrations(id,checksum,applied_at) VALUES (?,?,?)",
            (MIGRATION_ID, MIGRATION_CHECKSUM, datetime.now(UTC).isoformat()),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
