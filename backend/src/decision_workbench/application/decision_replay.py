from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import ValidationError

from decision_workbench.application.inference import InferenceService
from decision_workbench.application.projects import ProjectService
from decision_workbench.contracts.candidate_project_contracts import Candidate
from decision_workbench.contracts.decision_replay_contracts import (
    CurrentPackageReevaluation,
    DecisionCase,
    DecisionCaseCreateRequest,
    DecisionCaseDraftContext,
    DecisionCaseDraftSnapshot,
    DecisionRationale,
    DecisionReplayRequest,
    DecisionReplayResult,
    DecisionReplayRun,
    DecisionSelection,
    HistoricalCandidateEvaluation,
    HistoricalCandidateEvidence,
    RealizedOutcome,
    RetrospectiveActualEvidence,
    SimilarDecisionCase,
)
from decision_workbench.contracts.evidence_contracts import SnapshotResponse
from decision_workbench.contracts.objective_contracts import ObjectiveTerm
from decision_workbench.contracts.prediction_catalog_contracts import Prediction
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import TaskRegistry


ACTOR_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,127}$"


class DecisionReplayNotFoundError(LookupError):
    pass


class DecisionReplayValidationError(ValueError):
    pass


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DecisionReplayValidationError("判断時刻にはtimezoneが必要です")
    return value.astimezone(UTC)


def _package_digest(snapshot: SnapshotResponse) -> str:
    provenance = snapshot.payload.provenance
    package = provenance.package if provenance is not None else None
    if package is None or not package.manifest_sha256:
        raise DecisionReplayValidationError(
            "判断時点のSnapshotにModel Package identityがありません"
        )
    return package.manifest_sha256


def _primary_score(term: ObjectiveTerm, value: float) -> float:
    if term.direction in {"maximize", "at_least"}:
        return value
    if term.direction in {"minimize", "at_most"}:
        return -value
    if term.direction == "target":
        assert term.target is not None
        return -abs(value - term.target)
    if term.direction == "between":
        assert term.lower is not None and term.upper is not None
        distance = max(term.lower - value, 0.0, value - term.upper)
        return -distance
    raise DecisionReplayValidationError("主目的の方向をreplayできません")


class DecisionReplayService:
    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        inference: InferenceService,
    ) -> None:
        self.store = store
        self.registry = registry
        self.inference = inference
        self.projects = ProjectService(store, registry)

    def create_case(
        self,
        project_id: str,
        payload: DecisionCaseCreateRequest,
        *,
        human_actor_id: str | None,
    ) -> DecisionCase:
        project = self.projects.require(project_id)
        if project.scientific_identity.identity_kind != "single_task":
            raise DecisionReplayValidationError(
                "P0 Decision Replayはsingle-Task Projectだけに対応します"
            )
        cutoff = _aware(payload.decision_timestamp)
        if cutoff > datetime.now(UTC):
            raise DecisionReplayValidationError("判断時刻を未来にできません")
        outputs = {
            item.key
            for item in self.registry.contract_for(project.task_id).task_definition.outputs
        }
        if set(payload.outcome_policy.target_keys) - outputs:
            raise DecisionReplayValidationError(
                "outcome policyにTask未定義のtargetがあります"
            )

        candidate_by_key: dict[tuple[str, int], Candidate] = {}
        for reference in payload.candidates:
            candidate = self.store.get_candidate_revision(
                reference.candidate_id, reference.candidate_revision, project_id
            )
            if candidate is None:
                raise DecisionReplayValidationError(
                    "固定するCandidate revisionがProjectにありません"
                )
            if _aware(candidate.updated_at) > cutoff:
                raise DecisionReplayValidationError(
                    "判断時刻より後のCandidate revisionをhistorical evidenceへ含められません"
                )
            candidate_by_key[(candidate.id, candidate.revision)] = candidate

        historical: list[HistoricalCandidateEvidence] = []
        seen_candidate_keys: set[tuple[str, int]] = set()
        for snapshot_id in payload.snapshot_ids:
            raw = self.store.get_snapshot(snapshot_id)
            if raw is None:
                raise DecisionReplayValidationError(
                    "固定するPrediction Snapshotが見つかりません"
                )
            try:
                snapshot = SnapshotResponse.model_validate(raw)
                raw_candidate = Candidate.model_validate(raw["payload"]["raw_candidate"])
            except (ValidationError, KeyError, TypeError) as exc:
                raise DecisionReplayValidationError(
                    "Prediction SnapshotにCandidate revision identityがありません"
                ) from exc
            key = (raw_candidate.id, raw_candidate.revision)
            candidate = candidate_by_key.get(key)
            if candidate is None or snapshot.candidate_id != candidate.id:
                raise DecisionReplayValidationError(
                    "Snapshotは固定Candidate setの同じrevisionを参照する必要があります"
                )
            if key in seen_candidate_keys:
                raise DecisionReplayValidationError(
                    "一つのCandidate revisionへ複数Snapshotを固定できません"
                )
            if _aware(snapshot.created_at) > cutoff:
                raise DecisionReplayValidationError(
                    "判断時刻より後のPrediction Snapshotをhistorical evidenceへ含められません"
                )
            prediction = snapshot.payload.prediction
            if prediction is None or set(payload.outcome_policy.target_keys) - set(
                prediction.predictions
            ):
                raise DecisionReplayValidationError(
                    "Snapshotにoutcome policy対象の予測がありません"
                )
            historical.append(
                HistoricalCandidateEvidence(
                    candidate=key_to_reference(key),
                    candidate_name=candidate.name,
                    candidate_updated_at=candidate.updated_at,
                    snapshot_id=snapshot.id,
                    snapshot_created_at=snapshot.created_at,
                    predictions={
                        key: prediction.predictions[key]
                        for key in payload.outcome_policy.target_keys
                    },
                    model_package_manifest_digest=_package_digest(snapshot),
                    warnings=tuple(prediction.warnings),
                )
            )
            seen_candidate_keys.add(key)
        if seen_candidate_keys != set(candidate_by_key):
            raise DecisionReplayValidationError(
                "各Candidate revisionに判断時点のSnapshotが一つ必要です"
            )

        actual_by_id = {
            item.id: item for item in self.store.list_project_actuals(project_id)
        }
        retrospective: list[RetrospectiveActualEvidence] = []
        allowed_candidates = {item.candidate_id for item in payload.candidates}
        for actual_id in payload.actual_measurement_ids:
            actual = actual_by_id.get(actual_id)
            if actual is None or actual.candidate_id not in allowed_candidates:
                raise DecisionReplayValidationError(
                    "Actual Measurementは固定Candidate setの同じProjectに必要です"
                )
            if _aware(actual.created_at) <= cutoff:
                raise DecisionReplayValidationError(
                    "判断時刻以前のActualを後から得たevidenceとして扱えません"
                )
            if actual.property not in payload.outcome_policy.target_keys:
                raise DecisionReplayValidationError(
                    "Actual Measurementがoutcome policy対象外です"
                )
            raw_snapshot = self.store.get_snapshot(actual.snapshot_id)
            try:
                actual_snapshot = SnapshotResponse.model_validate(raw_snapshot)
                actual_candidate = Candidate.model_validate(
                    raw_snapshot["payload"]["raw_candidate"]
                )
                actual_prediction = actual_snapshot.payload.prediction
                actual_key = (actual_candidate.id, actual_candidate.revision)
                if (
                    actual_prediction is None
                    or actual_snapshot.candidate_id != actual.candidate_id
                    or actual_candidate.project_id != project_id
                    or actual_key not in candidate_by_key
                    or actual.property not in actual_prediction.predictions
                ):
                    raise ValueError
            except (ValidationError, KeyError, TypeError, ValueError) as exc:
                raise DecisionReplayValidationError(
                    "Actualは固定Candidate revisionのPrediction Snapshotを参照する必要があります"
                ) from exc
            retrospective.append(
                RetrospectiveActualEvidence(
                    actual=actual,
                    candidate=key_to_reference(actual_key),
                    prediction_snapshot_created_at=actual_snapshot.created_at,
                )
            )

        rationale = None
        if payload.rationale is not None:
            if human_actor_id is None or re.fullmatch(
                ACTOR_ID_PATTERN, human_actor_id
            ) is None:
                raise DecisionReplayValidationError(
                    "判断理由にはtrusted local Actor identityが必要です"
                )
            if (
                payload.selection.status == "selected"
                and payload.rationale.disposition != "selected"
            ) or (
                payload.selection.status == "no_decision"
                and payload.rationale.disposition == "selected"
            ):
                raise DecisionReplayValidationError(
                    "判断結果とrationale dispositionが一致しません"
                )
            rationale = DecisionRationale(
                **payload.rationale.model_dump(), actor_id=human_actor_id
            )

        identity_payload = {
            "project_id": project.id,
            "task_id": project.task_id,
            "task_contract_digest": project.task_contract_digest,
            "objective_definition_digest": project.objective_definition_digest,
            "request": payload.model_dump(mode="json"),
            "actor_id": rationale.actor_id if rationale is not None else None,
        }
        semantic_identity = semantic_digest(identity_payload)
        case_id = f"decision-case-{semantic_identity.removeprefix('sha256:')[:24]}"
        stored_payload: dict[str, object] = {
            "schema_version": "decision-case/v1",
            "task_id": project.task_id,
            "task_contract_digest": project.task_contract_digest,
            "objective_definition": (
                project.objective_definition.model_dump(mode="json")
                if project.objective_definition is not None
                else None
            ),
            "objective_definition_digest": project.objective_definition_digest,
            "decision_timestamp": cutoff.isoformat(),
            "candidates": [item.model_dump(mode="json") for item in payload.candidates],
            "historical_evidence": [item.model_dump(mode="json") for item in historical],
            "selection": payload.selection.model_dump(mode="json"),
            "rationale": rationale.model_dump(mode="json") if rationale else None,
            "retrospective_actuals": [
                item.model_dump(mode="json") for item in retrospective
            ],
            "outcome_policy": payload.outcome_policy.model_dump(mode="json"),
        }
        return self.store.create_decision_case(
            case_id=case_id,
            semantic_identity=semantic_identity,
            project_id=project.id,
            task_id=project.task_id,
            task_contract_digest=project.task_contract_digest,
            objective_definition_digest=project.objective_definition_digest,
            decision_timestamp=cutoff.isoformat(),
            payload=stored_payload,
        )

    def draft_context(self, project_id: str) -> DecisionCaseDraftContext:
        project = self.projects.require(project_id)
        snapshots: list[DecisionCaseDraftSnapshot] = []
        for candidate in self.store.list_candidates(
            project_id, include_archived=True
        ):
            for raw in self.store.list_snapshots(candidate.id):
                try:
                    fixed_candidate = Candidate.model_validate(
                        raw["payload"]["raw_candidate"]
                    )
                    snapshot = SnapshotResponse.model_validate(raw)
                except (ValidationError, KeyError, TypeError):
                    continue
                snapshots.append(
                    DecisionCaseDraftSnapshot(
                        snapshot_id=snapshot.id,
                        candidate=key_to_reference(
                            (fixed_candidate.id, fixed_candidate.revision)
                        ),
                        candidate_name=fixed_candidate.name,
                        created_at=snapshot.created_at,
                    )
                )
        current_selection = (
            key_to_reference_from_project(project, self.store)
            if project.decision_candidate_id
            else None
        )
        return DecisionCaseDraftContext(
            snapshots=tuple(
                sorted(snapshots, key=lambda item: (item.created_at, item.snapshot_id))
            ),
            actuals=tuple(self.store.list_project_actuals(project_id)),
            target_keys=tuple(
                item.key
                for item in self.registry.contract_for(project.task_id).task_definition.outputs
            ),
            current_selection=(
                DecisionSelection(status="selected", candidate=current_selection)
                if current_selection is not None
                else DecisionSelection(status="no_decision")
            ),
        )

    def list_cases(self, project_id: str) -> list[DecisionCase]:
        self.projects.require(project_id)
        return self.store.list_decision_cases(project_id)

    def get_case(self, project_id: str, case_id: str) -> DecisionCase:
        self.projects.require(project_id)
        case = self.store.get_decision_case(project_id, case_id)
        if case is None:
            raise DecisionReplayNotFoundError("Decision Caseが見つかりません")
        return case

    def run(
        self, project_id: str, case_id: str, request: DecisionReplayRequest
    ) -> DecisionReplayRun:
        project = self.projects.require(project_id)
        case = self.get_case(project_id, case_id)
        historical = tuple(
            HistoricalCandidateEvaluation(
                candidate=item.candidate,
                candidate_name=item.candidate_name,
                predictions=item.predictions,
                originally_selected=(
                    case.selection.candidate == item.candidate
                    if case.selection.candidate is not None
                    else False
                ),
            )
            for item in case.historical_evidence
        )
        alternative, reason = self._alternative_selection(case)
        historical_by_candidate = {
            (item.candidate.candidate_id, item.candidate.candidate_revision): item
            for item in case.historical_evidence
        }
        realized: list[RealizedOutcome] = []
        observed_targets: set[str] = set()
        for item in case.retrospective_actuals:
            actual = item.actual
            if actual.mean is None:
                raise DecisionReplayValidationError(
                    "normalized Actualに数値表現がありません"
                )
            fixed_prediction = historical_by_candidate[
                (item.candidate.candidate_id, item.candidate.candidate_revision)
            ].predictions[actual.property]
            observed_targets.add(actual.property)
            realized.append(
                RealizedOutcome(
                    candidate_id=actual.candidate_id,
                    target=actual.property,
                    actual_id=actual.id,
                    observed_value=actual.mean,
                    observed_label=actual.value_label,
                    predicted_value=fixed_prediction.value,
                    absolute_error=abs(actual.mean - fixed_prediction.value),
                    measured_at=actual.measured_at,
                )
            )

        current: list[CurrentPackageReevaluation] = []
        for item in case.historical_evidence:
            candidate = self.store.get_candidate_revision(
                item.candidate.candidate_id,
                item.candidate.candidate_revision,
                project_id,
            )
            if candidate is None:
                raise DecisionReplayValidationError(
                    "Replay対象のCandidate revisionが見つかりません"
                )
            prediction = self.inference.detailed_for(project, candidate)
            predictions = {
                key: Prediction.model_validate(prediction["predictions"][key])
                for key in case.outcome_policy.target_keys
            }
            current.append(
                CurrentPackageReevaluation(
                    candidate=item.candidate,
                    model_package_manifest_digest=project.model_package_manifest_digest,
                    predictions=predictions,
                )
            )

        similar = tuple(
            SimilarDecisionCase(
                case_id=item.id,
                project_id=item.project_id,
                decision_timestamp=item.decision_timestamp,
                selection_status=item.selection.status,
                snapshot_ids=tuple(
                    evidence.snapshot_id for evidence in item.historical_evidence
                ),
                actual_references=item.retrospective_actuals,
            )
            for item in self.store.list_compatible_decision_cases(
                task_id=case.task_id,
                task_contract_digest=case.task_contract_digest,
                objective_definition_digest=case.objective_definition_digest,
            )
            if item.id != case.id
            and item.outcome_policy.target_keys == case.outcome_policy.target_keys
        )
        result = DecisionReplayResult(
            historical=historical,
            realized_outcomes=tuple(realized),
            unobserved_targets=tuple(
                key
                for key in case.outcome_policy.target_keys
                if key not in observed_targets
            ),
            alternative_policy=request.alternative_policy,
            alternative_selection=alternative,
            alternative_selection_reason=reason,
            current_package_reevaluation=tuple(current),
            similar_cases=similar,
            warnings=(
                ("実測が一部または未到着です。",)
                if len(observed_targets) < len(case.outcome_policy.target_keys)
                else ()
            ),
        )
        semantic_identity = semantic_digest(
            {
                "case_identity": case.semantic_identity,
                "request": request.model_dump(mode="json"),
                "current_package_manifest_digest": project.model_package_manifest_digest,
                "actual_ids": sorted(item.actual.id for item in case.retrospective_actuals),
            }
        )
        run_id = f"decision-replay-{semantic_identity.removeprefix('sha256:')[:24]}"
        return self.store.create_decision_replay_run(
            run_id=run_id,
            semantic_identity=semantic_identity,
            project_id=project_id,
            case_id=case_id,
            payload={
                "schema_version": "decision-replay-run/v1",
                "request": request.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            },
        )

    def list_runs(
        self, project_id: str, case_id: str | None = None
    ) -> list[DecisionReplayRun]:
        self.projects.require(project_id)
        return self.store.list_decision_replay_runs(project_id, case_id)

    @staticmethod
    def _alternative_selection(case: DecisionCase):
        objective = case.objective_definition
        if objective is None:
            return None, "判断時点にObjective Definitionがないため比較不能です。"
        primary = [item for item in objective.terms if item.role == "primary_objective"]
        if len(primary) != 1:
            return None, "固定policyは単一のprimary objectiveだけに対応します。"
        term = primary[0]
        scored = []
        for item in case.historical_evidence:
            prediction = item.predictions.get(term.output_key)
            if prediction is None:
                return None, "当時のCandidate全件にprimary objective予測がありません。"
            scored.append((_primary_score(term, prediction.value), item.candidate))
        scored.sort(
            key=lambda item: (
                -item[0], item[1].candidate_id, item[1].candidate_revision
            )
        )
        return scored[0][1], "当時のSnapshotだけでprimary objective点推定を比較しました。"


def key_to_reference(key: tuple[str, int]):
    from decision_workbench.contracts.decision_replay_contracts import (
        DecisionCandidateReference,
    )

    return DecisionCandidateReference(candidate_id=key[0], candidate_revision=key[1])


def key_to_reference_from_project(project, store: Store):
    candidate = store.get_candidate(
        project.decision_candidate_id, project.id, include_archived=True
    )
    if candidate is None:
        return None
    snapshot = store.get_snapshot(project.decision_snapshot_id)
    try:
        fixed = Candidate.model_validate(snapshot["payload"]["raw_candidate"])
    except (ValidationError, KeyError, TypeError):
        return None
    return key_to_reference((fixed.id, fixed.revision))
