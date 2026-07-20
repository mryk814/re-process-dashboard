from __future__ import annotations

from typing import Any, Mapping

from .schemas import CandidateInput


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
        return CandidateInput.model_validate({"name": name, "inputs": raw.get("inputs"), "provenance": provenance})
    if version == "prediction-snapshot-v1":
        required = {"composition", "thickness_mm", "line_speed_m_min", "coating", "heat_pattern"}
        if not required <= set(raw):
            raise SnapshotPayloadError("旧スナップショットの候補入力が不完全です")
        return CandidateInput.model_validate({
            "name": name,
            "inputs": {
                "composition": raw["composition"],
                "process": {
                    "thickness_mm": raw["thickness_mm"],
                    "line_speed_m_min": raw["line_speed_m_min"],
                },
                "categorical": {"coating": raw["coating"]},
                "heat_pattern": raw["heat_pattern"],
            },
            "provenance": provenance,
        })
    raise SnapshotPayloadError(f"未対応のスナップショット形式です: {version!r}")
