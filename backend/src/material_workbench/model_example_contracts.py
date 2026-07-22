"""Typed contracts for checked-in, inactive Model Package examples."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


class MixtureComponentDesign(ExampleModel):
    predictor_id: Annotated[str, Field(min_length=1)]
    package_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    target: Annotated[str, Field(min_length=1)]
    unit: str
    weight: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class MixtureWeightProvenance(ExampleModel):
    method: Annotated[str, Field(min_length=1)]
    evaluation_unit: Annotated[str, Field(min_length=1)]
    cross_validation: Annotated[str, Field(min_length=1)]
    training_data_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    code_revision: Annotated[str, Field(min_length=1)]


class PredictiveMixtureDesignFixture(ExampleModel):
    schema_version: Literal["predictive-mixture-design/v1"]
    combination: Literal["distribution_mixture"]
    target: Annotated[str, Field(min_length=1)]
    unit: str
    components: Annotated[tuple[MixtureComponentDesign, ...], Field(min_length=2, max_length=8)]
    weight_provenance: MixtureWeightProvenance
    golden_component_means: dict[str, float]
    golden_mixture_mean: float

    @model_validator(mode="after")
    def coherent_component_contract(self) -> "PredictiveMixtureDesignFixture":
        ids = [item.predictor_id for item in self.components]
        if len(ids) != len(set(ids)):
            raise ValueError("mixture component ids must be unique")
        if any((item.target, item.unit) != (self.target, self.unit) for item in self.components):
            raise ValueError("mixture components must share target and unit")
        if not math.isclose(sum(item.weight for item in self.components), 1.0, rel_tol=0, abs_tol=1e-12):
            raise ValueError("mixture weights must sum to one")
        if set(self.golden_component_means) != set(ids) or any(not math.isfinite(item) for item in self.golden_component_means.values()):
            raise ValueError("golden means must cover every component")
        expected = sum(item.weight * self.golden_component_means[item.predictor_id] for item in self.components)
        if not math.isclose(expected, self.golden_mixture_mean, rel_tol=0, abs_tol=1e-12):
            raise ValueError("golden mixture mean does not match fixed weights")
        return self


def validate_mixture_component_digests(fixture: PredictiveMixtureDesignFixture, actual: dict[str, str]) -> None:
    expected = {item.predictor_id: item.package_digest for item in fixture.components}
    if actual != expected:
        raise ValueError("mixture component digest mismatch requires regeneration")
