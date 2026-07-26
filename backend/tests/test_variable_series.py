from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from material_workbench.contracts.series_contracts import (
    CoordinateUnitConversion,
    ElapsedOriginNormalization,
    IdenticalDuplicateMerge,
    RawSeriesAsset,
    RawSeriesAssetInput,
    RawSeriesPoint,
    RawSeriesProvenance,
    SeriesFeatureContract,
    SeriesNormalizationRecipe,
    StableSortNormalization,
    ValueUnitConversion,
)
from material_workbench.domain.series_curation import (
    build_series_features,
    canonicalize_series,
)
from material_workbench.modeling.model_packages import (
    FeaturePipelineDocument,
    PipelineFeatureSpec,
)
from material_workbench.persistence.store import Store


def _raw(
    *,
    points=((2.0, 373.15), (0.0, 293.15), (1.0, 323.15)),
    coordinate_unit="min",
    value_unit="K",
    name="可変長温度履歴",
) -> RawSeriesAsset:
    payload = RawSeriesAssetInput(
        name=name,
        series_kind="heat_history",
        coordinate_name="経過時間",
        coordinate_unit=coordinate_unit,
        value_name="温度",
        value_unit=value_unit,
        points=tuple(
            RawSeriesPoint(
                coordinate=coordinate,
                value=value,
                source_position=index,
                source_row=index + 2,
            )
            for index, (coordinate, value) in enumerate(points)
        ),
        provenance=RawSeriesProvenance(
            source_kind="workbook",
            source_locator="fixture.xlsx",
            source_digest="sha256:fixture",
            sheet_name="系列",
            captured_at=datetime(2026, 7, 26, tzinfo=UTC),
        ),
    )
    return RawSeriesAsset(
        **payload.model_dump(),
        id="raw-fixture",
        revision=1,
        content_digest=payload.calculated_digest,
        created_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def _recipe() -> SeriesNormalizationRecipe:
    return SeriesNormalizationRecipe(
        recipe_id="heat-si",
        version="1.0.0",
        steps=(
            CoordinateUnitConversion(
                kind="convert_coordinate_unit",
                to_unit="s",
            ),
            ValueUnitConversion(kind="convert_value_unit", to_unit="°C"),
            ElapsedOriginNormalization(kind="elapsed_origin"),
            StableSortNormalization(kind="stable_sort"),
            IdenticalDuplicateMerge(kind="merge_identical_duplicates"),
        ),
    )


def test_series_migration_is_additive_idempotent_and_seeds_two_shapes(
    tmp_path,
) -> None:
    database = tmp_path / "workbench.db"
    Store(database)
    Store(database)

    with sqlite3.connect(database) as conn:
        marker = conn.execute(
            "SELECT checksum FROM schema_migrations "
            "WHERE id='variable-series-assets-v1'"
        ).fetchone()
        raw_count = conn.execute(
            "SELECT COUNT(*) FROM raw_series_assets"
        ).fetchone()[0]
        canonical_count = conn.execute(
            "SELECT COUNT(*) FROM canonical_series_revisions"
        ).fetchone()[0]

    assert marker == ("immutable-raw-canonical-series-v1",)
    assert raw_count == 2
    assert canonical_count == 2


def test_raw_and_canonical_series_remain_separate_and_reproducible() -> None:
    raw = _raw()
    canonical = canonicalize_series(raw, _recipe())
    repeated = canonicalize_series(raw, _recipe())

    assert raw.points[0].coordinate == 2.0
    assert raw.points[0].value == 373.15
    assert canonical.status == "normalized"
    assert canonical.coordinate_unit == "s"
    assert canonical.value_unit == "°C"
    assert [point.coordinate for point in canonical.points] == [0.0, 60.0, 120.0]
    assert [point.value for point in canonical.points] == pytest.approx(
        [20.0, 50.0, 100.0]
    )
    assert canonical.canonical_digest == repeated.canonical_digest
    assert canonical.recipe_digest == _recipe().digest
    assert any(
        finding.reason_code == "coordinate_out_of_order"
        for finding in canonical.findings
    )


def test_conflicting_duplicate_is_quarantined_without_repairing_raw() -> None:
    raw = _raw(
        points=((0.0, 20.0), (1.0, 30.0), (1.0, 35.0)),
        coordinate_unit="s",
        value_unit="°C",
    )
    canonical = canonicalize_series(
        raw,
        SeriesNormalizationRecipe(
            recipe_id="safe-sort",
            version="1",
            steps=(
                StableSortNormalization(kind="stable_sort"),
                IdenticalDuplicateMerge(kind="merge_identical_duplicates"),
            ),
        ),
    )

    assert canonical.status == "quarantined"
    assert canonical.points == ()
    finding = next(
        item
        for item in canonical.findings
        if item.reason_code == "conflicting_duplicate"
    )
    assert finding.source_positions == (1, 2)
    assert [point.value for point in raw.points] == [20.0, 30.0, 35.0]


def test_out_of_order_series_requires_explicit_stable_sort() -> None:
    canonical = canonicalize_series(
        _raw(coordinate_unit="s", value_unit="°C"),
        SeriesNormalizationRecipe(recipe_id="no-sort", version="1"),
    )

    assert canonical.status == "blocked"
    assert canonical.points == ()
    assert canonical.findings[0].reason_code == "coordinate_out_of_order"


def test_feature_representation_is_separate_from_curation() -> None:
    canonical = canonicalize_series(_raw(), _recipe())
    original_points = canonical.points
    resampled = build_series_features(
        canonical,
        SeriesFeatureContract(
            representation_id="linear_resample_v1",
            sample_count=5,
            include_coordinate=False,
        ),
    )
    sequence = build_series_features(
        canonical,
        SeriesFeatureContract(
            representation_id="sequence_tensor_v1",
            include_coordinate=True,
        ),
    )

    assert canonical.points == original_points
    assert resampled.shape == (1, 5)
    assert len(resampled.values) == 5
    assert sequence.shape == (3, 2)
    assert len(sequence.values) == 6
    assert resampled.canonical_digest == canonical.canonical_digest
    assert resampled.feature_contract_digest != sequence.feature_contract_digest


def test_model_pipeline_can_pin_a_series_representation() -> None:
    document = FeaturePipelineDocument(
        id="series-pipeline",
        version="1",
        canonical_input_paths=("series.capacity",),
        features=(
            PipelineFeatureSpec(
                name="capacity_curve",
                unit="%",
                meaning="容量劣化曲線",
                group="other",
            ),
        ),
        series_representations=(
            SeriesFeatureContract(
                representation_id="sequence_tensor_v1",
                include_coordinate=True,
            ),
        ),
    )

    assert document.series_representations[0].input_schema_version == (
        "canonical-series/v1"
    )


def test_nonfinite_raw_value_is_rejected_before_persistence() -> None:
    with pytest.raises(ValidationError):
        RawSeriesPoint(
            coordinate=0,
            value=float("nan"),
            source_position=0,
        )


def test_series_api_displays_raw_canonical_history_and_feature_provenance(
    client,
) -> None:
    listed = client.get("/api/series-assets")
    assert listed.status_code == 200
    examples = listed.json()
    assert {item["series_kind"] for item in examples} >= {
        "heat_history",
        "degradation_curve",
    }
    assert len({len(item["points"]) for item in examples}) > 1

    payload = _raw(name="API登録系列").model_dump(
        mode="json",
        exclude={"id", "revision", "content_digest", "created_at"},
    )
    created = client.post("/api/series-assets", json=payload)
    assert created.status_code == 201, created.text
    raw = created.json()["raw"]
    canonicalized = client.post(
        f"/api/series-assets/{raw['id']}/canonical-revisions",
        json=_recipe().model_dump(mode="json"),
    )
    assert canonicalized.status_code == 201, canonicalized.text
    revision = canonicalized.json()
    restored = client.get(f"/api/series-assets/{raw['id']}")
    assert restored.status_code == 200
    assert restored.json()["raw"]["points"] == raw["points"]
    assert restored.json()["canonical_revisions"][-1] == revision

    preview = client.post(
        f"/api/canonical-series/{revision['id']}/feature-preview",
        json={
            "representation_id": "segment_statistics_v1",
            "include_coordinate": True,
        },
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["canonical_digest"] == revision["canonical_digest"]
    assert preview.json()["shape"] == [1, 5]
