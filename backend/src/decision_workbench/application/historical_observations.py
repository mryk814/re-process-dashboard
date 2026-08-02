from __future__ import annotations

from typing import Any

from decision_workbench.application.candidates import CandidateService, CandidateValidationError
from decision_workbench.application.projects import ProjectService
from decision_workbench.contracts.candidate_project_contracts import Candidate, CandidateInput
from decision_workbench.contracts.historical_observation_contracts import (
    HistoricalObservationCandidateResponse,
    HistoricalObservationEvidence,
    HistoricalObservationListResponse,
    HistoricalObservationRecord,
)
from decision_workbench.contracts.task_contracts import (
    HistoricalObservationReference,
    HistoricalObservationSourceRef,
)
from decision_workbench.domain.design_space_validation import validate_candidate_in_design_space
from decision_workbench.modeling.tabular.data import TabularData
from decision_workbench.modeling.tabular.features import candidate_from_observation
from decision_workbench.persistence.store import Store
from decision_workbench.application.project_runtime import ProjectRuntimeResolver
from decision_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError


class HistoricalObservationUnavailableError(ValueError):
    pass


class HistoricalObservationNotFoundError(LookupError):
    pass


class HistoricalObservationService:
    """Promote explicit flat Dataset records without borrowing lineage semantics."""

    def __init__(
        self,
        store: Store,
        registry: TaskRegistry,
        resolver: ProjectRuntimeResolver,
    ) -> None:
        self.projects = ProjectService(store, registry)
        self.candidates = CandidateService(store, registry, resolver)
        self.registry = registry
        self.resolver = resolver

    def _data(self, project_id: str) -> tuple[Any, TabularData]:
        project = self.projects.require(project_id)
        if not project.dataset_view_revision_id:
            raise HistoricalObservationUnavailableError("Dataset Revisionが固定されていません")
        data = self.resolver.context_runtime_for(project).data
        if not isinstance(data, TabularData):
            raise HistoricalObservationUnavailableError(
                "この予測タスクにはrelationなしの実測record候補化を提供していません"
            )
        return project, data

    def _candidate_input(self, project: Any, data: TabularData, row: dict[str, Any]) -> CandidateInput:
        if not row.get("eligible"):
            reasons = " / ".join(row.get("eligibility_reasons") or ())
            raise CandidateValidationError(
                f"この実測recordは候補化できません{f': {reasons}' if reasons else ''}"
            )
        payload = candidate_from_observation(row, data.profile)
        try:
            self.registry.validate_candidate(project.task_id, payload)
            validate_candidate_in_design_space(payload, project.design_space)
        except (TaskRegistryError, ValueError) as exc:
            raise CandidateValidationError(str(exc)) from exc
        return payload

    def _record(self, project: Any, data: TabularData, row: dict[str, Any]) -> HistoricalObservationRecord:
        try:
            payload = self._candidate_input(project, data, row)
        except CandidateValidationError as exc:
            return HistoricalObservationRecord(
                observation_id=str(row["id"]),
                parent_key=str(row["parent_key"]),
                source_label=str(row["source"]),
                inputs={
                    "composition": dict(row["composition"]),
                    "process": dict(row["features"]),
                    "categorical": dict(row["categorical"]),
                },
                actual_outputs={key: float(value) for key, value in row["outputs"].items()},
                candidate_eligible=False,
                candidate_reason=str(exc),
            )
        return HistoricalObservationRecord(
            observation_id=str(row["id"]),
            parent_key=str(row["parent_key"]),
            source_label=str(row["source"]),
            inputs=payload.inputs,
            actual_outputs={key: float(value) for key, value in row["outputs"].items()},
            candidate_eligible=True,
        )

    def list(self, project_id: str) -> HistoricalObservationListResponse:
        project, data = self._data(project_id)
        records = [self._record(project, data, row) for row in data.observations if row["outputs"]]
        return HistoricalObservationListResponse(
            dataset_view_revision_id=project.dataset_view_revision_id,
            source_sha256=data.source_sha256,
            available=any(record.candidate_eligible for record in records),
            reason=None if any(record.candidate_eligible for record in records) else "候補化できる実測recordがありません",
            records=records,
        )

    def _source_row(self, project_id: str, observation_id: str) -> tuple[Any, TabularData, dict[str, Any]]:
        project, data = self._data(project_id)
        row = next((item for item in data.observations if item["id"] == observation_id), None)
        if row is None:
            raise HistoricalObservationNotFoundError("実測recordが見つかりません")
        return project, data, row

    @staticmethod
    def _reference(project: Any, data: TabularData, row: dict[str, Any]) -> HistoricalObservationReference:
        assert project.dataset_view_revision_id is not None
        return HistoricalObservationReference(
            dataset_view_revision_id=project.dataset_view_revision_id,
            source_sha256=data.source_sha256,
            observation_id=str(row["id"]),
            parent_key=str(row["parent_key"]),
            source_label=str(row["source"]),
            actual_outputs={key: float(value) for key, value in row["outputs"].items()},
        )

    def create_candidate(self, project_id: str, observation_id: str) -> HistoricalObservationCandidateResponse:
        project, data, row = self._source_row(project_id, observation_id)
        payload = self._candidate_input(project, data, row)
        reference = self._reference(project, data, row)
        candidate = self.candidates.create(
            project_id,
            payload.model_copy(
                update={
                    "name": f"実測 {row['parent_key']}",
                    "provenance": HistoricalObservationSourceRef(
                        source_kind="historical_observation", source_ref=reference
                    ),
                }
            ),
        )
        return HistoricalObservationCandidateResponse(
            candidate=candidate,
            evidence=HistoricalObservationEvidence(
                candidate_id=candidate.id,
                reference=reference,
                inputs=candidate.inputs,
                actual_outputs=reference.actual_outputs,
            ),
        )

    def evidence(self, project_id: str, candidate_id: str) -> HistoricalObservationEvidence:
        candidate = self.candidates.get(project_id, candidate_id, include_archived=True)
        if candidate.provenance.source_kind != "historical_observation":
            raise HistoricalObservationUnavailableError("この候補は過去の実測recordから作成されていません")
        reference = candidate.provenance.source_ref
        project, data, row = self._source_row(project_id, reference.observation_id)
        current = self._reference(project, data, row)
        if current != reference:
            raise HistoricalObservationUnavailableError("保存済み実測recordのsource identityを再解決できません")
        source_inputs = self._candidate_input(project, data, row).inputs
        if candidate.inputs != source_inputs:
            raise HistoricalObservationUnavailableError(
                "保存済み候補の入力条件が過去の実測recordと一致しません"
            )
        return HistoricalObservationEvidence(
            candidate_id=candidate.id,
            reference=reference,
            inputs=source_inputs,
            actual_outputs=reference.actual_outputs,
        )
