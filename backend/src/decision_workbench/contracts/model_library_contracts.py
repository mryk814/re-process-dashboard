"""Read-only Workspace catalog contracts for reusable model assets."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from decision_workbench.contracts.chain_contracts import (
    GraphDefinitionRef,
    GraphRevisionRef,
    PredictionGraphProjection,
    StageContractSurface,
)


class ModelLibraryContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelAssetState(ModelLibraryContract):
    availability: Literal["available", "degraded", "unavailable"]
    lifecycle: Literal[
        "current",
        "superseded",
        "research_only",
        "compatibility_only",
    ] = "current"
    reason: str = ""
    impact: str = ""
    recovery_hint: str = ""

    @model_validator(mode="after")
    def non_current_state_is_explained(self) -> "ModelAssetState":
        if (
            self.availability != "available" or self.lifecycle != "current"
        ) and not all(
            value.strip()
            for value in (self.reason, self.impact, self.recovery_hint)
        ):
            raise ValueError(
                "non-current Model asset state requires reason, impact, and recovery"
            )
        return self


class ModelLibraryPort(ModelLibraryContract):
    path: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    value_kind: Annotated[str, Field(min_length=1)]
    unit: str | None = None
    quantity: str | None = None
    basis: str | None = None
    required: bool = True


class ModelLibraryDataReference(ModelLibraryContract):
    dataset_view_revision_ids: tuple[str, ...] = ()
    dataset_revision_ids: tuple[str, ...] = ()
    profile_revision_ids: tuple[str, ...] = ()
    profile_digests: tuple[str, ...] = ()
    source_sha256s: tuple[str, ...] = ()
    source_names: tuple[str, ...] = ()
    connector_id: str | None = None
    training_snapshot_id: str | None = None


class ModelLibraryProjectReference(ModelLibraryContract):
    project_id: Annotated[str, Field(min_length=1)]
    project_name: Annotated[str, Field(min_length=1)]
    archived: bool = False


class ModelLibraryTaskAsset(ModelLibraryContract):
    asset_type: Literal["task"] = "task"
    task_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    contract_digest: Annotated[str, Field(min_length=1)]
    state: ModelAssetState
    inputs: tuple[ModelLibraryPort, ...]
    outputs: tuple[ModelLibraryPort, ...]
    package_reference_ids: tuple[str, ...] = ()
    graph_revision_ids: tuple[str, ...] = ()
    project_references: tuple[ModelLibraryProjectReference, ...] = ()
    onboarding_ready: bool
    graph_authoring_ready: bool


class ModelLibraryPackageAsset(ModelLibraryContract):
    asset_type: Literal["package"] = "package"
    reference_id: Annotated[str, Field(min_length=1)]
    package_id: Annotated[str, Field(min_length=1)]
    version: str
    task_id: Annotated[str, Field(min_length=1)]
    task_contract_digest: Annotated[str, Field(min_length=1)]
    manifest_digest: Annotated[str, Field(min_length=1)]
    storage_scope: Literal["bundled", "personal"]
    state: ModelAssetState
    runtime_types: tuple[str, ...] = ()
    predictor_targets: tuple[str, ...] = ()
    quality_summary_available: bool = False
    data_references: ModelLibraryDataReference
    graph_revision_ids: tuple[str, ...] = ()
    project_references: tuple[ModelLibraryProjectReference, ...] = ()


class ModelLibraryTransformAsset(ModelLibraryContract):
    asset_type: Literal["transform"] = "transform"
    transform_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    state: ModelAssetState
    surface: StageContractSurface | None = None
    package_manifest_digest: str | None = None
    graph_revision_ids: tuple[str, ...] = ()


class ModelLibraryGraphStageReference(ModelLibraryContract):
    stage_id: Annotated[str, Field(min_length=1)]
    stage_kind: Literal["task", "deterministic_transform"]
    contract_id: Annotated[str, Field(min_length=1)]
    contract_digest: Annotated[str, Field(min_length=1)]
    package_manifest_digest: Annotated[str, Field(min_length=1)]
    data_references: ModelLibraryDataReference
    available: bool
    reason: str = ""


class ModelLibraryGraphRevision(ModelLibraryContract):
    revision_id: Annotated[str, Field(min_length=1)]
    revision: Annotated[int, Field(ge=1)]
    revision_digest: Annotated[str, Field(min_length=1)]
    state: ModelAssetState
    revision_contract: GraphRevisionRef
    stages: tuple[ModelLibraryGraphStageReference, ...]
    project_references: tuple[ModelLibraryProjectReference, ...] = ()


class ModelLibraryGraphDefinition(ModelLibraryContract):
    definition_id: Annotated[str, Field(min_length=1)]
    definition_digest: Annotated[str, Field(min_length=1)]
    definition: GraphDefinitionRef
    projection: PredictionGraphProjection
    revisions: tuple[ModelLibraryGraphRevision, ...]


class ModelLibraryGraphAsset(ModelLibraryContract):
    asset_type: Literal["graph"] = "graph"
    graph_id: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    state: ModelAssetState
    latest_revision_id: str | None = None
    definitions: tuple[ModelLibraryGraphDefinition, ...]
    compatible_task_ids: tuple[str, ...] = ()
    compatible_transform_ids: tuple[str, ...] = ()
    project_references: tuple[ModelLibraryProjectReference, ...] = ()


class ModelLibraryCatalog(ModelLibraryContract):
    schema_version: Literal["model-library-catalog/v1"] = (
        "model-library-catalog/v1"
    )
    tasks: tuple[ModelLibraryTaskAsset, ...]
    packages: tuple[ModelLibraryPackageAsset, ...]
    transforms: tuple[ModelLibraryTransformAsset, ...]
    graphs: tuple[ModelLibraryGraphAsset, ...]
