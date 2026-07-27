"""Provider boundary for candidate-decision AI review."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from material_workbench.application.ai_review_tools import AiReviewToolSurface
from material_workbench.contracts.ai_review_contracts import AiActorIdentity


@dataclass(frozen=True)
class AiReviewProviderContext:
    task_instruction: str
    system_policy: str
    typed_context: Mapping[str, str | int]
    untrusted_data_notice: str


class AiReviewProvider(Protocol):
    @property
    def identity(self) -> AiActorIdentity: ...

    @property
    def sampling_settings(self) -> Mapping[str, str | int | float | bool | None]: ...

    def review_candidate(
        self,
        context: AiReviewProviderContext,
        tools: AiReviewToolSurface,
    ) -> Mapping[str, Any]: ...


class ScriptedFixtureAiReviewProvider:
    """Deterministic test/demo provider that never claims to be a real model."""

    def __init__(self, *, workspace_id: str = "local-workspace") -> None:
        self._identity = AiActorIdentity(
            actor_id="ai-review-scripted-fixture",
            agent_definition_id="candidate-decision-review-scripted-fixture/v1",
            model_provider="test",
            model_id="scripted-fixture",
            policy_version="candidate-review-policy/v1",
            toolset_version="candidate-review-tools/v1",
            workspace_id=workspace_id,
            capabilities=("candidate_decision_review",),
        )

    @property
    def identity(self) -> AiActorIdentity:
        return self._identity

    @property
    def sampling_settings(self) -> Mapping[str, str | int | float | bool | None]:
        return {"deterministic": True, "fixture": True}

    def review_candidate(
        self,
        context: AiReviewProviderContext,
        tools: AiReviewToolSurface,
    ) -> Mapping[str, Any]:
        candidate = tools.call("candidate_revision")
        snapshots = tools.call("predictive_snapshots")
        actuals = tools.call("actual_measurements")
        objectives = tools.call("objective_and_design_space")
        tools.call("project_summary")
        tools.call("decision_activity_runs")
        candidate_ref = candidate.evidence_refs[0]
        limitations = [
            "これはtest/scripted-fixtureであり、実モデルの有用性を評価する出力ではありません。",
            "保存済みevidenceだけを確認し、新しい予測や因果判断は生成していません。",
        ]
        if not snapshots.payload:
            limitations.append("current revisionの保存済み予測がありません。")
        if not actuals.payload:
            limitations.append("current candidateの実測がありません。")
        if not objectives.evidence_refs:
            limitations.append("ProjectのObjectiveまたはDesign Spaceが固定されていません。")
        return {
            "schema_version": "candidate-decision-review-output/v1",
            "status": "complete",
            "findings": [
                {
                    "finding_id": "fixture-current-revision",
                    "category": "missing_evidence" if not snapshots.payload else "support",
                    "severity": "caution" if not snapshots.payload else "info",
                    "claim": (
                        "current revisionに保存済み予測がありません。"
                        if not snapshots.payload
                        else "current revisionと保存済み予測を根拠として確認できます。"
                    ),
                    "evidence_refs": [candidate_ref.model_dump(mode="json")],
                    "reasoning_summary": (
                        "許可されたtyped toolから取得したcurrent candidate revisionだけを参照しました。"
                    ),
                    "confidence_kind": "none",
                    "confidence_level": None,
                    "uncertainty_note": (
                        "この所見は保存状況の確認であり、成功確率や因果効果を表しません。"
                    ),
                    "suggested_action": (
                        "run_decision_activity"
                        if not snapshots.payload
                        else "inspect_evidence"
                    ),
                }
            ],
            "summary": "current candidate revisionの保存済みevidenceを限定的に確認しました。",
            "suggested_actions": [
                {
                    "action": (
                        "collect_measurement"
                        if not actuals.payload
                        else "inspect_evidence"
                    ),
                    "rationale": (
                        "実測がないため次の判断材料を追加します。"
                        if not actuals.payload
                        else "保存済み実測と予測の対応を人が確認します。"
                    ),
                }
            ],
            "limitations": limitations,
        }
