"""Immutable evidence for stage-only and end-to-end Chain evaluation."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from material_workbench.contracts.chain_contracts import ChainContractModel


Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


class ChainEvaluationStageIdentity(ChainContractModel):
    stage_id: Annotated[str, Field(min_length=1)]
    contract_id: Annotated[str, Field(min_length=1)]
    contract_digest: Digest
    package_manifest_digest: Digest
    dataset_profile_digest: Digest | None = None


class ChainEvaluationMetricValue(ChainContractModel):
    mae: Annotated[float, Field(ge=0)]
    rmse: Annotated[float, Field(ge=0)]


class ChainEvaluationTarget(ChainContractModel):
    target: Annotated[str, Field(min_length=1)]
    label: Annotated[str, Field(min_length=1)]
    unit: str
    cohort: Annotated[str, Field(min_length=1)]
    observation_family: Annotated[str, Field(min_length=1)]
    observations: Annotated[int, Field(ge=1)]
    split_groups: Annotated[int, Field(ge=2)]
    stage_only: ChainEvaluationMetricValue
    end_to_end: ChainEvaluationMetricValue


class ChainEvaluationFoldEvidence(ChainContractModel):
    target: Annotated[str, Field(min_length=1)]
    outer_fold: Annotated[int, Field(ge=0)]
    train_groups: Annotated[int, Field(ge=1)]
    test_groups: Annotated[int, Field(ge=1)]
    test_observations: Annotated[int, Field(ge=1)]
    train_group_digest: Digest
    test_group_digest: Digest
    upstream_training_source: Literal["inner-grouped-oof"]
    upstream_test_source: Literal["outer-train-only"]
    upstream_training_predictions: Annotated[int, Field(ge=1)]
    upstream_test_predictions: Annotated[int, Field(ge=1)]
    upstream_self_fit_violations: Literal[0]
    outer_test_training_overlap: Literal[0]


class ChainEvaluationSplit(ChainContractModel):
    strategy: Literal["nested-grouped-outer-k-fold"]
    group_key: Annotated[str, Field(min_length=1)]
    folds: Annotated[int, Field(ge=2)]
    assignment_policy: Literal["sorted-group-round-robin"]
    assignment_digest: Digest
    assignments: dict[str, dict[str, int]]

    @model_validator(mode="after")
    def assignments_are_valid(self) -> "ChainEvaluationSplit":
        for target, assignment in self.assignments.items():
            if not target or not assignment:
                raise ValueError("each target requires a non-empty fold assignment")
            values = sorted(set(assignment.values()))
            if values != list(range(self.folds)):
                raise ValueError("target fold IDs must be contiguous and use every fold")
        return self


class ChainEvaluationReport(ChainContractModel):
    schema_version: Literal["chain-evaluation/v1"] = "chain-evaluation/v1"
    evaluation_id: Annotated[str, Field(min_length=1)]
    chain_id: Annotated[str, Field(min_length=1)]
    source_data_digest: Digest
    stages: Annotated[tuple[ChainEvaluationStageIdentity, ...], Field(min_length=3)]
    split: ChainEvaluationSplit
    metric_definitions: dict[Literal["mae", "rmse"], str]
    targets: Annotated[tuple[ChainEvaluationTarget, ...], Field(min_length=1)]
    fold_evidence: Annotated[tuple[ChainEvaluationFoldEvidence, ...], Field(min_length=1)]
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def evidence_covers_every_target_fold(self) -> "ChainEvaluationReport":
        target_names = [item.target for item in self.targets]
        if len(target_names) != len(set(target_names)):
            raise ValueError("Chain evaluation targets must be unique")
        expected = {
            (target, fold)
            for target in target_names
            for fold in range(self.split.folds)
        }
        actual = {(item.target, item.outer_fold) for item in self.fold_evidence}
        if actual != expected:
            raise ValueError("fold evidence must cover every target and outer fold")
        if set(self.split.assignments) != set(target_names):
            raise ValueError("fold assignments must cover every target")
        if set(self.metric_definitions) != {"mae", "rmse"}:
            raise ValueError("MAE and RMSE definitions are both required")
        return self


class ResolvedChainEvaluation(ChainContractModel):
    report: ChainEvaluationReport
    artifact_digest: Digest
    chain_revision_id: Annotated[str, Field(min_length=1)]
    chain_revision_digest: Digest
    dataset_view_revision_ids: dict[str, str]
