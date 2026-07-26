"""Typed, immutable contracts for variable-length scientific series."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.task_contracts import ContractModel
from material_workbench.execution.inference_work_graph import semantic_digest


class RawSeriesPoint(ContractModel):
    coordinate: Annotated[float, Field(allow_inf_nan=False)]
    value: Annotated[float, Field(allow_inf_nan=False)]
    channel: Annotated[str, Field(min_length=1)] = "value"
    source_row: Annotated[int | None, Field(ge=1)] = None
    source_position: Annotated[int, Field(ge=0)]


class RawSeriesProvenance(ContractModel):
    source_kind: Literal["workbook", "database", "object_storage", "manual", "demo"]
    source_locator: Annotated[str, Field(min_length=1)]
    source_digest: Annotated[str, Field(min_length=1)]
    sheet_name: str | None = None
    captured_at: datetime


class RawSeriesAssetInput(ContractModel):
    schema_version: Literal["raw-series/v1"] = "raw-series/v1"
    name: Annotated[str, Field(min_length=1, max_length=160)]
    series_kind: Literal[
        "heat_history",
        "degradation_curve",
        "sensor_trace",
    ]
    coordinate_name: Annotated[str, Field(min_length=1)]
    coordinate_unit: Annotated[str, Field(min_length=1)]
    value_name: Annotated[str, Field(min_length=1)]
    value_unit: Annotated[str, Field(min_length=1)]
    points: Annotated[tuple[RawSeriesPoint, ...], Field(min_length=1, max_length=10_000)]
    provenance: RawSeriesProvenance

    @property
    def calculated_digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))


class RawSeriesAsset(RawSeriesAssetInput):
    id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    content_digest: Annotated[str, Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def digest_matches_payload(self) -> "RawSeriesAsset":
        payload = RawSeriesAssetInput.model_validate(
            self.model_dump(
                mode="json",
                exclude={"id", "revision", "content_digest", "created_at"},
            )
        )
        if payload.calculated_digest != self.content_digest:
            raise ValueError("Raw Seriesのcontent digestが内容と一致しません")
        return self


class CoordinateUnitConversion(ContractModel):
    kind: Literal["convert_coordinate_unit"]
    to_unit: Annotated[str, Field(min_length=1)]


class ValueUnitConversion(ContractModel):
    kind: Literal["convert_value_unit"]
    to_unit: Annotated[str, Field(min_length=1)]


class ElapsedOriginNormalization(ContractModel):
    kind: Literal["elapsed_origin"]


class StableSortNormalization(ContractModel):
    kind: Literal["stable_sort"]


class IdenticalDuplicateMerge(ContractModel):
    kind: Literal["merge_identical_duplicates"]


SeriesNormalizationStep = Annotated[
    CoordinateUnitConversion
    | ValueUnitConversion
    | ElapsedOriginNormalization
    | StableSortNormalization
    | IdenticalDuplicateMerge,
    Field(discriminator="kind"),
]


class SeriesNormalizationRecipe(ContractModel):
    schema_version: Literal["series-normalization-recipe/v1"] = (
        "series-normalization-recipe/v1"
    )
    recipe_id: Annotated[str, Field(min_length=1)]
    version: Annotated[str, Field(min_length=1)]
    steps: tuple[SeriesNormalizationStep, ...] = ()

    @property
    def digest(self) -> str:
        return semantic_digest(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def steps_are_not_repeated(self) -> "SeriesNormalizationRecipe":
        kinds = [item.kind for item in self.steps]
        if len(kinds) != len(set(kinds)):
            raise ValueError("同じ系列正規化stepは重複できません")
        return self


class SeriesQualityFinding(ContractModel):
    severity: Literal["info", "warning", "quarantined", "blocked"]
    reason_code: Literal[
        "too_few_points",
        "coordinate_out_of_order",
        "identical_duplicate",
        "conflicting_duplicate",
        "unsupported_coordinate_unit",
        "unsupported_value_unit",
    ]
    message: str
    source_positions: tuple[int, ...] = ()


class CanonicalSeriesPoint(ContractModel):
    coordinate: Annotated[float, Field(allow_inf_nan=False)]
    value: Annotated[float, Field(allow_inf_nan=False)]
    channel: Annotated[str, Field(min_length=1)]
    source_positions: Annotated[tuple[int, ...], Field(min_length=1)]


class CanonicalSeriesRevision(ContractModel):
    schema_version: Literal["canonical-series/v1"] = "canonical-series/v1"
    id: Annotated[str, Field(min_length=1)]
    raw_series_id: Annotated[str, Field(min_length=1)]
    raw_content_digest: Annotated[str, Field(min_length=1)]
    recipe: SeriesNormalizationRecipe
    recipe_digest: Annotated[str, Field(min_length=1)]
    status: Literal["accepted", "normalized", "warning", "quarantined", "blocked"]
    coordinate_name: str
    coordinate_unit: str
    value_name: str
    value_unit: str
    points: tuple[CanonicalSeriesPoint, ...]
    findings: tuple[SeriesQualityFinding, ...]
    transformation_log: tuple[str, ...]
    canonical_digest: Annotated[str, Field(min_length=1)]
    created_at: datetime

    @model_validator(mode="after")
    def revision_is_self_consistent(self) -> "CanonicalSeriesRevision":
        if self.recipe.digest != self.recipe_digest:
            raise ValueError("Series Recipe digestが定義と一致しません")
        if self.status in {"quarantined", "blocked"} and self.points:
            raise ValueError("隔離・blocked系列はcanonical pointsを公開できません")
        if self.status not in {"quarantined", "blocked"}:
            channels: dict[str, list[float]] = {}
            for point in self.points:
                channels.setdefault(point.channel, []).append(point.coordinate)
            if any(
                later <= earlier
                for coordinates in channels.values()
                for earlier, later in zip(coordinates, coordinates[1:])
            ):
                raise ValueError("Canonical Seriesの座標はchannel内で厳密昇順です")
        expected = semantic_digest(
            {
                "raw_content_digest": self.raw_content_digest,
                "recipe_digest": self.recipe_digest,
                "status": self.status,
                "coordinate_name": self.coordinate_name,
                "coordinate_unit": self.coordinate_unit,
                "value_name": self.value_name,
                "value_unit": self.value_unit,
                "points": [item.model_dump(mode="json") for item in self.points],
                "findings": [item.model_dump(mode="json") for item in self.findings],
                "transformation_log": self.transformation_log,
            }
        )
        if expected != self.canonical_digest:
            raise ValueError("Canonical Series digestが内容と一致しません")
        return self


class SeriesFeatureContract(ContractModel):
    schema_version: Literal["series-feature-contract/v1"] = (
        "series-feature-contract/v1"
    )
    representation_id: Literal[
        "linear_resample_v1",
        "segment_statistics_v1",
        "sequence_tensor_v1",
    ]
    input_schema_version: Literal["canonical-series/v1"] = "canonical-series/v1"
    sample_count: Annotated[int | None, Field(ge=2, le=2048)] = None
    include_coordinate: bool = True

    @model_validator(mode="after")
    def parameters_match_representation(self) -> "SeriesFeatureContract":
        if self.representation_id == "linear_resample_v1" and self.sample_count is None:
            raise ValueError("linear resampleにはsample_countが必要です")
        if self.representation_id != "linear_resample_v1" and self.sample_count is not None:
            raise ValueError("sample_countはlinear resampleだけに指定します")
        return self


class SeriesFeaturePreview(ContractModel):
    canonical_series_id: str
    canonical_digest: str
    feature_contract: SeriesFeatureContract
    feature_contract_digest: str
    feature_names: tuple[str, ...]
    values: tuple[float, ...]
    shape: tuple[int, ...]


class SeriesAssetDetail(ContractModel):
    raw: RawSeriesAsset
    canonical_revisions: tuple[CanonicalSeriesRevision, ...] = ()
