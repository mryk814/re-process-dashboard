"""Decision activity service.

The service is activity-agnostic: it resolves a handler from the registry and
never branches on a specific activity or task. Resource preconditions are
declared per resource kind, not per activity.
"""
from __future__ import annotations

from collections.abc import Callable

from material_workbench.application.candidates import CandidateService
from material_workbench.application.decision_activity_registry import (
    ActivityContext,
    DecisionActivityHandler,
    DecisionActivityNotFoundError,
    DecisionActivityValidationError,
    build_registry,
)
from material_workbench.application.projects import ProjectService
from material_workbench.contracts.decision_activity_contracts import (
    CounterfactualSummary,
    DecisionActivityAvailability,
    DecisionActivityProvenance,
    DecisionActivityRun,
    DecisionActivityRunRequest,
)
from material_workbench.contracts.schemas import Candidate, CandidateInput, Project
from material_workbench.contracts.task_contracts import (
    DecisionActivityReference,
    DecisionActivitySourceRef,
)
from material_workbench.execution.inference_work_graph import (
    InferenceKey,
    InferenceWorkGraph,
    semantic_digest,
)
from material_workbench.domain.design_space_validation import (
    validate_candidate_in_design_space,
)
from material_workbench.persistence.store import Store
from material_workbench.application.project_runtime import ProjectRuntimeResolver
from material_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError


class DecisionActivityService:
    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        graph: InferenceWorkGraph,
        resolver: ProjectRuntimeResolver,
        activities: dict[str, DecisionActivityHandler] | None = None,
    ) -> None:
        self.store = store
        self.registry = registry
        self.graph = graph
        self.resolver = resolver
        self.projects = ProjectService(store, registry)
        self.candidates = CandidateService(store, registry, resolver)
        self.activities = build_registry() if activities is None else dict(activities)

    def _handler(self, activity_id: str) -> DecisionActivityHandler:
        handler = self.activities.get(activity_id)
        if handler is None:
            raise DecisionActivityNotFoundError("検討アクティビティが見つかりません")
        return handler

    def _resource_checks(
        self,
        project_id: str,
        candidate_id: str | None,
        expected_revision: int | None,
    ) -> dict[str, Callable[[], str | None]]:
        """One precondition per resource kind, shared by every activity."""

        def saved_candidate() -> str | None:
            if candidate_id is None or expected_revision is None:
                return "保存済みの候補revisionが必要です"
            try:
                candidate = self.candidates.at_revision(
                    project_id, candidate_id, expected_revision
                )
            except (LookupError, ValueError):
                return "保存済みの候補revisionが必要です"
            if candidate.blend_validation.status == "invalid":
                return "保存済みの候補revisionが必要です"
            return None

        def comparison_candidate() -> str | None:
            others = [
                item
                for item in self.store.list_candidates(project_id)
                if item.id != candidate_id
            ]
            if others or (expected_revision is not None and expected_revision > 1):
                return None
            return "比較できる別の候補、または同じ候補の過去revisionが必要です"

        def objective_definition() -> str | None:
            project = self.projects.require(project_id)
            if (
                project.objective_definition is None
                or project.objective_definition_digest is None
            ):
                return "Projectの目標値を先に設定してください"
            return None

        def project_design_space() -> str | None:
            project = self.projects.require(project_id)
            if project.design_space is None or project.design_space_digest is None:
                return "Project-level Design Spaceが固定されていません"
            return None

        return {
            "candidate": saved_candidate,
            "comparison_candidate": comparison_candidate,
            "objective_definition": objective_definition,
            "project_design_space": project_design_space,
        }

    def availability(
        self,
        project_id: str,
        candidate_id: str | None = None,
        expected_revision: int | None = None,
    ) -> list[DecisionActivityAvailability]:
        project = self.projects.require(project_id)
        contract = self.registry.contract_for(project.task_id)
        checks = self._resource_checks(project_id, candidate_id, expected_revision)
        cached: dict[str, str | None] = {}
        results = []
        # 登録順がUIの表示順の正本。activity_idの辞書順で並べ替えない。
        for handler in self.activities.values():
            definition = handler.definition
            reasons: list[str] = []
            for operation in definition.required_operations:
                if not getattr(contract.runtime_capability.operations, operation):
                    reasons.append(f"{operation}に対応する予測runtimeがありません")
            for resource in definition.required_resources:
                if resource not in cached:
                    cached[resource] = checks[resource]()
                reason = cached[resource]
                if reason is not None and reason not in reasons:
                    reasons.append(reason)
            results.append(DecisionActivityAvailability(
                definition=definition,
                available=not reasons,
                reasons=tuple(reasons),
            ))
        return results

    def _context(
        self,
        project: Project,
        candidate: Candidate,
        parameters: object,
    ) -> tuple[ActivityContext, object]:
        resolved = self.resolver.resolve(project)
        runtime = resolved.runtime
        if runtime.model_package is None:
            raise DecisionActivityValidationError("Model Packageが解決されていません")
        definition = self.registry.contract_for(project.task_id).task_definition

        def validate_candidate(item: Candidate) -> None:
            try:
                self.registry.validate_candidate(project.task_id, item)
                validate_candidate_in_design_space(item, project.design_space)
            except (TaskRegistryError, ValueError) as exc:
                raise ValueError(str(exc)) from exc

        def resolve_candidate(candidate_id: str, revision: int) -> Candidate:
            # Any stored revision is immutable, so a comparison may reference an
            # older revision of the same candidate as well as another candidate.
            return self.candidates.historical_revision(project.id, candidate_id, revision)

        context = ActivityContext(
            project=project,
            candidate=candidate,
            task_definition=definition,
            candidate_family=self.registry.candidate_family_for(project.task_id),
            runtime=runtime,  # type: ignore[arg-type]
            parameters=parameters,
            validate_candidate=validate_candidate,
            resolve_candidate=resolve_candidate,
        )
        return context, resolved.identity

    def run(
        self,
        project_id: str,
        candidate_id: str,
        activity_id: str,
        payload: DecisionActivityRunRequest,
    ) -> DecisionActivityRun:
        handler = self._handler(activity_id)
        definition = handler.definition
        if payload.parameters.schema_version != handler.parameters_kind:
            raise DecisionActivityValidationError(
                f"{definition.label}には{handler.parameters_kind}のパラメーターが必要です"
            )
        project = self.projects.require(project_id)
        candidate = self.candidates.at_revision(
            project_id, candidate_id, payload.expected_revision
        )
        if candidate.blend_validation.status == "invalid":
            reasons = " / ".join(issue.message for issue in candidate.blend_validation.issues)
            raise DecisionActivityValidationError(
                f"配合がDesign Spaceを満たしていないため実行できません: {reasons}"
            )
        availability = next(
            item
            for item in self.availability(project_id, candidate_id, payload.expected_revision)
            if item.definition.activity_id == definition.activity_id
        )
        if not availability.available:
            raise DecisionActivityValidationError(" / ".join(availability.reasons))

        context, identity = self._context(project, candidate, payload.parameters)
        prepared = handler.prepare(context)

        canonical = self.registry.validate_candidate(project.task_id, candidate).model_dump(
            mode="json", exclude={"provenance"}
        )
        if candidate.blend is not None:
            canonical["blend"] = candidate.blend.model_input_payload()
        parameter_payload = payload.parameters.model_dump(mode="json")
        provenance_identity = {
            "project_id": project.id,
            "task_id": project.task_id,
            "task_contract_digest": project.task_contract_digest,
            "candidate_id": candidate.id,
            "candidate_revision": candidate.revision,
            "canonical_input_digest": semantic_digest(canonical),
            "model_package_digest": identity.package_manifest_digest,
            "feature_pipeline_digest": identity.pipeline_digest,
            "activity_id": definition.activity_id,
            "activity_version": definition.version,
            "project_design_space_digest": project.design_space_digest,
            "objective_definition_digest": project.objective_definition_digest,
            "parameters": parameter_payload,
        }
        semantic_identity = semantic_digest(provenance_identity)
        existing = self.store.get_decision_activity_run_by_identity(semantic_identity)
        if existing is not None:
            return DecisionActivityRun.model_validate(existing)
        key = InferenceKey.build(
            task_id=identity.task_id,
            runtime_type=identity.runtime_type,
            canonical_input=canonical,
            package_digest=identity.package_digest,
            pipeline_digest=identity.pipeline_digest,
            support_digest=identity.support_digest,
            operation=definition.activity_id,
            operation_parameters=parameter_payload,
        )
        computed = self.graph.execute(key, lambda: handler.compute(context, prepared))
        if computed.result.schema_version != definition.result_kind:
            raise DecisionActivityValidationError(
                f"{definition.label}の結果契約が宣言と一致しません"
            )
        provenance = DecisionActivityProvenance(
            task_id=project.task_id,
            task_contract_digest=project.task_contract_digest,
            candidate_id=candidate.id,
            candidate_revision=candidate.revision,
            canonical_input_digest=semantic_digest(canonical),
            model_package_digest=identity.package_manifest_digest,
            feature_pipeline_digest=identity.pipeline_digest,
            activity_id=definition.activity_id,
            activity_version=definition.version,
            parameters_digest=semantic_digest(parameter_payload),
            project_design_space_digest=project.design_space_digest,
            objective_definition_digest=project.objective_definition_digest,
            project_design_space_binding_provenance=(
                project.design_space_binding_provenance
            ),
            model=computed.model,
        )
        stored = self.store.create_decision_activity_run(
            semantic_identity=semantic_identity,
            project_id=project.id,
            candidate_id=candidate.id,
            activity_id=definition.activity_id,
            activity_version=definition.version,
            payload={
                "definition": definition.model_dump(mode="json"),
                "parameters": parameter_payload,
                "provenance": provenance.model_dump(mode="json"),
                "result": computed.result.model_dump(mode="json"),
            },
        )
        return DecisionActivityRun.model_validate(stored)

    def list_runs(
        self, project_id: str, candidate_id: str | None = None
    ) -> list[DecisionActivityRun]:
        self.projects.require(project_id)
        return [
            DecisionActivityRun.model_validate(item)
            for item in self.store.list_decision_activity_runs(project_id, candidate_id)
        ]

    def get_run(self, project_id: str, run_id: str) -> DecisionActivityRun:
        self.projects.require(project_id)
        run = self.store.get_decision_activity_run(run_id, project_id)
        if run is None:
            raise DecisionActivityNotFoundError("保存済みの検討アクティビティが見つかりません")
        return DecisionActivityRun.model_validate(run)

    def promote_proposal(
        self,
        project_id: str,
        run_id: str,
        proposal_id: str,
    ) -> Candidate:
        """Explicitly turn one immutable Activity proposal into a normal Candidate."""

        run = self.get_run(project_id, run_id)
        if not isinstance(run.result, CounterfactualSummary):
            raise DecisionActivityValidationError(
                "この検討結果には候補化できる変更案がありません"
            )
        proposal = next(
            (
                item
                for item in run.result.proposals
                if item.proposal_id == proposal_id
            ),
            None,
        )
        if proposal is None:
            raise DecisionActivityNotFoundError("選択した変更案が見つかりません")
        for existing in self.candidates.list(project_id, include_archived=True):
            provenance = existing.provenance
            if (
                provenance.source_kind == "decision_activity"
                and provenance.source_ref.run_id == run.id
                and provenance.source_ref.proposal_id == proposal.proposal_id
                and existing.archived_at is None
            ):
                return existing
        return self.candidates.create(
            project_id,
            CandidateInput(
                name=f"目標到達案 {proposal.rank}",
                inputs=proposal.inputs,
                provenance=DecisionActivitySourceRef(
                    source_kind="decision_activity",
                    source_ref=DecisionActivityReference(
                        run_id=run.id,
                        proposal_id=proposal.proposal_id,
                        base_candidate_id=run.result.base_candidate_id,
                        base_candidate_revision=run.result.base_candidate_revision,
                    ),
                ),
            ),
        )


__all__ = [
    "DecisionActivityNotFoundError",
    "DecisionActivityService",
    "DecisionActivityValidationError",
]
