"""Typed contracts for checked-in, inactive Model Package examples."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .model_packages import PredictiveSummary
from .task_contracts import TargetRuntimeCapability


class ExampleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExampleSmokeInput(ExampleModel):
    predictor_id: str
    features: dict[str, float]
    seed: int = 0

    @field_validator("features")
    @classmethod
    def finite_features(cls, value: dict[str, float]) -> dict[str, float]:
        if not value or any(not math.isfinite(item) for item in value.values()):
            raise ValueError("smoke features must be finite and nonempty")
        return value


class ExampleSmokeExpected(ExampleModel):
    summary: PredictiveSummary
    capability: TargetRuntimeCapability


class ExampleQualityReport(ExampleModel):
    schema_version: Literal["model-example-quality/v1"]
    evaluation_unit: Annotated[str, Field(min_length=1)]
    metrics: dict[str, float]
    notes: tuple[str, ...] = ()

    @field_validator("metrics")
    @classmethod
    def finite_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        if not value or any(not key or not math.isfinite(item) for key, item in value.items()):
            raise ValueError("quality metrics must be finite and nonempty")
        return value
