from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from material_workbench.api.dependencies import get_store
from material_workbench.application.series_assets import SeriesAssetService
from material_workbench.contracts.series_contracts import (
    CanonicalSeriesRevision,
    RawSeriesAsset,
    RawSeriesAssetInput,
    SeriesAssetDetail,
    SeriesFeatureContract,
    SeriesFeaturePreview,
    SeriesNormalizationRecipe,
)
from material_workbench.domain.series_curation import SeriesCurationError
from material_workbench.persistence.series_repository import (
    SeriesAssetNotFoundError,
    SeriesRepository,
)
from material_workbench.persistence.store import Store


router = APIRouter()


def get_series_asset_service(
    store: Annotated[Store, Depends(get_store)],
) -> SeriesAssetService:
    return SeriesAssetService(SeriesRepository(store.path))


ServiceDependency = Annotated[
    SeriesAssetService,
    Depends(get_series_asset_service),
]


def _raise_series_error(exc: Exception) -> None:
    if isinstance(exc, SeriesAssetNotFoundError):
        raise HTTPException(404, str(exc)) from exc
    raise HTTPException(422, str(exc)) from exc


@router.get("/api/series-assets", response_model=tuple[RawSeriesAsset, ...])
def list_series_assets(service: ServiceDependency) -> tuple[RawSeriesAsset, ...]:
    return service.list()


@router.post(
    "/api/series-assets",
    response_model=SeriesAssetDetail,
    status_code=201,
)
def create_series_asset(
    payload: RawSeriesAssetInput,
    service: ServiceDependency,
) -> SeriesAssetDetail:
    return service.create(payload)


@router.get(
    "/api/series-assets/{series_id}",
    response_model=SeriesAssetDetail,
)
def get_series_asset(
    series_id: str,
    service: ServiceDependency,
) -> SeriesAssetDetail:
    try:
        return service.get(series_id)
    except SeriesAssetNotFoundError as exc:
        _raise_series_error(exc)


@router.post(
    "/api/series-assets/{series_id}/canonical-revisions",
    response_model=CanonicalSeriesRevision,
    status_code=201,
)
def canonicalize_series_asset(
    series_id: str,
    recipe: SeriesNormalizationRecipe,
    service: ServiceDependency,
) -> CanonicalSeriesRevision:
    try:
        return service.canonicalize(series_id, recipe)
    except (SeriesAssetNotFoundError, SeriesCurationError) as exc:
        _raise_series_error(exc)


@router.post(
    "/api/canonical-series/{revision_id}/feature-preview",
    response_model=SeriesFeaturePreview,
)
def preview_series_features(
    revision_id: str,
    contract: SeriesFeatureContract,
    service: ServiceDependency,
) -> SeriesFeaturePreview:
    try:
        return service.feature_preview(revision_id, contract)
    except (SeriesAssetNotFoundError, SeriesCurationError) as exc:
        _raise_series_error(exc)
