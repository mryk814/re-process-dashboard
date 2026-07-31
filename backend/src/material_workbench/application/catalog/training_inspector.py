"""Use case for tracing raw, selected, and feature-ready training rows."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from material_workbench.application.catalog.errors import (
    CatalogValidationError,
    lifecycle_profile,
    require_project,
)
from material_workbench.application.project_runtime import ProjectRuntimeResolver
from material_workbench.data.importer import training_context_key
from material_workbench.modeling.model_lifecycle import (
    canonical_training_dataset,
    canonical_training_dataset_digest,
    validate_lifecycle_metadata,
)
from material_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
)
from material_workbench.persistence.store import Store
from material_workbench.tasks.task_registry import TaskRegistry


_SUPPORTED_TRAINING_UNITS = {
    "individual_observation",
    "parent_condition_mean",
    "replicate_context_mean",
    "source_row",
    "independent source row",
    "source_row_grouped_by_parent",
    "wear_measurement_row",
}


@dataclass(frozen=True)
class TrainingInspector:
    store: Store
    registry: TaskRegistry
    resolver: ProjectRuntimeResolver

    def model_training_data(
        self,
        project_id: str,
        *,
        stage: Literal["curation", "selected", "features"] = "selected",
        target: str | None = None,
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        project = require_project(self.store, project_id)
        resolved = self.resolver.resolve(project)
        package = resolved.runtime.model_package
        assert package is not None
        contract = self.registry.contract_for(project.task_id)
        adapter = self.registry.training_inspector_for(project.task_id)
        data = resolved.runtime.data
        validate_lifecycle_metadata(
            package,
            contract,
            profile_path=lifecycle_profile(data),
        )
        available_targets = [item.target for item in package.manifest.predictors]
        selected_target = target or available_targets[0]
        if selected_target not in available_targets:
            raise CatalogValidationError(
                f"model package does not predict target: {selected_target}"
            )
        canonical = canonical_training_dataset(
            project.task_id,
            data,
            contract,
            pipeline_version=package.manifest.feature_pipeline.version,
        )
        selected_rows = [
            row for row in canonical["rows"] if selected_target in row["outputs"]
        ]
        predictor = next(
            item
            for item in package.manifest.predictors
            if item.target == selected_target
        )
        training_unit = str(
            predictor.config.get("training_unit", "individual_observation")
        )
        if training_unit not in _SUPPORTED_TRAINING_UNITS:
            raise CatalogValidationError(
                f"unsupported model training unit: {training_unit}"
            )
        observations = {str(row["id"]): row for row in data.observations}
        output = next(
            item
            for item in contract.task_definition.outputs
            if item.key == selected_target
        )
        compiled_training = (
            compile_target_training_set(
                canonical,
                target=selected_target,
                unit=output.unit,
            )
            if training_unit == "replicate_context_mean"
            else None
        )
        model_row_count = (
            len(compiled_training.y)
            if compiled_training is not None
            else len({training_context_key(row) for row in selected_rows})
            if training_unit == "parent_condition_mean"
            else len(selected_rows)
        )
        curation_rows = [
            (
                row,
                row.get("run_context", {})
                .get("curation", {})
                .get("status", "accepted"),
            )
            for row in data.observations
        ]
        target_curation_summaries = []
        for target_key in available_targets:
            target_rows = [
                row
                for row, _ in curation_rows
                if row["eligible"] and target_key in row["outputs"]
            ]
            target_exclusion_reasons: dict[str, int] = {}
            for row, _ in curation_rows:
                state = (
                    row.get("run_context", {})
                    .get("curation", {})
                    .get("target_status", {})
                    .get(target_key, {})
                )
                if not state or state.get("usable"):
                    continue
                reason = str(state.get("reason") or "値なし")
                target_exclusion_reasons[reason] = (
                    target_exclusion_reasons.get(reason, 0) + 1
                )
            target_curation_summaries.append({
                "target": target_key,
                "usable_rows": len(target_rows),
                "source_groups": len(
                    {str(row["parent_key"]) for row in target_rows}
                ),
                "exclusion_reasons": dict(
                    sorted(
                        target_exclusion_reasons.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ),
            })
        exclusion_reasons: dict[str, int] = {}
        for row, _ in curation_rows:
            for reason in row.get("run_context", {}).get("curation", {}).get(
                "reasons", []
            ):
                exclusion_reasons[str(reason)] = (
                    exclusion_reasons.get(str(reason), 0) + 1
                )
        curation_summary = {
            "source_rows": len(data.observations),
            "input_usable_rows": sum(
                status in {"accepted", "warning"} for _, status in curation_rows
            ),
            "accepted_rows": sum(
                status == "accepted" for _, status in curation_rows
            ),
            "warning_rows": sum(
                status == "warning" for _, status in curation_rows
            ),
            "quarantined_rows": sum(
                status == "quarantined" for _, status in curation_rows
            ),
            "blocked_rows": sum(
                status == "blocked" for _, status in curation_rows
            ),
            "exclusion_reasons": dict(
                sorted(
                    exclusion_reasons.items(), key=lambda item: (-item[1], item[0])
                )
            ),
            "targets": target_curation_summaries,
        }
        identifier_columns = [
            {
                "key": "observation_id",
                "label": "実測ID",
                "unit": None,
                "group": "識別",
            },
            {
                "key": "parent_key",
                "label": "親工程条件",
                "unit": None,
                "group": "識別",
            },
        ]
        if stage == "curation":
            curation_columns = tuple(
                resolved.runtime.data.profile.curation_recipe.columns
                if getattr(resolved.runtime.data.profile, "curation_recipe", None)
                else ()
            )
            columns = [
                *identifier_columns,
                {
                    "key": "curation.status",
                    "label": "採否",
                    "unit": None,
                    "group": "判定",
                },
                {
                    "key": "curation.notes",
                    "label": "理由・注意",
                    "unit": None,
                    "group": "判定",
                },
                {
                    "key": "curation.transforms",
                    "label": "適用した前処理",
                    "unit": None,
                    "group": "判定",
                },
                {
                    "key": "curation.targets",
                    "label": "目的変数の利用可否",
                    "unit": None,
                    "group": "判定",
                },
                *[
                    {
                        "key": f"raw.{column}",
                        "label": column,
                        "unit": None,
                        "group": "原値",
                    }
                    for column in curation_columns
                ],
                *[
                    {
                        "key": f"normalized.{column}",
                        "label": column,
                        "unit": None,
                        "group": "正規化",
                    }
                    for column in curation_columns
                ],
            ]
            page_rows = []
            for observation in data.observations[offset : offset + limit]:
                curation = observation.get("run_context", {}).get("curation", {})
                values_by_column = curation.get("values", {})
                notes = [
                    *curation.get("reasons", []),
                    *curation.get("warnings", []),
                ]
                transforms = []
                for column, trace in values_by_column.items():
                    raw_value = str(trace.get("raw", ""))
                    normalized_value = trace.get("normalized")
                    changed = raw_value != str(normalized_value)
                    if changed:
                        try:
                            changed = float(raw_value) != float(normalized_value)
                        except (TypeError, ValueError):
                            pass
                    if changed:
                        transforms.append(
                            f"{column}: {raw_value or '空欄'} → {normalized_value}"
                        )
                target_states = [
                    f"{target_key}: {'採用' if state.get('usable') else state.get('reason') or '不採用'}"
                    for target_key, state in curation.get(
                        "target_status", {}
                    ).items()
                ]
                values: dict[str, Any] = {
                    "observation_id": observation["id"],
                    "parent_key": observation["parent_key"],
                    "curation.status": curation.get("status", "accepted"),
                    "curation.notes": " / ".join(notes) if notes else "—",
                    "curation.transforms": (
                        " / ".join(transforms) if transforms else "変換なし"
                    ),
                    "curation.targets": (
                        " / ".join(target_states) if target_states else "—"
                    ),
                }
                for column in curation_columns:
                    trace = values_by_column.get(column, {})
                    values[f"raw.{column}"] = trace.get("raw")
                    values[f"normalized.{column}"] = trace.get("normalized")
                page_rows.append({
                    "observation_id": observation["id"],
                    "parent_key": observation["parent_key"],
                    "values": values,
                })
        elif stage == "selected":
            input_fields = [
                field
                for group in contract.task_definition.input_groups
                for field in group.fields
            ]
            columns = [
                *identifier_columns,
                *[
                    {
                        "key": field.path,
                        "label": field.label,
                        "unit": field.unit,
                        "group": "入力",
                    }
                    for field in input_fields
                ],
                {
                    "key": f"output.{selected_target}",
                    "label": f"{output.label}（実測）",
                    "unit": output.unit,
                    "group": "実測",
                },
            ]
            input_paths = [field.path for field in input_fields]
            page_rows = []
            for row in selected_rows[offset : offset + limit]:
                observation = observations[row["observation_id"]]
                values: dict[str, Any] = {
                    "observation_id": row["observation_id"],
                    "parent_key": row["parent_key"],
                    f"output.{selected_target}": row["outputs"][selected_target],
                }
                values.update(adapter.selected_input_values(observation, input_paths))
                page_rows.append({
                    "observation_id": row["observation_id"],
                    "parent_key": row["parent_key"],
                    "values": values,
                })
        else:
            model_rows = selected_rows
            predictor_feature_names = set(predictor.feature_names)
            target_feature_specs = [
                feature
                for feature in canonical["feature_pipeline"]["features"]
                if feature["name"] in predictor_feature_names
            ]
            if training_unit == "parent_condition_mean":
                grouped: dict[str, list[dict[str, Any]]] = {}
                for row in selected_rows:
                    grouped.setdefault(training_context_key(row), []).append(row)
                model_rows = [
                    {
                        "observation_id": context_key,
                        "parent_key": group_rows[0]["parent_key"],
                        "condition_context_id": context_key,
                        "presentation": dict(
                            adapter.parent_condition_metadata(group_rows)
                        ),
                        "observation_ids": [
                            str(row["observation_id"]) for row in group_rows
                        ],
                        "replicate_count": len(group_rows),
                        "features": {
                            key: sum(
                                float(row["features"][key]) for row in group_rows
                            )
                            / len(group_rows)
                            for key in group_rows[0]["features"]
                        },
                        "outputs": {
                            selected_target: sum(
                                float(row["outputs"][selected_target])
                                for row in group_rows
                            )
                            / len(group_rows)
                        },
                    }
                    for context_key, group_rows in sorted(grouped.items())
                ]
            elif training_unit == "replicate_context_mean":
                assert compiled_training is not None
                compiled = compiled_training
                model_rows = [
                    {
                        "observation_id": context_id,
                        "parent_key": validation_group,
                        "replicate_context": context_id,
                        "validation_group": validation_group,
                        "observation_ids": list(observation_ids),
                        "replicate_count": repeat_count,
                        "features": {
                            feature_name: float(compiled.x[index, feature_index])
                            for feature_index, feature_name in enumerate(
                                compiled.feature_names
                            )
                        },
                        "outputs": {
                            selected_target: float(compiled.y[index]),
                        },
                    }
                    for index, (
                        context_id,
                        validation_group,
                        observation_ids,
                        repeat_count,
                    ) in enumerate(
                        zip(
                            compiled.replicate_contexts,
                            compiled.validation_groups,
                            compiled.observation_ids,
                            compiled.repeat_counts,
                            strict=True,
                        )
                    )
                ]
            feature_identifier_columns = list(
                adapter.feature_identifier_columns(training_unit)
            )
            columns = [
                *feature_identifier_columns,
                *[
                    {
                        "key": f"feature.{feature['name']}",
                        "label": feature["name"],
                        "unit": feature["unit"],
                        "group": "特徴量",
                    }
                    for feature in target_feature_specs
                ],
                {
                    "key": f"output.{selected_target}",
                    "label": f"{output.label}（実測）",
                    "unit": output.unit,
                    "group": "実測",
                },
            ]
            page_rows = [
                {
                    "observation_id": row["observation_id"],
                    "parent_key": row["parent_key"],
                    "values": {
                        **dict(adapter.feature_identifier_values(row, training_unit)),
                        **{
                            f"feature.{key}": value
                            for key, value in row["features"].items()
                        },
                        f"output.{selected_target}": row["outputs"][
                            selected_target
                        ],
                    },
                }
                for row in model_rows[offset : offset + limit]
            ]
        return {
            "stage": stage,
            "target": selected_target,
            "target_label": output.label,
            "source_data_digest": canonical["source_data_digest"],
            "feature_dataset_digest": canonical_training_dataset_digest(canonical),
            "feature_pipeline_id": canonical["feature_pipeline"]["id"],
            "feature_pipeline_version": canonical["feature_pipeline"]["version"],
            "training_unit": training_unit,
            "stage_counts": {
                "source_rows": len(data.observations),
                "selected_rows": len(selected_rows),
                "model_rows": model_row_count,
            },
            "total": (
                len(data.observations)
                if stage == "curation"
                else len(selected_rows)
                if stage == "selected"
                else len(model_rows)
            ),
            "parent_conditions": (
                len({str(row["parent_key"]) for row in data.observations})
                if stage == "curation"
                else len({training_context_key(row) for row in selected_rows})
            ),
            "offset": offset,
            "limit": limit,
            "columns": columns,
            "rows": page_rows,
            "curation_summary": curation_summary,
        }
