from __future__ import annotations

import csv
from collections import Counter
from io import StringIO
from statistics import fmean, pstdev
from typing import Literal

from .candidates import CandidateService
from .projects import ProjectService
from ..importer import lineage_neighborhood, lineage_node_detail
from ..schemas import Candidate, LineageIndexResponse, LineageResponse, QualityResponse
from ..services import candidate_from_lineage
from ..store import Store
from ..task_registry import DataExplorerEntry, TaskRegistry, TaskRegistryError


class DataExplorerUnavailableError(LookupError):
    pass


class LineageNotFoundError(LookupError):
    pass


class DataExplorationValidationError(ValueError):
    pass


class DataExplorationService:
    def __init__(self, store: Store, registry: TaskRegistry) -> None:
        self.store = store
        self.registry = registry
        self.projects = ProjectService(store, registry)
        self.candidates = CandidateService(store, registry)

    def explorer(self, project_id: str, capability: Literal["quality", "lineage"]) -> DataExplorerEntry:
        project = self.projects.require(project_id)
        try:
            explorer = self.registry.data_explorer_for(project.task_id)
        except TaskRegistryError as exc:
            raise DataExplorerUnavailableError("このプロジェクトではデータ探索を利用できません") from exc
        if not getattr(explorer.capability, capability):
            raise DataExplorerUnavailableError("このプロジェクトではデータ探索を利用できません")
        return explorer

    def quality(self, project_id: str) -> QualityResponse:
        project = self.projects.require(project_id)
        data = self.explorer(project_id, "quality").data
        scenarios = data.quality
        detected = data.detected_quality
        quality_category = data.technical_columns.get(("quality", "category"))
        return QualityResponse.model_validate({
            "total": len(scenarios),
            "by_category": Counter(row[quality_category] for row in scenarios) if quality_category else {},
            "issues": scenarios,
            "reference_scenarios": scenarios,
            "detected_total": len(detected),
            "detected_by_type": Counter(row["issue_type"] for row in detected),
            "detected_issues": detected,
            "dataset": {
                "task_id": project.task_id,
                "source_path": data.source_path,
                "source_sha256": data.source_sha256,
                "profile_id": data.profile_id,
                "profile_path": data.profile_path,
            },
        })

    def quality_csv(self, project_id: str) -> str:
        output = StringIO()
        issues = self.explorer(project_id, "quality").data.detected_quality
        fieldnames = [
            "issue_id", "issue_type", "source_sheet", "entity_key", "detail",
            "focus_entity_key", "related_entity_keys", "missing_reference_key", "suggested_view",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows({**issue, "related_entity_keys": "|".join(issue["related_entity_keys"])} for issue in issues)
        return "\ufeff" + output.getvalue()

    def lineage_index(
        self,
        project_id: str,
        *,
        query: str = "",
        entity_type: str = "",
        issue_only: bool = False,
        limit: int = 40,
    ) -> LineageIndexResponse:
        data = self.explorer(project_id, "lineage").data
        normalized = query.strip().casefold()
        issue_keys = {issue["entity_key"] for issue in data.detected_quality if issue["entity_key"]}
        items: list[dict] = []
        counts: Counter[str] = Counter()
        for sheet_name, key_column in data.entity_sheets.items():
            records = data.entities[key_column]
            counts[sheet_name] += len(records)
            if entity_type and sheet_name != entity_type:
                continue
            for key, source_row in records.items():
                metadata = self._lineage_metadata(data, sheet_name, key, source_row)
                search_text = " ".join([key, *(str(value) for value in metadata.values() if not isinstance(value, dict))]).casefold()
                if normalized and normalized not in search_text:
                    continue
                if issue_only and key not in issue_keys:
                    continue
                items.append({"key": key, "entity_type": sheet_name, "has_issue": key in issue_keys, **metadata})
        known_keys = {item["key"] for item in items}
        for issue in data.detected_quality:
            key = issue["entity_key"]
            if not key or key in known_keys or key not in data.lineage:
                continue
            relations = data.lineage[key]
            key_column = next((column for column, values in relations.items() if key in values), "")
            sheet_name = next((sheet for sheet, column in data.entity_sheets.items() if column == key_column), issue["source_sheet"])
            if entity_type and sheet_name != entity_type:
                continue
            if normalized and normalized not in key.casefold():
                continue
            items.append({"key": key, "entity_type": sheet_name, "has_issue": True})
            known_keys.add(key)
        items.sort(key=lambda item: (not item["has_issue"], item["entity_type"], item["key"]))
        return LineageIndexResponse.model_validate({
            "items": items[:max(1, min(limit, 100))],
            "total_entities": sum(counts.values()),
            "relation_rows": len(data.sheets[data.relation_sheet]),
            "detected_issues": len(data.detected_quality),
            "counts_by_type": counts,
        })

    def lineage(self, project_id: str, entity_key: str, *, limit: int = 40) -> LineageResponse:
        project = self.projects.require(project_id)
        data = self.explorer(project_id, "lineage").data
        item = data.lineage.get(entity_key)
        if item is None:
            raise LineageNotFoundError("来歴が見つかりません")
        try:
            node = lineage_node_detail(data, entity_key)
        except KeyError as exc:
            raise LineageNotFoundError("来歴ノードの元データが見つかりません") from exc
        graph = lineage_neighborhood(data, entity_key, max_nodes=limit)
        connected_keys = {graph_node["key"] for graph_node in graph["nodes"]}
        issues = [issue for issue in data.detected_quality if issue["entity_key"] in connected_keys]
        try:
            payload = candidate_from_lineage(data, entity_key)
            self.registry.validate_candidate(project.task_id, payload)
        except (TaskRegistryError, ValueError) as exc:
            candidate_eligible = False
            candidate_reason = str(exc)
        else:
            candidate_eligible = True
            candidate_reason = "接続された実績を候補入力として引き継げます"
        return LineageResponse.model_validate({
            "key": entity_key,
            "relations": item,
            "quality_issues": issues,
            "node": node,
            "graph": graph,
            "candidate_eligible": candidate_eligible,
            "candidate_reason": candidate_reason,
        })

    def create_candidate_from_lineage(self, project_id: str, entity_key: str) -> Candidate:
        explorer = self.explorer(project_id, "lineage")
        if not explorer.capability.candidate_creation:
            raise DataExplorerUnavailableError("このプロジェクトでは実績から候補を作成できません")
        try:
            payload = candidate_from_lineage(explorer.data, entity_key)
        except ValueError as exc:
            raise DataExplorationValidationError(str(exc)) from exc
        return self.candidates.create(project_id, payload)

    @staticmethod
    def _lineage_metadata(data, sheet_name: str, key: str, source_row: dict) -> dict:
        if sheet_name != data.role_to_sheet["annealing"]:
            return {}
        relations = data.lineage.get(key, {})
        melt_key_column = data.role_to_key["melt"]
        melt_keys = sorted(set(relations.get(melt_key_column, [])))
        melt_row = data.entities.get(melt_key_column, {}).get(melt_keys[0], {}) if len(melt_keys) == 1 else {}
        feature = data.anneal_features.get(key, {})
        family_column = data.technical_columns.get(("melt", "family"))
        learning_flag_column = data.policy_columns.get(("annealing", "learning_flag/v1"))
        values_by_property: dict[str, list[float]] = {}
        for observation in data.observations:
            if observation["parent_key"] != key or observation["source"] == data.role_to_sheet["hot_tensile"]:
                continue
            for property_name, value in observation["outputs"].items():
                values_by_property.setdefault(property_name, []).append(float(value))
        return {
            "family": str(melt_row.get(family_column) or "") if family_column else "",
            "project": str(source_row.get(data.technical_columns[("annealing", "project")]) or ""),
            "route": str(feature.get("standard_route") or ""),
            "peak_temperature_c": feature.get("max_temperature_c"),
            "learning_status": str(source_row.get(learning_flag_column) or "") if learning_flag_column else "",
            "has_observation": bool(values_by_property),
            "observation_summary": {
                property_name: {
                    "mean": round(float(fmean(values)), 3),
                    "std": round(float(pstdev(values)), 3),
                    "n": len(values),
                }
                for property_name, values in sorted(values_by_property.items())
            },
        }
