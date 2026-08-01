from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from .candidates import CandidateService
from .inference import InferenceService, InferenceValidationError
from .projects import ProjectService
from decision_workbench.contracts.prediction_catalog_contracts import (
    ActualMeasurement,
    ActualMeasurementInput,
)
from decision_workbench.contracts.candidate_project_contracts import (
    Candidate,
    Project,
)
from decision_workbench.contracts.evidence_contracts import (
    DetailedPredictionResponse,
    PredictionVsActualResponse,
    SnapshotResponse,
)
from decision_workbench.persistence.snapshot_reader import SnapshotPayloadError, candidate_input_from_snapshot
from decision_workbench.persistence.store import Store
from decision_workbench.tasks.task_registry import TaskRegistry
from decision_workbench.contracts.task_contracts import OutputDefinition
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.modeling.missingness import (
    assess_input_missingness,
    require_operation_allowed,
)


class RecordNotFoundError(LookupError):
    pass


class RecordValidationError(ValueError):
    pass


class RecordIntegrityError(RuntimeError):
    pass


def normalize_actual_measurement(
    payload: ActualMeasurementInput,
    output: OutputDefinition,
) -> ActualMeasurementInput:
    """Resolve a user observation through the Task-owned output semantics."""
    if output.target_kind in {"continuous", "continuous_positive"}:
        if payload.mean is None:
            raise RecordValidationError(f"{output.label}は数値の実測値を指定してください")
        return payload.model_copy(update={"value_label": None})
    if output.target_kind == "binary":
        semantics = output.binary
        assert semantics is not None
        raw = payload.value if payload.value is not None else payload.mean
        if raw == semantics.event_label or raw is True or raw == 1:
            return payload.model_copy(update={"mean": 1.0, "value": None, "value_label": semantics.event_label})
        if raw == semantics.non_event_label or raw is False or raw == 0:
            return payload.model_copy(update={"mean": 0.0, "value": None, "value_label": semantics.non_event_label})
        raise RecordValidationError(f"{output.label}は{semantics.event_label}または{semantics.non_event_label}を指定してください")
    if output.target_kind == "count":
        raw = payload.value if payload.value is not None else payload.mean
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not float(raw).is_integer() or raw < 0:
            raise RecordValidationError(f"{output.label}は0以上の整数で指定してください")
        return payload.model_copy(update={"mean": float(raw), "value": None, "value_label": None})
    semantics = output.ordinal
    assert semantics is not None
    raw = payload.value
    if isinstance(raw, str) and raw in semantics.categories:
        index = semantics.categories.index(raw)
        return payload.model_copy(update={"mean": float(index), "value": None, "value_label": raw})
    if isinstance(raw, int) and not isinstance(raw, bool) and 0 <= raw < len(semantics.categories):
        return payload.model_copy(update={"mean": float(raw), "value": None, "value_label": semantics.categories[raw]})
    raise RecordValidationError(f"{output.label}は定義済みの順序カテゴリを指定してください")


class RecordService:
    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        inference: InferenceService,
        resolver: ProjectRuntimeResolver,
    ) -> None:
        self.store = store
        self.registry = registry
        self.inference = inference
        self.projects = ProjectService(store, registry)
        self.candidates = CandidateService(store, registry, resolver)

    def list_snapshots(self, project_id: str, candidate_id: str) -> list[SnapshotResponse]:
        project = self.projects.require(project_id)
        self.registry.require_declared_operation(project.task_id, "snapshot")
        self.candidates.get(project_id, candidate_id, include_archived=True)
        return [SnapshotResponse.model_validate(item) for item in self.store.list_snapshots(candidate_id)]

    def predict_and_snapshot(self, project_id: str, candidate_id: str, revision: int) -> DetailedPredictionResponse:
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        project = self.projects.require(project_id)
        result = self.inference.detailed_for(project, candidate)
        return DetailedPredictionResponse(prediction=result, snapshot=self.create_snapshot_for(candidate, result))

    def create_snapshot(self, project_id: str, candidate_id: str) -> SnapshotResponse:
        project = self.projects.require(project_id)
        self.inference.require_operation(project.task_id, "snapshot")
        candidate = self.candidates.get(project_id, candidate_id)
        return self.create_snapshot_for(candidate)

    def create_snapshot_for(self, candidate: Candidate, result: dict[str, Any] | None = None) -> SnapshotResponse:
        project = self.projects.require(candidate.project_id)
        self.inference.require_operation(project.task_id, "snapshot")
        runtime = self.resolver.runtime_for(project)
        if hasattr(runtime, "missing_policy_inputs"):
            evidence = assess_input_missingness(
                candidate,
                runtime.missing_policy_inputs,
                runtime.training_stats,
                operation="snapshot",
            )
            try:
                require_operation_allowed(evidence)
            except ValueError as exc:
                raise RecordValidationError(str(exc)) from exc
        if result is None:
            result = self.inference.detailed_for(project, candidate)
        return SnapshotResponse.model_validate(
            self.store.create_snapshot(
                candidate.id,
                to_jsonable_python(self._snapshot_payload(project, candidate, result)),
            )
        )

    @staticmethod
    def _snapshot_payload(
        project: Project, candidate: Candidate, result: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "snapshot_schema_version": "prediction-snapshot-v2",
            "candidate_id": candidate.id,
            "raw_candidate": candidate.model_dump(mode="json"),
            "canonical_input": result["canonical_input"],
            "prediction": result,
            "provenance": result["model_meta"],
            "project_design_space_digest": project.design_space_digest,
            "project_design_space_binding_provenance": (
                project.design_space_binding_provenance
            ),
        }

    def restore(self, project_id: str, snapshot_id: str) -> Candidate:
        project = self.projects.require(project_id)
        self.inference.require_operation(project.task_id, "snapshot")
        snapshot = self.store.get_snapshot(snapshot_id)
        if snapshot is None or self.store.get_candidate(snapshot["candidate_id"], project_id, include_archived=True) is None:
            raise RecordNotFoundError("スナップショットが見つかりません")
        try:
            payload = candidate_input_from_snapshot(snapshot_id, snapshot["payload"])
        except SnapshotPayloadError as exc:
            raise RecordValidationError(str(exc)) from exc
        return self.candidates.create(project_id, payload)

    def get_snapshot(self, project_id: str, snapshot_id: str) -> SnapshotResponse:
        project = self.projects.require(project_id)
        self.registry.require_declared_operation(project.task_id, "snapshot")
        snapshot = self.store.get_snapshot(snapshot_id)
        if snapshot is None or self.store.get_candidate(snapshot["candidate_id"], project_id, include_archived=True) is None:
            raise RecordNotFoundError("スナップショットが見つかりません")
        return SnapshotResponse.model_validate(snapshot)

    def list_actuals(self, project_id: str, candidate_id: str) -> list[ActualMeasurement]:
        project = self.projects.require(project_id)
        self.registry.require_declared_operation(project.task_id, "actual_measurement")
        self.candidates.get(project_id, candidate_id, include_archived=True)
        return self.store.list_actuals(candidate_id)

    def create_actual(self, project_id: str, candidate_id: str, revision: int, payload: ActualMeasurementInput) -> ActualMeasurement:
        project = self.projects.require(project_id)
        self.inference.require_operation(project.task_id, "actual_measurement")
        candidate = self.candidates.at_revision(project_id, candidate_id, revision)
        outputs = {
            output.key: output
            for output in self.registry.contract_for(project.task_id).task_definition.outputs
        }
        output = outputs.get(payload.property)
        if output is None:
            raise RecordValidationError(
                f"実測の特性 {payload.property} は予測タスク {project.task_id} に定義されていません"
            )
        if payload.unit != output.unit:
            raise RecordValidationError(
                f"{output.label}（{payload.property}）の単位は {output.unit} です"
            )
        payload = normalize_actual_measurement(payload, output)
        result = self.inference.detailed_for(project, candidate)
        if payload.property not in result.get("predictions", {}):
            raise RecordIntegrityError(
                f"固定する予測snapshotに {payload.property} の予測がありません"
            )
        return self.store.create_snapshot_and_actual(
            project_id,
            candidate_id,
            revision,
            to_jsonable_python(self._snapshot_payload(project, candidate, result)),
            payload,
        )

    def delete_actual(self, project_id: str, candidate_id: str, actual_id: str) -> None:
        project = self.projects.require(project_id)
        self.inference.require_operation(project.task_id, "actual_measurement")
        self.candidates.get(project_id, candidate_id, include_archived=True)
        if actual_id not in {item.id for item in self.store.list_actuals(candidate_id)} or not self.store.delete_actual(actual_id):
            raise RecordNotFoundError("実測が見つかりません")

    def prediction_vs_actual(self, project_id: str, candidate_id: str) -> PredictionVsActualResponse:
        actuals = self.list_actuals(project_id, candidate_id)
        comparisons = []
        for actual in actuals:
            snapshot = self.store.get_snapshot(actual.snapshot_id)
            if snapshot is None:
                raise RecordIntegrityError("実測に対応する予測スナップショットが見つかりません")
            if snapshot.get("candidate_id") != candidate_id:
                raise RecordIntegrityError("実測に対応する予測スナップショットの候補が一致しません")
            try:
                validated_snapshot = SnapshotResponse.model_validate(snapshot)
            except ValidationError as exc:
                raise RecordIntegrityError(
                    "実測に対応する予測スナップショットが破損しているため照合できません"
                ) from exc
            payload = snapshot.get("payload")
            if (
                not isinstance(payload, dict)
                or payload.get("snapshot_schema_version") != "prediction-snapshot-v2"
            ):
                raise RecordIntegrityError(
                    "実測に対応する予測スナップショットが旧形式のため照合できません"
                )
            if (
                validated_snapshot.payload.prediction is None
                or validated_snapshot.payload.provenance is None
            ):
                raise RecordIntegrityError(
                    "実測に対応する予測スナップショットが旧形式または不完全なため照合できません"
                )
            try:
                snapshot_candidate = Candidate.model_validate(payload.get("raw_candidate"))
            except ValidationError as exc:
                raise RecordIntegrityError(
                    "実測に対応する予測スナップショットの候補情報が破損しているため照合できません"
                ) from exc
            if payload.get("candidate_id") != candidate_id or snapshot_candidate.id != candidate_id:
                raise RecordIntegrityError("実測に対応する予測スナップショットの候補が一致しません")
            comparisons.append({
                "actual": actual,
                "snapshot_id": validated_snapshot.id,
                "snapshot_created_at": validated_snapshot.created_at,
                "candidate_revision": snapshot_candidate.revision,
                "prediction": validated_snapshot.payload.prediction,
                "provenance": validated_snapshot.payload.provenance,
            })
        return PredictionVsActualResponse(candidate_id=candidate_id, actuals=actuals, comparisons=comparisons)
