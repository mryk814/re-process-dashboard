"""Resolve only Prediction Graph v1 plans for the dependency-aware runtime."""
from __future__ import annotations

from typing import Any, Mapping

from decision_workbench.application.chain.plan import ChainExecutionError
from decision_workbench.application.chain_candidate_adapters import (
    ChainCandidateAdapter,
    ChainCandidateAdapterError,
    candidate_adapter_for,
)
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.chain_contracts import (
    ChainProjectIdentity,
    PredictionGraphDefinition,
    PredictionGraphRevision,
)
from decision_workbench.modeling.transform_catalog import (
    DeterministicTransformCatalog,
)
from decision_workbench.persistence.store import Store


class PredictionGraphPlanningUseCase:
    """Fail-closed resolver kept separate from the legacy Chain v1 planner."""

    def __init__(
        self,
        store: Store,
        transform_catalog: DeterministicTransformCatalog | None,
    ) -> None:
        self.store = store
        self.transform_catalog = transform_catalog

    def adapter_for(
        self,
        revision: PredictionGraphRevision,
    ) -> ChainCandidateAdapter:
        if self.transform_catalog is None:
            raise ChainExecutionError(
                "決定論的Transformを利用できないためPrediction Graphを実行できません"
            )
        try:
            return candidate_adapter_for(revision, self.transform_catalog)
        except ChainCandidateAdapterError as exc:
            raise ChainExecutionError(str(exc)) from exc

    def resolve(
        self,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
        *,
        project_bindings: Mapping[str, Any] | None = None,
    ) -> tuple[
        Candidate,
        PredictionGraphDefinition,
        PredictionGraphRevision,
        ChainProjectIdentity,
        ChainCandidateAdapter,
        dict[str, Any],
    ]:
        project = self.store.get_project(project_id)
        if project is None:
            raise ChainExecutionError("Prediction Graph Projectが見つかりません")
        identity = project.scientific_identity
        if identity.identity_kind != "chain":
            raise ChainExecutionError(
                "Prediction Graph runtimeには固定Graph Revisionが必要です"
            )
        revision = self.store.get_chain_revision(identity.chain_revision_id)
        if not isinstance(revision, PredictionGraphRevision):
            raise ChainExecutionError(
                "legacy Chain RevisionはPrediction Graph runtimeへ渡せません"
            )
        if revision.revision_digest != identity.chain_revision_digest:
            raise ChainExecutionError("固定されたGraph Revision digestが一致しません")
        definition = self.store.get_chain_definition(
            revision.graph_id,
            revision.graph_definition_digest,
        )
        if not isinstance(definition, PredictionGraphDefinition):
            raise ChainExecutionError(
                "Prediction Graph RevisionのDefinitionを解決できません"
            )
        candidate = self.store.get_candidate_revision(
            candidate_id,
            candidate_revision,
            project_id,
        )
        if candidate is None:
            raise ChainExecutionError(
                "指定したPrediction Graph candidate revisionが見つかりません"
            )
        adapter = self.adapter_for(revision)
        adapter_values = adapter.external_values(candidate)
        bindings = dict(project_bindings or {})
        external: dict[str, Any] = {}
        for graph_input in definition.inputs:
            source = graph_input.value_source
            if source.source_kind == "fixed_value":
                external[graph_input.input_id] = source.value
                continue
            if source.source_kind == "project_binding":
                if source.binding_key not in bindings:
                    raise ChainExecutionError(
                        "Prediction Graph Project bindingが不足しています: "
                        f"{source.binding_key}"
                    )
                external[graph_input.input_id] = bindings[source.binding_key]
                continue
            adapter_path = (
                "candidate.blend"
                if source.candidate_path == "blend"
                else f"candidate.{source.candidate_path}"
            )
            if adapter_path not in adapter_values:
                raise ChainExecutionError(
                    "Prediction Graph candidate inputが不足しています: "
                    f"{source.candidate_path}"
                )
            external[graph_input.input_id] = adapter_values[adapter_path]
        return (
            candidate,
            definition,
            revision,
            identity,
            adapter,
            external,
        )
