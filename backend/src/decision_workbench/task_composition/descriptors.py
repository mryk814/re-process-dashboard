"""Declarative task composition descriptors."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.task_contracts import (
    ApplicationCapability,
    CANONICAL_CANDIDATE_SCHEMA_VERSION,
    DataExplorerCapability,
    TaskDefinition,
)
from decision_workbench.task_composition.ports import (
    CurveFamilyHandler,
    DataLoader,
    FeatureRowBuilder,
    PredictionRuntime,
    ResponseCurveHandler,
    RuntimeFactory,
    SpecializedPackageBuilder,
    TrainingInspectorAdapter,
    TrainingCandidateBuilder,
)


def _canonical_option(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(
            (key, _canonical_option(item))
            for key, item in sorted(value.items())
        )
    if isinstance(value, (list, tuple)):
        return tuple(_canonical_option(item) for item in value)
    return value


@dataclass(frozen=True)
class StandardModelAuthoring:
    candidate_builder: TrainingCandidateBuilder
    estimator_ids: tuple[str, ...]
    positive_targets: frozenset[str] = frozenset()
    default_estimator_id: str | None = None
    default_estimator_options: tuple[tuple[str, Any], ...] = ()
    recipe_policy: Literal["catalog_compatible", "specialized_constraints"] = (
        "catalog_compatible"
    )
    required_estimator_options: tuple[tuple[str, Any], ...] = ()
    specialization_reason: str | None = None
    default_validation_folds: int = 5

    def default_options(self) -> dict[str, Any]:
        return dict(self.default_estimator_options)

    def allowed_estimator_ids(
        self,
        compatible_catalog_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        if self.recipe_policy == "specialized_constraints":
            return self.estimator_ids
        return compatible_catalog_ids

    def resolved_options(
        self,
        options: dict[str, Any] | None,
    ) -> dict[str, Any]:
        resolved = dict(options or {})
        for key, required in self.required_estimator_options:
            if (
                key in resolved
                and _canonical_option(resolved[key])
                != _canonical_option(required)
            ):
                raise ValueError(
                    f"standard recipe option {key} is fixed by Task scientific constraints"
                )
            resolved[key] = required
        return resolved


@dataclass(frozen=True)
class StarterProject:
    project_id: str
    name: str
    candidate_factory: Callable[
        [PredictionRuntime, TaskDefinition],
        list[CandidateInput],
    ]
    seed_on_upgrade: bool = False
    legacy_candidate_factory: Callable[
        [PredictionRuntime, TaskDefinition],
        list[CandidateInput],
    ] | None = None
    distribution: Literal["quickstart", "gallery", "legacy_hidden"] = "legacy_hidden"
    gallery_metadata: "SampleGalleryMetadata | None" = None


@dataclass(frozen=True)
class SampleGalleryMetadata:
    """Editorial context for a user-facing starter Project.

    Task, Package, and capability facts stay in their existing registries.  This
    descriptor owns only the reason a person would choose this particular
    starter, plus its source and limitation wording.
    """

    question: str
    scenario_summary: str
    domain: str
    data_shape: str
    source_kind: Literal["public", "synthetic", "generated_fixture", "bundled_demonstration"]
    source_label: str
    source_url: str
    license: str
    citation: str
    record_summary: str
    limitations: str
    documentation_path: str = ""


@dataclass(frozen=True)
class TaskModule:
    task_id: str
    package_override_env: str
    source_env: str
    source_kind: str
    default_source: Path
    data_loader: DataLoader
    runtime_factory: RuntimeFactory
    feature_row_builder: FeatureRowBuilder
    training_inspector: TrainingInspectorAdapter
    application: ApplicationCapability
    candidate_family_adapter_id: str = CANONICAL_CANDIDATE_SCHEMA_VERSION
    default_package: Path | None = None
    specialized_package_builder: SpecializedPackageBuilder | None = None
    standard_model_authoring: StandardModelAuthoring | None = None
    data_explorer: DataExplorerCapability | None = None
    starter_project: StarterProject | None = None
    response_curve: ResponseCurveHandler | None = None
    curve_family: CurveFamilyHandler | None = None
    default_data_projection: bool = False
