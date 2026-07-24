from __future__ import annotations

import csv
from collections import Counter
from io import StringIO
from statistics import fmean, pstdev
from typing import Literal

from .candidates import CandidateService
from .projects import ProjectService
from material_workbench.data.importer import lineage_neighborhood, lineage_node_detail
from material_workbench.contracts.schemas import (
    Candidate,
    LineageIndexResponse,
    LineageNodeReview,
    LineageNodeReviewInput,
    LineageNodeReviewList,
    LineageResponse,
    QualityResponse,
)
from material_workbench.domain.services import candidate_from_lineage, lineage_candidate_options
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import DataExplorerEntry, TaskRegistry, TaskRegistryError
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver


class DataExplorerUnavailableError(LookupError):
    pass


class LineageNotFoundError(LookupError):
    pass


class DataExplorationValidationError(ValueError):
    pass


class DataExplorationService:
    def __init__(self, store: Store, registry: TaskRegistry, resolver: ProjectRuntimeResolver) -> None:
        self.store = store
        self.registry = registry
        self.resolver = resolver
        self.projects = ProjectService(store, registry)
        self.candidates = CandidateService(store, registry, resolver)

    def explorer(self, project_id: str, capability: Literal["quality", "lineage"]) -> DataExplorerEntry:
        project = self.projects.require(project_id)
        try:
            explorer = self.resolver.data_explorer_for(project)
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
        issue_filter: Literal["all", "with_issues", "without_issues"] = "all",
        include_hidden: bool = False,
        limit: int = 200,
    ) -> LineageIndexResponse:
        data = self.explorer(project_id, "lineage").data
        normalized = query.strip().casefold()
        issue_keys = {issue["entity_key"] for issue in data.detected_quality if issue["entity_key"]}
        reviews = self.store.list_lineage_reviews(project_id)
        review_by_key = {review.entity_key: review for review in reviews}
        items: list[dict] = []
        counts: Counter[str] = Counter()
        for sheet_name, key_column in data.entity_sheets.items():
            records = data.entities.get(key_column, {})
            counts[sheet_name] += len(records)
            if entity_type and sheet_name != entity_type:
                continue
            for key, source_row in records.items():
                review = review_by_key.get(key)
                if review is not None and review.status == "hidden" and not include_hidden:
                    continue
                metadata = self._lineage_metadata(data, sheet_name, key, source_row)
                search_text = " ".join([key, *(str(value) for value in metadata.values() if not isinstance(value, dict))]).casefold()
                if normalized and normalized not in search_text:
                    continue
                if issue_filter == "with_issues" and key not in issue_keys:
                    continue
                if issue_filter == "without_issues" and key in issue_keys:
                    continue
                items.append({
                    "key": key,
                    "entity_type": sheet_name,
                    "has_issue": key in issue_keys,
                    "review_status": review.status if review else None,
                    "review_note": review.note if review else None,
                    **metadata,
                })
        known_keys = {item["key"] for item in items}
        for issue in data.detected_quality:
            if issue_filter == "without_issues":
                break
            key = issue["entity_key"]
            if not key or key in known_keys or key not in data.lineage:
                continue
            review = review_by_key.get(key)
            if review is not None and review.status == "hidden" and not include_hidden:
                continue
            relations = data.lineage[key]
            key_column = next((column for column, values in relations.items() if key in values), "")
            sheet_name = next((sheet for sheet, column in data.entity_sheets.items() if column == key_column), issue["source_sheet"])
            if entity_type and sheet_name != entity_type:
                continue
            if normalized and normalized not in key.casefold():
                continue
            items.append({
                "key": key,
                "entity_type": sheet_name,
                "has_issue": True,
                "review_status": review.status if review else None,
                "review_note": review.note if review else None,
            })
            known_keys.add(key)
        items.sort(key=lambda item: (not item["has_issue"], item["entity_type"], item["key"]))
        return LineageIndexResponse.model_validate({
            "items": items[:max(1, min(limit, 500))],
            "matched_entities": len(items),
            "total_entities": sum(counts.values()),
            "relation_rows": len(data.sheets[data.relation_sheet]),
            "detected_issues": len(data.detected_quality),
            "counts_by_type": counts,
            "review_count": len(reviews),
        })

    def lineage(
        self,
        project_id: str,
        entity_key: str,
        *,
        limit: int = 40,
        all_reachable: bool = False,
    ) -> LineageResponse:
        project = self.projects.require(project_id)
        data = self.explorer(project_id, "lineage").data
        item = data.lineage.get(entity_key)
        if item is None:
            raise LineageNotFoundError("来歴が見つかりません")
        try:
            node = lineage_node_detail(data, entity_key)
        except KeyError as exc:
            raise LineageNotFoundError("来歴ノードの元データが見つかりません") from exc
        graph = lineage_neighborhood(
            data,
            entity_key,
            max_nodes=limit,
            all_reachable=all_reachable,
        )
        connected_keys = {graph_node["key"] for graph_node in graph["nodes"]}
        issues = [issue for issue in data.detected_quality if issue["entity_key"] in connected_keys]
        candidate_options = []
        for option in lineage_candidate_options(data, entity_key):
            try:
                payload = candidate_from_lineage(
                    data,
                    entity_key,
                    process_key=option.process_key,
                    melt_key=option.melt_key,
                )
                self.registry.validate_candidate(project.task_id, payload)
            except (TaskRegistryError, ValueError):
                continue
            candidate_options.append(option)
        candidate_eligible = bool(candidate_options)
        if len(candidate_options) == 1:
            candidate_reason = "接続された実績を候補入力として引き継げます"
        elif candidate_options:
            candidate_reason = f"工程条件と成分の組合せを選んで候補にできます（{len(candidate_options)}通り）"
        else:
            candidate_reason = "候補化できる工程条件と成分の組み合わせが見つかりません"
        return LineageResponse.model_validate({
            "key": entity_key,
            "relations": item,
            "quality_issues": issues,
            "node": node,
            "graph": graph,
            "candidate_eligible": candidate_eligible,
            "candidate_reason": candidate_reason,
            "candidate_options": candidate_options,
            "review": self.store.get_lineage_review(project_id, entity_key),
        })

    def lineage_reviews(self, project_id: str) -> LineageNodeReviewList:
        self.explorer(project_id, "lineage")
        items = self.store.list_lineage_reviews(project_id)
        return LineageNodeReviewList(
            items=items,
            counts_by_status=Counter(item.status for item in items),
        )

    def save_lineage_review(
        self,
        project_id: str,
        entity_key: str,
        payload: LineageNodeReviewInput,
    ) -> LineageNodeReview:
        data = self.explorer(project_id, "lineage").data
        if entity_key not in data.lineage:
            raise LineageNotFoundError(f"キー {entity_key} は系譜に存在しません")
        return self.store.upsert_lineage_review(project_id, entity_key, payload)

    def delete_lineage_review(self, project_id: str, entity_key: str) -> bool:
        self.projects.require(project_id)
        return self.store.delete_lineage_review(project_id, entity_key)

    def lineage_reviews_csv(self, project_id: str) -> str:
        labels = {
            "noted": "メモ",
            "later": "後で確認",
            "accepted": "問題なし",
            "needs_fix": "要修正",
            "hidden": "非表示",
        }
        output = StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "status", "status_label", "entity_key", "entity_type",
                "note", "updated_at",
            ],
        )
        writer.writeheader()
        writer.writerows({
            "status": review.status,
            "status_label": labels[review.status],
            "entity_key": review.entity_key,
            "entity_type": review.entity_type,
            "note": review.note,
            "updated_at": review.updated_at.isoformat(),
        } for review in self.lineage_reviews(project_id).items)
        return "\ufeff" + output.getvalue()

    def create_candidate_from_lineage(
        self,
        project_id: str,
        entity_key: str,
        *,
        process_key: str | None = None,
        melt_key: str | None = None,
    ) -> Candidate:
        explorer = self.explorer(project_id, "lineage")
        if not explorer.capability.candidate_creation:
            raise DataExplorerUnavailableError("このプロジェクトでは実績から候補を作成できません")
        try:
            payload = candidate_from_lineage(
                explorer.data,
                entity_key,
                process_key=process_key,
                melt_key=melt_key,
            )
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
            "melt_keys": melt_keys,
            "project": str(source_row.get(data.technical_columns.get(("annealing", "project"))) or ""),
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
