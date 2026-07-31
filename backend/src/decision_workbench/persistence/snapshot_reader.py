from __future__ import annotations

from typing import Any, Mapping

from decision_workbench.contracts.candidate_project_contracts import CandidateInput


class SnapshotPayloadError(ValueError):
    pass


def candidate_input_from_snapshot(snapshot_id: str, payload: Mapping[str, Any]) -> CandidateInput:
    version = payload.get("snapshot_schema_version")
    raw = payload.get("raw_candidate")
    if not isinstance(raw, dict):
        raise SnapshotPayloadError("このスナップショットには復元可能な候補入力がありません")
    name = f"{raw.get('name') or '候補'} (復元)"
    provenance = {"source_kind": "snapshot", "source_ref": {"snapshot_id": snapshot_id}}
    if version == "prediction-snapshot-v2":
        return CandidateInput.model_validate({
            "name": name,
            "inputs": raw.get("inputs"),
            "blend": raw.get("blend"),
            "editor_state": raw.get("editor_state") or {},
            # blend_validation is deliberately omitted. CandidateService resolves
            # the pinned resources and recomputes it before saving the restore.
            "provenance": provenance,
        })
    if version == "prediction-snapshot-v1":
        raise SnapshotPayloadError("旧スナップショットは入力項目の改定後に復元できません。候補を新しい項目で作り直してください")
    raise SnapshotPayloadError(f"未対応のスナップショット形式です: {version!r}")
