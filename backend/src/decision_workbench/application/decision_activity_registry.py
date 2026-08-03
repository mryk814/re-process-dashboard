"""Allow-listed decision activities and the context handed to each one.

An activity owns its parameters model, its pre-flight validation, and its
computation. The service resolves handlers through this registry, so adding an
activity never adds a branch to an existing one.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from decision_workbench.contracts.decision_activity_contracts import (
    DecisionActivityDefinition,
    DecisionActivityResult,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    Project,
)
from decision_workbench.contracts.prediction_catalog_contracts import ModelMetadata
from decision_workbench.contracts.sampling_identity_contracts import SamplingRequest
from decision_workbench.contracts.task_contracts import TaskDefinition
from decision_workbench.task_composition.candidate_family_adapters import (
    CandidateFamilyAdapter,
)


class DecisionActivityNotFoundError(LookupError):
    pass


class DecisionActivityValidationError(ValueError):
    pass


class ActivityRuntime(Protocol):
    def predict_core(self, candidate: Any, **kwargs: Any) -> dict[str, Any]: ...

    def support_summary(self, candidate: Any) -> Any: ...


@dataclass(frozen=True)
class ActivityContext:
    project: Project
    candidate: Candidate
    task_definition: TaskDefinition
    candidate_family: CandidateFamilyAdapter
    runtime: ActivityRuntime
    parameters: Any
    validate_candidate: Callable[[Candidate], None]
    resolve_candidate: Callable[[str, int], Candidate]
    sampling_request: SamplingRequest | None = None

    def prediction_sampling_kwargs(self) -> dict[str, SamplingRequest]:
        return (
            {}
            if self.sampling_request is None
            else {"sampling_request": self.sampling_request}
        )


@dataclass(frozen=True)
class ActivityComputation:
    model: ModelMetadata
    result: DecisionActivityResult


@dataclass(frozen=True)
class DecisionActivityHandler:
    definition: DecisionActivityDefinition
    parameters_kind: str
    prepare: Callable[[ActivityContext], Any]
    compute: Callable[[ActivityContext, Any], ActivityComputation]


def build_registry() -> dict[str, DecisionActivityHandler]:
    from decision_workbench.application.decision_activity_difference import (
        CANDIDATE_DIFFERENCE_HANDLER,
    )
    from decision_workbench.application.decision_activity_counterfactual import (
        COUNTERFACTUAL_HANDLER,
    )
    from decision_workbench.application.decision_activity_robustness import (
        ROBUSTNESS_HANDLER,
    )

    handlers = (
        ROBUSTNESS_HANDLER,
        CANDIDATE_DIFFERENCE_HANDLER,
        COUNTERFACTUAL_HANDLER,
    )
    registry: dict[str, DecisionActivityHandler] = {}
    for handler in handlers:
        activity_id = handler.definition.activity_id
        if activity_id in registry:
            raise ValueError(f"duplicate decision activity: {activity_id}")
        if handler.definition.result_kind == handler.parameters_kind:
            raise ValueError(
                f"activity parameters and result must use distinct kinds: {activity_id}"
            )
        registry[activity_id] = handler
    return registry
