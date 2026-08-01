"""Immutable, reproducible evaluation evidence for Proposal Strategies."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, model_validator

from decision_workbench.contracts.task_contracts import ContractModel


StrategyLifecycleStatus = Literal[
    "experimental",
    "production",
    "unavailable",
    "no_adopt",
    "retired",
]


class ProposalLabAdoptionMemoInput(ContractModel):
    strategy_id: Annotated[str, Field(min_length=1)]
    status: Literal["experimental", "production", "no_adopt"]
    primary_criterion: Annotated[str, Field(min_length=1)]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    trade_offs: tuple[Annotated[str, Field(min_length=1, max_length=500)], ...] = ()


class ProposalLabCreateRequest(ContractModel):
    run_ids: Annotated[tuple[str, ...], Field(min_length=4)]
    evaluation_fixture_version: Annotated[str, Field(min_length=1)] = (
        "saved-screening-replay/v1"
    )
    adoption_memos: Annotated[
        tuple[ProposalLabAdoptionMemoInput, ...],
        Field(min_length=1),
    ]

    @model_validator(mode="after")
    def unique_inputs(self) -> "ProposalLabCreateRequest":
        if len(set(self.run_ids)) != len(self.run_ids):
            raise ValueError("Proposal LabのRun IDは重複できません")
        memo_ids = [memo.strategy_id for memo in self.adoption_memos]
        if len(set(memo_ids)) != len(memo_ids):
            raise ValueError("同じstrategyのadoption memoは一つだけ保存します")
        return self


class ProposalLabRunMetric(ContractModel):
    run_id: str
    strategy_id: str
    strategy_version: str
    seed: int
    pool_digest: str
    score_digest: str
    selection_digest: str
    evaluated_count: int
    model_call_count: int | None
    runtime_ms: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    memory_peak_bytes: int | None = Field(default=None, ge=0)
    selected_count: int
    goal_achievement_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    feasible_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    constraint_unknown_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    supported_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    caution_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    extrapolated_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    duplicate_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    failure_count: int = Field(ge=0)
    fallback_count: int = Field(ge=0)


class ProposalLabStrategySummary(ContractModel):
    strategy_id: str
    strategy_version: str
    acquisition_id: str
    acquisition_version: str
    acquisition_parameter_digest: Annotated[str, Field(pattern=r"^sha256:")]
    lifecycle_status_at_evaluation: StrategyLifecycleStatus
    required_capabilities: tuple[str, ...]
    unavailable_reasons: tuple[str, ...]
    acquisition_scope: Literal["marginal", "joint"]
    seeds: tuple[int, ...]
    mean_goal_achievement_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    mean_feasible_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    mean_constraint_unknown_rate: float = Field(
        ge=0, le=1, allow_inf_nan=False
    )
    mean_supported_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    extrapolated_rate_range: float = Field(ge=0, le=1, allow_inf_nan=False)
    goal_achievement_rate_range: float = Field(ge=0, le=1, allow_inf_nan=False)


class ProposalLabProtocol(ContractModel):
    schema_version: Literal["proposal-lab-protocol/v1"] = "proposal-lab-protocol/v1"
    digest: Annotated[str, Field(pattern=r"^sha256:")]
    project_id: str
    task_id: str
    package_id: str
    package_digest: str
    runtime_capability_digest: str
    dataset_identity_digest: str
    training_identity_kind: Literal[
        "training_snapshot",
        "legacy_training_data",
    ]
    training_snapshot_id: str
    training_snapshot_digest: str
    design_space_digest: str
    objective_digest: str
    target: str
    generator_id: str
    generator_version: str
    generator_parameters_digest: Annotated[str, Field(pattern=r"^sha256:")]
    selector_id: str
    selector_version: str
    selection_policy_id: str
    selection_policy_version: str
    proposal_count: int
    distance_contract_digest: Annotated[str, Field(pattern=r"^sha256:")]
    incumbent_resolution_digest: Annotated[str, Field(pattern=r"^sha256:")]
    support_policy: str
    pool_multiplier: int
    budget: int
    seeds: tuple[int, ...]
    evaluation_fixture_version: str
    constraint_scope: Literal["known_design_space_and_outcome_only"] = (
        "known_design_space_and_outcome_only"
    )


class ProposalLabAdoptionMemo(ProposalLabAdoptionMemoInput):
    evidence_run_ids: tuple[str, ...]
    registry_changed: Literal[False] = False


class ProposalLabReport(ContractModel):
    schema_version: Literal["proposal-lab-report/v1"] = "proposal-lab-report/v1"
    id: str
    project_id: str
    created_at: datetime
    report_digest: Annotated[str, Field(pattern=r"^sha256:")]
    protocol: ProposalLabProtocol
    runs: tuple[ProposalLabRunMetric, ...]
    strategy_summaries: tuple[ProposalLabStrategySummary, ...]
    adoption_memos: tuple[ProposalLabAdoptionMemo, ...]
    limitations: tuple[str, ...]
