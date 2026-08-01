"""Build immutable Proposal Lab reports from comparable saved Screening Runs."""
from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from decision_workbench.application.proposal_strategy_registry import STRATEGIES
from decision_workbench.application.screening import _screening_project_lock
from decision_workbench.contracts.proposal_lab_contracts import (
    ProposalLabAdoptionMemo,
    ProposalLabCreateRequest,
    ProposalLabProtocol,
    ProposalLabReport,
    ProposalLabRunMetric,
    ProposalLabStrategySummary,
)
from decision_workbench.contracts.screening_contracts import ScreeningRunResponse
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import ProjectNotFoundError, Store


class ProposalLabValidationError(ValueError):
    pass


class ProposalLabNotFoundError(LookupError):
    pass


def _identity(run: ScreeningRunResponse, fixture_version: str) -> dict[str, Any]:
    strategy = run.proposal_strategy
    diagnostics = run.proposal_diagnostics
    lifecycle = run.model_provenance.source_lifecycle
    package = run.model_provenance.package
    training_data = run.model_provenance.training_data
    if strategy is None or diagnostics is None:
        raise ProposalLabValidationError(f"Run {run.id}にProposal証跡がありません")
    if run.purpose != "goal_search" or run.schema_version != "screening-run/v8":
        raise ProposalLabValidationError(
            f"Run {run.id}は保存済みgoal_search v8ではありません"
        )
    if (
        not run.design_space_digest
        or not run.objective_definition_digest
        or package is None
        or not package.id
        or not package.manifest_sha256
        or not strategy.runtime_capability_digest
        or training_data is None
    ):
        raise ProposalLabValidationError(f"Run {run.id}の固定identityが不足しています")
    return {
        "project_id": run.project_id,
        "task_id": run.objective_definition.task_id if run.objective_definition else "",
        "package_id": package.id if package else "",
        "package_digest": package.manifest_sha256 if package else "",
        "runtime_capability_digest": strategy.runtime_capability_digest or "",
        "dataset_identity_digest": semantic_digest(
            training_data.model_dump(mode="json")
        ),
        "training_identity_kind": (
            "training_snapshot" if lifecycle else "legacy_training_data"
        ),
        "training_snapshot_id": (
            lifecycle.training_snapshot_id
            if lifecycle
            else training_data.training_data_id
        ),
        "training_snapshot_digest": (
            lifecycle.training_snapshot_digest
            if lifecycle
            else semantic_digest(training_data.model_dump(mode="json"))
        ),
        "design_space_digest": run.design_space_digest,
        "objective_digest": run.objective_definition_digest,
        "target": run.target,
        "base_inputs": (
            run.base_inputs.model_dump(mode="json") if run.base_inputs else None
        ),
        "variables": {
            key: value.model_dump(mode="json")
            for key, value in run.variables.items()
        },
        "generator_id": strategy.generator_id,
        "generator_version": strategy.generator_version,
        "generator_parameters_digest": semantic_digest(
            strategy.generator_parameters
        ),
        "selector_id": strategy.selector_id,
        "selector_version": strategy.selector_version,
        "selection_policy_id": (
            run.proposal_selection.policy_id if run.proposal_selection else ""
        ),
        "selection_policy_version": (
            run.proposal_selection.policy_version if run.proposal_selection else ""
        ),
        "proposal_count": (
            run.proposal_selection.requested_count if run.proposal_selection else 0
        ),
        "distance_contract_digest": semantic_digest(
            {
                "id": strategy.distance_id,
                "version": strategy.distance_version,
                "parameters": strategy.distance_parameters,
                "effective_diversity_weight": (
                    run.proposal_selection.effective_diversity_weight
                    if run.proposal_selection
                    else None
                ),
            }
        ),
        "incumbent_resolution_digest": semantic_digest(
            strategy.incumbent_resolution.model_dump(mode="json")
            if strategy.incumbent_resolution
            else None
        ),
        "support_policy": strategy.support_policy,
        "pool_multiplier": strategy.pool_multiplier,
        "budget": run.samples * strategy.pool_multiplier,
        "evaluation_fixture_version": fixture_version,
    }


def _run_metric(run: ScreeningRunResponse) -> ProposalLabRunMetric:
    assert run.proposal_strategy is not None
    assert run.proposal_diagnostics is not None
    selected_indices = (
        {item.point_index for item in run.proposal_selection.selected}
        if run.proposal_selection
        else {point.index for point in run.representative_points}
    )
    selected = [point for point in run.points if point.index in selected_indices]
    count = len(selected)
    if count == 0:
        raise ProposalLabValidationError(f"Run {run.id}に選択済み候補がありません")

    def ratio(predicate) -> float:
        return sum(1 for point in selected if predicate(point)) / count

    identities = [semantic_digest(point.inputs) for point in selected]
    diagnostics = run.proposal_diagnostics
    return ProposalLabRunMetric(
        run_id=run.id,
        strategy_id=run.proposal_strategy.id,
        strategy_version=run.proposal_strategy.version,
        seed=run.seed,
        pool_digest=semantic_digest(
            [item.model_dump(mode="json") for item in run.proposal_pool]
        ),
        score_digest=semantic_digest(
            [
                {
                    "pool_index": item.pool_index,
                    "score": item.acquisition_score,
                    "components": item.acquisition_components,
                }
                for item in run.proposal_pool
            ]
        ),
        selection_digest=semantic_digest(
            run.proposal_selection.model_dump(mode="json")
            if run.proposal_selection
            else [point.index for point in selected]
        ),
        evaluated_count=diagnostics.evaluated_count,
        model_call_count=diagnostics.model_call_count,
        runtime_ms=diagnostics.runtime_ms,
        memory_peak_bytes=diagnostics.memory_peak_bytes,
        selected_count=count,
        goal_achievement_rate=ratio(
            lambda point: point.goal_evaluation.achieved is True
        ),
        feasible_rate=ratio(
            lambda point: not point.secondary_goal_evaluations
            or all(
                item.achieved is True
                for item in point.secondary_goal_evaluations.values()
            )
        ),
        constraint_unknown_rate=ratio(
            lambda point: bool(point.secondary_goal_evaluations)
            and not any(
                item.achieved is False
                for item in point.secondary_goal_evaluations.values()
            )
            and any(
                item.achieved is None
                for item in point.secondary_goal_evaluations.values()
            )
        ),
        supported_rate=ratio(lambda point: point.support.status == "supported"),
        caution_rate=ratio(lambda point: point.support.status == "caution"),
        extrapolated_rate=ratio(
            lambda point: point.support.status == "extrapolated"
        ),
        duplicate_rate=1 - (len(set(identities)) / count),
        failure_count=diagnostics.rejected_count,
        fallback_count=1 if run.proposal_strategy.fallback_from else 0,
    )


class ProposalLabService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def create(
        self, request: ProposalLabCreateRequest, project_id: str
    ) -> ProposalLabReport:
        with _screening_project_lock(project_id):
            return self._create_unlocked(request, project_id)

    def _create_unlocked(
        self, request: ProposalLabCreateRequest, project_id: str
    ) -> ProposalLabReport:
        raw_runs = []
        for run_id in request.run_ids:
            raw = self.store.get_screening_run(run_id, project_id)
            if raw is None:
                raise ProposalLabNotFoundError(
                    f"Proposal Lab用Runが見つかりません: {run_id}"
                )
            raw_runs.append(ScreeningRunResponse.model_validate(raw))

        identities = [
            _identity(run, request.evaluation_fixture_version) for run in raw_runs
        ]
        comparison_identity = dict(identities[0])
        for key in ("base_inputs", "variables"):
            comparison_identity[key] = semantic_digest(comparison_identity[key])
        for current in identities[1:]:
            comparable = dict(current)
            for key in ("base_inputs", "variables"):
                comparable[key] = semantic_digest(comparable[key])
            if comparable != comparison_identity:
                differing = sorted(
                    key
                    for key in comparison_identity
                    if comparison_identity[key] != comparable.get(key)
                )
                raise ProposalLabValidationError(
                    "同じfixtureでないRunは比較できません: " + ", ".join(differing)
                )

        by_strategy: dict[str, list[ScreeningRunResponse]] = defaultdict(list)
        for run in raw_runs:
            assert run.proposal_strategy is not None
            by_strategy[run.proposal_strategy.id].append(run)
        if len(by_strategy) < 2:
            raise ProposalLabValidationError("2種類以上のstrategyが必要です")
        definitions = {item.strategy_id: item for item in STRATEGIES}
        unknown_strategy_ids = sorted(set(by_strategy) - set(definitions))
        if unknown_strategy_ids:
            raise ProposalLabValidationError(
                "registryにないProposal Strategyです: "
                + ", ".join(unknown_strategy_ids)
            )
        strategy_protocols: dict[str, dict[str, Any]] = {}
        for strategy_id, runs in by_strategy.items():
            first = runs[0].proposal_strategy
            assert first is not None
            expected = {
                "strategy_version": first.version,
                "acquisition_id": first.acquisition_id,
                "acquisition_version": first.acquisition_version,
                "parameter": {
                    "value": first.exploration_parameter,
                    "role": first.parameter_role,
                    "representation": first.acquisition_representation,
                },
            }
            for run in runs[1:]:
                current = run.proposal_strategy
                assert current is not None
                actual = {
                    "strategy_version": current.version,
                    "acquisition_id": current.acquisition_id,
                    "acquisition_version": current.acquisition_version,
                    "parameter": {
                        "value": current.exploration_parameter,
                        "role": current.parameter_role,
                        "representation": current.acquisition_representation,
                    },
                }
                if actual != expected:
                    raise ProposalLabValidationError(
                        f"{strategy_id}のversion／acquisition／parameterをseed間で固定してください"
                    )
            strategy_protocols[strategy_id] = expected
        seed_sets = {
            strategy_id: {run.seed for run in runs}
            for strategy_id, runs in by_strategy.items()
        }
        expected_seeds = next(iter(seed_sets.values()))
        if len(expected_seeds) < 2 or any(
            seeds != expected_seeds for seeds in seed_sets.values()
        ):
            raise ProposalLabValidationError(
                "各strategyに同じ2個以上のseedを揃えてください"
            )
        memo_ids = {memo.strategy_id for memo in request.adoption_memos}
        if not memo_ids.issubset(by_strategy):
            raise ProposalLabValidationError(
                "adoption memoのstrategyは比較Runに含めてください"
            )

        metrics = tuple(sorted(
            (_run_metric(run) for run in raw_runs),
            key=lambda item: (item.strategy_id, item.seed, item.run_id),
        ))
        summaries = []
        for strategy_id, runs in sorted(by_strategy.items()):
            definition = definitions[strategy_id]
            strategy_protocol = strategy_protocols[strategy_id]
            evaluated_strategy = runs[0].proposal_strategy
            assert evaluated_strategy is not None
            strategy_metrics = [
                metric for metric in metrics if metric.strategy_id == strategy_id
            ]
            summaries.append(
                ProposalLabStrategySummary(
                    strategy_id=strategy_id,
                    strategy_version=str(
                        strategy_protocol["strategy_version"]
                    ),
                    acquisition_id=str(strategy_protocol["acquisition_id"]),
                    acquisition_version=str(
                        strategy_protocol["acquisition_version"]
                    ),
                    acquisition_parameter_digest=semantic_digest(
                        strategy_protocol["parameter"]
                    ),
                    lifecycle_status_at_evaluation=(
                        evaluated_strategy.lifecycle_status
                    ),
                    required_capabilities=(
                        evaluated_strategy.required_capabilities
                    ),
                    unavailable_reasons=(),
                    acquisition_scope=(
                        "joint" if definition.requires_joint_samples else "marginal"
                    ),
                    seeds=tuple(sorted(expected_seeds)),
                    mean_goal_achievement_rate=mean(
                        item.goal_achievement_rate for item in strategy_metrics
                    ),
                    mean_feasible_rate=mean(
                        item.feasible_rate for item in strategy_metrics
                    ),
                    mean_constraint_unknown_rate=mean(
                        item.constraint_unknown_rate
                        for item in strategy_metrics
                    ),
                    mean_supported_rate=mean(
                        item.supported_rate for item in strategy_metrics
                    ),
                    extrapolated_rate_range=(
                        max(item.extrapolated_rate for item in strategy_metrics)
                        - min(item.extrapolated_rate for item in strategy_metrics)
                    ),
                    goal_achievement_rate_range=(
                        max(
                            item.goal_achievement_rate for item in strategy_metrics
                        )
                        - min(
                            item.goal_achievement_rate for item in strategy_metrics
                        )
                    ),
                )
            )

        protocol_payload = {
            key: value
            for key, value in comparison_identity.items()
            if key not in {"base_inputs", "variables"}
        }
        protocol = ProposalLabProtocol(
            **protocol_payload,
            digest=semantic_digest(comparison_identity),
            seeds=tuple(sorted(expected_seeds)),
        )
        memos = tuple(
            ProposalLabAdoptionMemo(
                **memo.model_dump(mode="json"),
                evidence_run_ids=tuple(
                    sorted(run.id for run in by_strategy[memo.strategy_id])
                ),
                registry_changed=False,
            )
            for memo in sorted(
                request.adoption_memos,
                key=lambda item: item.strategy_id,
            )
        )
        report_payload = {
            "schema_version": "proposal-lab-report/v1",
            "protocol": protocol.model_dump(mode="json"),
            "runs": [item.model_dump(mode="json") for item in metrics],
            "strategy_summaries": [
                item.model_dump(mode="json") for item in summaries
            ],
            "adoption_memos": [item.model_dump(mode="json") for item in memos],
            "limitations": [
                "ground truth fixtureがないためregretは未評価です",
                "memory peakはScreening Runに記録がある場合だけ比較します",
                "保存した判定はproduction registryを自動変更しません",
                "marginal rankingとgreedy batch selectionをjoint acquisitionとは扱いません",
            ],
        }
        report_payload["report_digest"] = semantic_digest(report_payload)
        stored = self.store.create_proposal_lab_report(
            project_id=project_id,
            payload=report_payload,
        )
        return ProposalLabReport.model_validate(stored)

    def list(self, project_id: str) -> list[ProposalLabReport]:
        if self.store.get_project(project_id) is None:
            raise ProjectNotFoundError(project_id)
        return [
            ProposalLabReport.model_validate(item)
            for item in self.store.list_proposal_lab_reports(project_id)
        ]

    def get(self, report_id: str, project_id: str) -> ProposalLabReport:
        report = self.store.get_proposal_lab_report(report_id, project_id)
        if report is None:
            raise ProposalLabNotFoundError(
                f"Proposal Lab reportが見つかりません: {report_id}"
            )
        return ProposalLabReport.model_validate(report)
