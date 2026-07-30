"""Declarative task composition descriptors."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from material_workbench.contracts.schemas import CandidateInput
from material_workbench.contracts.task_contracts import (
    ApplicationCapability,
    DataExplorerCapability,
    TaskDefinition,
)
from material_workbench.task_composition.ports import (
    CurveFamilyHandler,
    DataLoader,
    FeatureRowBuilder,
    PredictionRuntime,
    ResponseCurveHandler,
    RuntimeFactory,
    SpecializedPackageBuilder,
    TrainingCandidateBuilder,
)


@dataclass(frozen=True)
class StandardModelAuthoring:
    candidate_builder: TrainingCandidateBuilder
    estimator_ids: tuple[str, ...]
    positive_targets: frozenset[str] = frozenset()
    default_estimator_id: str | None = None
    default_estimator_options: tuple[tuple[str, Any], ...] = ()

    def default_options(self) -> dict[str, Any]:
        return dict(self.default_estimator_options)


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
    application: ApplicationCapability
    default_package: Path | None = None
    specialized_package_builder: SpecializedPackageBuilder | None = None
    standard_model_authoring: StandardModelAuthoring | None = None
    data_explorer: DataExplorerCapability | None = None
    starter_project: StarterProject | None = None
    response_curve: ResponseCurveHandler | None = None
    curve_family: CurveFamilyHandler | None = None
