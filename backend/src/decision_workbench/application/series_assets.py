from __future__ import annotations

from decision_workbench.contracts.series_contracts import (
    CanonicalSeriesRevision,
    RawSeriesAsset,
    RawSeriesAssetInput,
    SeriesAssetDetail,
    SeriesFeatureContract,
    SeriesFeaturePreview,
    SeriesNormalizationRecipe,
)
from decision_workbench.domain.series_curation import (
    build_series_features,
    canonicalize_series,
)
from decision_workbench.persistence.series_repository import SeriesRepository


class SeriesAssetService:
    def __init__(self, repository: SeriesRepository) -> None:
        self.repository = repository

    def create(self, payload: RawSeriesAssetInput) -> SeriesAssetDetail:
        raw = self.repository.create_raw(payload)
        return self.repository.detail(raw.id)

    def list(self) -> tuple[RawSeriesAsset, ...]:
        return self.repository.list_raw()

    def get(self, series_id: str) -> SeriesAssetDetail:
        return self.repository.detail(series_id)

    def canonicalize(
        self,
        series_id: str,
        recipe: SeriesNormalizationRecipe,
    ) -> CanonicalSeriesRevision:
        raw = self.repository.get_raw(series_id)
        return self.repository.save_canonical(
            canonicalize_series(raw, recipe)
        )

    def feature_preview(
        self,
        revision_id: str,
        contract: SeriesFeatureContract,
    ) -> SeriesFeaturePreview:
        revision = self.repository.get_canonical(revision_id)
        return build_series_features(revision, contract)
