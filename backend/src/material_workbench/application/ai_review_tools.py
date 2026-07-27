"""Read-only, allow-listed evidence surface for AI candidate review.

The provider receives this facade, never Store, a database connection, or a path.
All observations are captured before provider execution and every reference is
issued by this module from the observed value.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from material_workbench.contracts.ai_review_contracts import (
    AiReviewEvidenceReference,
)
from material_workbench.persistence.store import ProjectNotFoundError, Store


AI_REVIEW_READ_TOOLS = (
    "project_summary",
    "candidate_revision",
    "predictive_snapshots",
    "decision_activity_runs",
    "actual_measurements",
    "objective_and_design_space",
)
AI_REVIEW_WRITE_TOOLS = ("create_ai_review_run",)


class AiReviewToolError(RuntimeError):
    pass


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _digest(value: Any) -> str:
    encoded = json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|token|password|secret)"
    r"(\s*[:=]\s*)([^\s,;]+)"
)
_SECRET_TOKEN = re.compile(r"\b(?:sk|ghp|github_pat)-[A-Za-z0-9_-]{12,}\b")
_NUMERIC_LITERAL = re.compile(
    r"(?<![A-Za-z0-9_.])-?(?:\d+(?:\.\d+)?|\.\d+)"
    r"(?:[eE][+-]?\d+)?(?![A-Za-z0-9_.])"
)


def _sanitize_untrusted(value: Any) -> Any:
    if isinstance(value, str):
        redacted = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", value)
        return _SECRET_TOKEN.sub("[REDACTED]", redacted)
    if isinstance(value, dict):
        return {str(key): _sanitize_untrusted(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_untrusted(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_untrusted(item) for item in value)
    return value


def _value_with_digest(value: Any, expected_digest: str) -> Any | None:
    if _digest(value) == expected_digest:
        return value
    if isinstance(value, dict):
        for child in value.values():
            found = _value_with_digest(child, expected_digest)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _value_with_digest(child, expected_digest)
            if found is not None:
                return found
    return None


def _numeric_values(value: Any) -> set[Decimal]:
    values: set[Decimal] = set()
    if isinstance(value, bool) or value is None:
        return values
    if isinstance(value, (int, float)):
        try:
            values.add(Decimal(str(value)).normalize())
        except InvalidOperation:
            pass
    elif isinstance(value, dict):
        for child in value.values():
            values.update(_numeric_values(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            values.update(_numeric_values(child))
    return values


@dataclass(frozen=True)
class AiReviewToolObservation:
    tool_name: str
    trust_level: str
    payload: Any
    evidence_refs: tuple[AiReviewEvidenceReference, ...]


class AiReviewToolSurface:
    """A closed tool surface with no dynamic query or mutation primitive."""

    __slots__ = (
        "__observations",
        "__evidence",
        "__evidence_values",
        "project_id",
        "candidate_id",
        "candidate_revision",
    )

    def __init__(
        self,
        store: Store,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
    ) -> None:
        self.project_id = project_id
        self.candidate_id = candidate_id
        self.candidate_revision = candidate_revision
        observations = self._capture(
            store,
            project_id=project_id,
            candidate_id=candidate_id,
            candidate_revision=candidate_revision,
        )
        self.__observations = observations
        self.__evidence = {
            self.reference_key(ref): ref
            for observation in observations.values()
            for ref in observation.evidence_refs
        }
        self.__evidence_values = {}
        for observation in observations.values():
            for ref in observation.evidence_refs:
                observed = _value_with_digest(
                    observation.payload, ref.observed_value_digest
                )
                if observed is not None:
                    self.__evidence_values[self.reference_key(ref)] = observed

    @staticmethod
    def reference_key(ref: AiReviewEvidenceReference) -> tuple[object, ...]:
        return (
            ref.resource_kind,
            ref.resource_id,
            ref.revision,
            ref.run_id,
            ref.field_path,
            ref.observed_value_digest,
        )

    @staticmethod
    def _reference(
        *,
        resource_kind: str,
        resource_id: str,
        value: Any,
        revision: int | None = None,
        run_id: str | None = None,
        field_path: str = "$",
    ) -> AiReviewEvidenceReference:
        return AiReviewEvidenceReference(
            resource_kind=resource_kind,
            resource_id=resource_id,
            revision=revision,
            run_id=run_id,
            field_path=field_path,
            observed_value_digest=_digest(value),
        )

    @classmethod
    def _capture(
        cls,
        store: Store,
        *,
        project_id: str,
        candidate_id: str,
        candidate_revision: int,
    ) -> dict[str, AiReviewToolObservation]:
        project = store.get_project(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)
        current = store.get_candidate(candidate_id, project_id)
        if current is None:
            raise AiReviewToolError("候補が見つかりません")
        if current.revision != candidate_revision:
            raise AiReviewToolError("候補revisionがcurrent revisionと一致しません")
        candidate = store.get_candidate_revision(
            candidate_id, candidate_revision, project_id
        )
        if candidate is None:
            raise AiReviewToolError("候補revisionが見つかりません")

        project_payload = _sanitize_untrusted(project.model_dump(mode="json"))
        candidate_payload = _sanitize_untrusted(candidate.model_dump(mode="json"))
        snapshots = []
        snapshot_refs = []
        for snapshot in store.list_snapshots(candidate_id):
            raw_candidate = snapshot.get("payload", {}).get("raw_candidate")
            if (
                not isinstance(raw_candidate, dict)
                or raw_candidate.get("revision") != candidate_revision
            ):
                continue
            snapshot = _sanitize_untrusted(snapshot)
            snapshots.append(snapshot)
            snapshot_refs.append(
                cls._reference(
                    resource_kind="predictive_snapshot",
                    resource_id=str(snapshot["id"]),
                    revision=candidate_revision,
                    value=snapshot,
                )
            )
        activities = []
        activity_refs = []
        for run in store.list_decision_activity_runs(project_id, candidate_id):
            provenance = run.get("provenance")
            if (
                not isinstance(provenance, dict)
                or provenance.get("candidate_revision") != candidate_revision
            ):
                continue
            run = _sanitize_untrusted(run)
            activities.append(run)
            activity_refs.append(
                cls._reference(
                    resource_kind="decision_activity_run",
                    resource_id=str(run["id"]),
                    revision=candidate_revision,
                    run_id=str(run["id"]),
                    value=run,
                )
            )
        actuals = []
        for item in store.list_actuals(candidate_id):
            snapshot = store.get_snapshot(item.snapshot_id)
            raw_candidate = (
                snapshot.get("payload", {}).get("raw_candidate")
                if snapshot is not None
                else None
            )
            if (
                not isinstance(raw_candidate, dict)
                or raw_candidate.get("revision") != candidate_revision
            ):
                continue
            actuals.append(_sanitize_untrusted(item.model_dump(mode="json")))
        actual_refs = [
            cls._reference(
                resource_kind="actual_measurement",
                resource_id=str(item["id"]),
                revision=candidate_revision,
                value=item,
            )
            for item in actuals
        ]
        objective_payload = _sanitize_untrusted({
            "objective_definition": (
                project.objective_definition.model_dump(mode="json")
                if project.objective_definition is not None
                else None
            ),
            "objective_definition_digest": project.objective_definition_digest,
            "design_space": (
                project.design_space.model_dump(mode="json")
                if project.design_space is not None
                else None
            ),
            "design_space_digest": project.design_space_digest,
        })
        objective_refs = []
        if objective_payload["objective_definition"] is not None:
            objective_refs.append(
                cls._reference(
                    resource_kind="objective_definition",
                    resource_id=project.objective_definition_digest or project_id,
                    value=objective_payload["objective_definition"],
                )
            )
        if objective_payload["design_space"] is not None:
            objective_refs.append(
                cls._reference(
                    resource_kind="design_space",
                    resource_id=project.design_space_digest or project_id,
                    value=objective_payload["design_space"],
                )
            )
        task_payload = {
            "task_id": project.task_id,
            "task_contract_digest": project.task_contract_digest,
            "model_package_ref_id": project.model_package_ref_id,
            "model_package_manifest_digest": project.model_package_manifest_digest,
        }
        project_refs = (
            cls._reference(
                resource_kind="project",
                resource_id=project_id,
                value=project_payload,
            ),
            cls._reference(
                resource_kind="task_capability",
                resource_id=project.task_id,
                value={
                    "task_id": project.task_id,
                    "task_contract_digest": project.task_contract_digest,
                },
            ),
            cls._reference(
                resource_kind="model_package",
                resource_id=project.model_package_ref_id or "unbound",
                value={
                    "reference_id": project.model_package_ref_id,
                    "manifest_digest": project.model_package_manifest_digest,
                },
            ),
        )
        return {
            "project_summary": AiReviewToolObservation(
                "project_summary",
                "untrusted_data",
                {"project": project_payload, "runtime_binding": task_payload},
                project_refs,
            ),
            "candidate_revision": AiReviewToolObservation(
                "candidate_revision",
                "untrusted_data",
                candidate_payload,
                (
                    cls._reference(
                        resource_kind="candidate_revision",
                        resource_id=candidate_id,
                        revision=candidate_revision,
                        value=candidate_payload,
                    ),
                ),
            ),
            "predictive_snapshots": AiReviewToolObservation(
                "predictive_snapshots",
                "untrusted_data",
                tuple(snapshots),
                tuple(snapshot_refs),
            ),
            "decision_activity_runs": AiReviewToolObservation(
                "decision_activity_runs",
                "untrusted_data",
                tuple(activities),
                tuple(activity_refs),
            ),
            "actual_measurements": AiReviewToolObservation(
                "actual_measurements",
                "untrusted_data",
                tuple(actuals),
                tuple(actual_refs),
            ),
            "objective_and_design_space": AiReviewToolObservation(
                "objective_and_design_space",
                "untrusted_data",
                objective_payload,
                tuple(objective_refs),
            ),
        }

    def call(self, tool_name: str) -> AiReviewToolObservation:
        if tool_name not in AI_REVIEW_READ_TOOLS:
            raise AiReviewToolError(f"AI Review toolは許可されていません: {tool_name}")
        return self.__observations[tool_name]

    def all_evidence_refs(self) -> tuple[AiReviewEvidenceReference, ...]:
        return tuple(self.__evidence.values())

    def input_snapshot_digest(self) -> str:
        snapshot = {
            name: {
                "trust_level": observation.trust_level,
                "payload": observation.payload,
            }
            for name, observation in self.__observations.items()
        }
        return _digest(snapshot)

    def validate_evidence_refs(
        self, refs: tuple[AiReviewEvidenceReference, ...]
    ) -> None:
        for ref in refs:
            issued = self.__evidence.get(self.reference_key(ref))
            if issued is None:
                raise AiReviewToolError("AI Reviewが未観測のevidenceを参照しました")
            if (
                ref.resource_kind
                in {
                    "candidate_revision",
                    "predictive_snapshot",
                    "decision_activity_run",
                    "actual_measurement",
                }
                and ref.revision != self.candidate_revision
            ):
                raise AiReviewToolError(
                    "AI Review evidenceのcandidate revisionがcurrent revisionと一致しません"
                )

    def validate_claim_grounding(self, findings: tuple[Any, ...]) -> None:
        """Require every claimed numeric literal to exist in attached evidence."""

        for finding in findings:
            refs = tuple(finding.evidence_refs)
            self.validate_evidence_refs(refs)
            observed_numbers: set[Decimal] = set()
            for ref in refs:
                observed = self.__evidence_values.get(self.reference_key(ref))
                if observed is not None:
                    observed_numbers.update(_numeric_values(observed))
            text = f"{finding.claim} {finding.reasoning_summary}"
            for raw in _NUMERIC_LITERAL.findall(text):
                try:
                    claimed = Decimal(raw).normalize()
                except InvalidOperation:
                    raise AiReviewToolError(
                        "AI Review claimの数値を検証できません"
                    ) from None
                if claimed not in observed_numbers:
                    raise AiReviewToolError(
                        "AI Review claimの数値が添付evidenceに存在しません"
                    )
