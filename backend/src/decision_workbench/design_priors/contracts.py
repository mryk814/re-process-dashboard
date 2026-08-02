"""Contracts for data-only Design Prior Packages.

These packages model empirical input plausibility ``p(x)`` only.  They never
carry a predictor, executable code, or a feasibility decision.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DESIGN_PRIOR_PACKAGE_SCHEMA_VERSION = "design-prior-package/v1"
DESIGN_PRIOR_OBSERVATIONS_SCHEMA_VERSION = "design-prior-observations/v1"
DESIGN_PRIOR_QUALITY_SCHEMA_VERSION = "design-prior-quality/v1"
MAX_DESIGN_PRIOR_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_DESIGN_PRIOR_PACKAGE_BYTES = 128 * 1024 * 1024
MAX_DESIGN_PRIOR_ROWS = 100_000


class DesignPriorPackageError(ValueError):
    """A Design Prior Package is malformed, unsafe, or incompatible."""


class _PriorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DesignPriorArtifact(_PriorModel):
    path: str
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    bytes: Annotated[int, Field(gt=0, le=MAX_DESIGN_PRIOR_ARTIFACT_BYTES)]
    media_type: Literal["application/json"] = "application/json"

    @field_validator("path")
    @classmethod
    def package_relative_regular_file(cls, value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute() or ".." in path.parts or path.name != value.split("/")[-1]:
            raise ValueError("artifact path must be a package-relative file path")
        if path.suffix.lower() != ".json":
            raise ValueError("artifact path must name a JSON file")
        return value.replace("\\", "/")


class DesignPriorGenerator(_PriorModel):
    generator_id: Literal["empirical_rows", "knn_local"]
    version: Literal["1.0.0"] = "1.0.0"
    observations_artifact: str
    max_neighbors: Annotated[int, Field(ge=1, le=128)] = 8

    @field_validator("observations_artifact")
    @classmethod
    def package_relative_file(cls, value: str) -> str:
        return DesignPriorArtifact(path=value, sha256="0" * 64, bytes=1).path


class DesignPriorSource(_PriorModel):
    dataset_view_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None
    training_snapshot_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def source_identity_present(self) -> "DesignPriorSource":
        if self.dataset_view_digest is None and self.training_snapshot_digest is None:
            raise ValueError("Design PriorにはDataset ViewまたはTraining Snapshot digestが必要です")
        return self


class DesignPriorManifest(_PriorModel):
    schema_version: Literal[DESIGN_PRIOR_PACKAGE_SCHEMA_VERSION]
    package_id: Annotated[str, Field(min_length=1)]
    package_version: Annotated[str, Field(min_length=1)]
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    canonical_input_schema_version: Annotated[str, Field(min_length=1)]
    canonical_input_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    feature_recipe_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")] | None = None
    source: DesignPriorSource
    generators: Annotated[tuple[DesignPriorGenerator, ...], Field(min_length=2)]
    artifacts: Annotated[tuple[DesignPriorArtifact, ...], Field(min_length=1)]
    sampling_seed_policy: Literal["request_seed"] = "request_seed"
    training_code_revision: Annotated[str, Field(min_length=1)]
    quality_report: str

    @field_validator("canonical_input_paths")
    @classmethod
    def unique_paths(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item for item in value) or len(value) != len(set(value)):
            raise ValueError("canonical input paths must be unique and non-empty")
        return value

    @field_validator("quality_report")
    @classmethod
    def quality_report_is_relative(cls, value: str) -> str:
        return DesignPriorArtifact(path=value, sha256="0" * 64, bytes=1).path

    @model_validator(mode="after")
    def artifacts_and_generators_are_complete(self) -> "DesignPriorManifest":
        artifact_paths = {item.path for item in self.artifacts}
        generator_ids = [item.generator_id for item in self.generators]
        if len(generator_ids) != len(set(generator_ids)):
            raise ValueError("Design Prior generator ids must be unique")
        if {"empirical_rows", "knn_local"} - set(generator_ids):
            raise ValueError("P0 requires empirical_rows and knn_local generators")
        if self.quality_report not in artifact_paths:
            raise ValueError("quality report must be declared as an artifact")
        if any(item.observations_artifact not in artifact_paths for item in self.generators):
            raise ValueError("generator observations artifact must be declared")
        return self


class DesignPriorObservation(_PriorModel):
    sample_id: Annotated[str, Field(min_length=1)]
    inputs: dict[str, float | str]


class DesignPriorObservations(_PriorModel):
    schema_version: Literal[DESIGN_PRIOR_OBSERVATIONS_SCHEMA_VERSION]
    rows: Annotated[tuple[DesignPriorObservation, ...], Field(min_length=2, max_length=MAX_DESIGN_PRIOR_ROWS)]

    @model_validator(mode="after")
    def unique_sample_ids(self) -> "DesignPriorObservations":
        ids = [row.sample_id for row in self.rows]
        if len(ids) != len(set(ids)):
            raise ValueError("Design Prior observation sample ids must be unique")
        for row in self.rows:
            for path, value in row.inputs.items():
                if (
                    isinstance(value, (int, float))
                    and not isinstance(value, bool)
                    and not math.isfinite(value)
                ):
                    raise ValueError(
                        "Design Prior numeric inputs must be finite: "
                        f"{row.sample_id}:{path}"
                    )
        return self


class DesignPriorQualityReport(_PriorModel):
    schema_version: Literal[DESIGN_PRIOR_QUALITY_SCHEMA_VERSION]
    rows: Annotated[int, Field(ge=2, le=MAX_DESIGN_PRIOR_ROWS)]
    canonical_input_paths: Annotated[tuple[str, ...], Field(min_length=1)]
    numeric_paths: tuple[str, ...]
    generator_comparison: tuple[
        Literal["empirical_rows@1.0.0", "knn_local@1.0.0"], ...
    ]
    limitations: Annotated[tuple[str, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def paths_and_generators_are_consistent(self) -> "DesignPriorQualityReport":
        canonical = set(self.canonical_input_paths)
        if len(canonical) != len(self.canonical_input_paths):
            raise ValueError("quality report canonical input paths must be unique")
        if len(set(self.numeric_paths)) != len(self.numeric_paths):
            raise ValueError("quality report numeric paths must be unique")
        if not set(self.numeric_paths) <= canonical:
            raise ValueError("quality report numeric paths must be canonical input paths")
        if set(self.generator_comparison) != {
            "empirical_rows@1.0.0",
            "knn_local@1.0.0",
        }:
            raise ValueError("quality report must compare both P0 generators")
        return self


class DesignPriorPackageReference(_PriorModel):
    """Explicit package identity resolved for one immutable Proposal Run."""

    locator: Annotated[str, Field(min_length=1)]
    package_id: Annotated[str, Field(min_length=1)]
    package_version: Annotated[str, Field(min_length=1)]
    manifest_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    generator_id: Literal["empirical_rows", "knn_local"]
    lane: Literal["conservative", "balanced", "frontier"]
    lane_parameter_digest: Annotated[
        str, Field(pattern=r"^sha256:[0-9a-f]{64}$")
    ] | None = None


class DesignPriorSampleEvidence(_PriorModel):
    package_id: str
    package_version: str
    manifest_digest: str
    generator_id: Literal["empirical_rows", "knn_local"]
    generator_version: Literal["1.0.0"]
    lane: Literal["conservative", "balanced", "frontier"]
    lane_parameter_digest: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
    seed: Annotated[int, Field(ge=0)]
    raw_sample_id: str
    neighbor_sample_id: str | None = None
    nearest_neighbor_distance: Annotated[float, Field(ge=0)] | None = None
    typicality_band: Literal["typical", "near_edge", "low_density"]
    unseen_category_combination: bool = False
    design_space_boundary_proximity: Annotated[float, Field(ge=0, le=1)] | None = None
    transformations: tuple[str, ...] = ()
