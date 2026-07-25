from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from .dependencies import (
    get_project_runtime_resolver,
    get_store,
    get_task_registry,
    project_or_404,
)
from .errors import PROJECT_API_ERRORS
from material_workbench.modeling.model_lifecycle import (
    canonical_training_dataset,
    canonical_training_dataset_digest,
    validate_lifecycle_metadata,
)
from material_workbench.modeling.model_packages import PREDICTOR_RUNTIME_TYPES
from material_workbench.data.importer import training_context_key
from material_workbench.contracts.schemas import ModelPackageStatus, ModelTrainingDataPage, TaskCatalogItem
from material_workbench.persistence.store import Store
from material_workbench.contracts.task_contracts import ResolvedTaskDefinition
from material_workbench.tasks.task_registry import TaskRegistry
from material_workbench.tasks.project_runtime_resolver import ProjectRuntimeResolver


router = APIRouter()
StoreDependency = Annotated[Store, Depends(get_store)]
RegistryDependency = Annotated[TaskRegistry, Depends(get_task_registry)]
ResolverDependency = Annotated[ProjectRuntimeResolver, Depends(get_project_runtime_resolver)]


@router.get("/api/health")
@router.get("/health", include_in_schema=False)
def health(store: StoreDependency, registry: RegistryDependency) -> dict[str, Any]:
    available = set(registry.available_task_ids)
    default_project = store.get_project("default")
    default_runtime = (
        registry.runtime_for(default_project.task_id)
        if default_project is not None and default_project.task_id in available
        else None
    )
    return {
        "ok": True,
        "ready": True,
        "degraded": len(available) != len(registry.task_ids),
        "models": sorted(default_runtime.models) if default_runtime is not None else [],  # type: ignore[attr-defined]
        "source": default_runtime.data.source_path if default_runtime is not None else None,
        "tasks": {
            task_id: (
                {
                    "availability": registry.availability_for(task_id).model_dump(mode="json"),
                    "package_id": registry.entry_for(task_id).model_package.manifest.package_id,
                    "outputs": sorted(registry.runtime_for(task_id).output_keys),
                    "source": registry.runtime_for(task_id).data.source_path,
                }
                if task_id in available
                else {
                    "availability": registry.availability_for(task_id).model_dump(mode="json"),
                    "package_id": None,
                    "outputs": [],
                    "source": None,
                }
            )
            for task_id in registry.task_ids
        },
    }


@router.get("/api/readiness", operation_id="getReadiness")
def readiness(registry: RegistryDependency) -> dict[str, Any]:
    unavailable = {
        task_id: registry.availability_for(task_id).model_dump(mode="json")
        for task_id in registry.task_ids
        if registry.availability_for(task_id).status == "unavailable"
    }
    return {
        "ready": True,
        "degraded": bool(unavailable),
        "available_tasks": list(registry.available_task_ids),
        "unavailable_tasks": unavailable,
    }


@router.get(
    "/api/projects/{project_id}/model-package",
    response_model=ModelPackageStatus,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectModelPackage",
)
def model_package(
    project_id: str,
    store: StoreDependency,
    registry: RegistryDependency,
    resolver: ResolverDependency,
) -> dict[str, Any]:
    project = project_or_404(store, project_id)
    resolved = resolver.resolve(project)
    package = resolved.runtime.model_package
    assert package is not None
    manifest = package.manifest
    quality = validate_lifecycle_metadata(
        package,
        registry.contract_for(project.task_id),
        profile_path=getattr(resolved.runtime.data, "profile", Path(resolved.runtime.data.profile_path)),
    )
    optional_dependencies = {
        "sklearn.skops.v1": importlib.util.find_spec("skops") is not None,
        "lightgbm.booster.v1": importlib.util.find_spec("lightgbm") is not None,
        "gpytorch.static_exact_rbf.v1": (
            importlib.util.find_spec("torch") is not None
            and importlib.util.find_spec("safetensors") is not None
        ),
    }
    dependencies = {
        runtime_type: optional_dependencies.get(runtime_type, True)
        for runtime_type in PREDICTOR_RUNTIME_TYPES
    }
    return {
        "id": manifest.package_id,
        "version": manifest.package_version,
        "task_id": manifest.task_id,
        "manifest_sha256": package.manifest_sha256,
        "active_runtimes": sorted({item.runtime_type for item in manifest.predictors}),
        "supported_runtimes": [
            {"runtime_type": runtime_type, "available": available}
            for runtime_type, available in dependencies.items()
        ],
        "predictors": [
            {
                "target": item.target,
                "runtime_type": item.runtime_type,
                "predictive_family": item.predictive_family,
            }
            for item in manifest.predictors
        ],
        "quality_report": quality.model_dump(mode="json"),
    }


def _heat_pattern_label(points: Any) -> str | None:
    if not isinstance(points, list) or not points:
        return None
    labels = [
        f"{float(point['time_s']):g}s / {float(point['temperature_c']):g}℃"
        for point in points
        if isinstance(point, dict) and point.get("time_s") is not None and point.get("temperature_c") is not None
    ]
    return " → ".join(labels) if labels else None


@router.get(
    "/api/projects/{project_id}/model-package/training-data",
    response_model=ModelTrainingDataPage,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectModelTrainingData",
)
def model_training_data(
    project_id: str,
    store: StoreDependency,
    registry: RegistryDependency,
    resolver: ResolverDependency,
    stage: Annotated[Literal["curation", "selected", "features"], Query()] = "selected",
    target: Annotated[str | None, Query()] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    project = project_or_404(store, project_id)
    resolved = resolver.resolve(project)
    package = resolved.runtime.model_package
    assert package is not None
    contract = registry.contract_for(project.task_id)
    data = resolved.runtime.data
    validate_lifecycle_metadata(
        package,
        contract,
        profile_path=getattr(data, "profile", Path(data.profile_path)),
    )
    available_targets = [item.target for item in package.manifest.predictors]
    selected_target = target or available_targets[0]
    if selected_target not in available_targets:
        raise HTTPException(status_code=422, detail=f"model package does not predict target: {selected_target}")
    canonical = canonical_training_dataset(
        project.task_id,
        data,
        contract,
        pipeline_version=package.manifest.feature_pipeline.version,
    )
    selected_rows = [row for row in canonical["rows"] if selected_target in row["outputs"]]
    predictor = next(item for item in package.manifest.predictors if item.target == selected_target)
    training_unit = predictor.config.get("training_unit", "individual_observation")
    if training_unit not in {"individual_observation", "parent_condition_mean"}:
        training_unit = "individual_observation"
    observations = {str(row["id"]): row for row in data.observations}
    output = next(item for item in contract.task_definition.outputs if item.key == selected_target)
    curation_rows = [
        (
            row,
            row.get("run_context", {}).get("curation", {}).get("status", "accepted"),
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
            if state.get("usable"):
                continue
            reason = str(state.get("reason") or "値なし")
            target_exclusion_reasons[reason] = target_exclusion_reasons.get(reason, 0) + 1
        target_curation_summaries.append({
            "target": target_key,
            "usable_rows": len(target_rows),
            "source_groups": len({str(row["parent_key"]) for row in target_rows}),
            "exclusion_reasons": dict(
                sorted(target_exclusion_reasons.items(), key=lambda item: (-item[1], item[0]))
            ),
        })
    exclusion_reasons: dict[str, int] = {}
    for row, _ in curation_rows:
        for reason in row.get("run_context", {}).get("curation", {}).get("reasons", []):
            exclusion_reasons[str(reason)] = exclusion_reasons.get(str(reason), 0) + 1
    curation_summary = {
        "source_rows": len(data.observations),
        "input_usable_rows": sum(status in {"accepted", "warning"} for _, status in curation_rows),
        "accepted_rows": sum(status == "accepted" for _, status in curation_rows),
        "warning_rows": sum(status == "warning" for _, status in curation_rows),
        "quarantined_rows": sum(status == "quarantined" for _, status in curation_rows),
        "blocked_rows": sum(status == "blocked" for _, status in curation_rows),
        "exclusion_reasons": dict(
            sorted(exclusion_reasons.items(), key=lambda item: (-item[1], item[0]))
        ),
        "targets": target_curation_summaries,
    }
    identifier_columns = [
        {"key": "observation_id", "label": "実測ID", "unit": None, "group": "識別"},
        {"key": "parent_key", "label": "親工程条件", "unit": None, "group": "識別"},
    ]
    if stage == "curation":
        curation_columns = tuple(
            resolved.runtime.data.profile.curation_recipe.columns
            if getattr(resolved.runtime.data.profile, "curation_recipe", None)
            else ()
        )
        columns = [
            *identifier_columns,
            {"key": "curation.status", "label": "採否", "unit": None, "group": "判定"},
            {"key": "curation.notes", "label": "理由・注意", "unit": None, "group": "判定"},
            {"key": "curation.transforms", "label": "適用した前処理", "unit": None, "group": "判定"},
            {"key": "curation.targets", "label": "目的変数の利用可否", "unit": None, "group": "判定"},
            *[
                {"key": f"raw.{column}", "label": column, "unit": None, "group": "原値"}
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
        source_rows = data.observations
        page_rows = []
        for observation in source_rows[offset:offset + limit]:
            curation = observation.get("run_context", {}).get("curation", {})
            values_by_column = curation.get("values", {})
            notes = [*curation.get("reasons", []), *curation.get("warnings", [])]
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
                    transforms.append(f"{column}: {raw_value or '空欄'} → {normalized_value}")
            target_states = [
                f"{target_key}: {'採用' if state.get('usable') else state.get('reason') or '不採用'}"
                for target_key, state in curation.get("target_status", {}).items()
            ]
            values: dict[str, Any] = {
                "observation_id": observation["id"],
                "parent_key": observation["parent_key"],
                "curation.status": curation.get("status", "accepted"),
                "curation.notes": " / ".join(notes) if notes else "—",
                "curation.transforms": " / ".join(transforms) if transforms else "変換なし",
                "curation.targets": " / ".join(target_states) if target_states else "—",
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
                {"key": field.path, "label": field.label, "unit": field.unit, "group": "入力"}
                for field in input_fields
            ],
            {"key": f"output.{selected_target}", "label": f"{output.label}（実測）", "unit": output.unit, "group": "実測"},
        ]
        page_rows = []
        for row in selected_rows[offset:offset + limit]:
            observation = observations[row["observation_id"]]
            process = observation.get("features") or {}
            composition = observation.get("composition") or {}
            values: dict[str, Any] = {
                "observation_id": row["observation_id"],
                "parent_key": row["parent_key"],
                f"output.{selected_target}": row["outputs"][selected_target],
            }
            for field in input_fields:
                if field.path == "heat_pattern":
                    values[field.path] = _heat_pattern_label(process.get("heat_pattern"))
                    continue
                group, key = field.path.split(".", 1)
                values[field.path] = composition.get(key) if group == "composition" else process.get(key)
            page_rows.append({
                "observation_id": row["observation_id"],
                "parent_key": row["parent_key"],
                "values": values,
            })
    else:
        model_rows = selected_rows
        feature_identifier_columns = identifier_columns
        if training_unit == "parent_condition_mean":
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in selected_rows:
                grouped.setdefault(training_context_key(row), []).append(row)
            model_rows = [
                {
                    "observation_id": context_key,
                    "parent_key": group_rows[0]["parent_key"],
                    "condition_context_id": context_key,
                    "composition_key": group_rows[0].get("composition_key"),
                    "replicate_count": len(group_rows),
                    "features": {
                        key: sum(float(row["features"][key]) for row in group_rows) / len(group_rows)
                        for key in group_rows[0]["features"]
                    },
                    "outputs": {
                        selected_target: sum(float(row["outputs"][selected_target]) for row in group_rows) / len(group_rows)
                    },
                }
                for context_key, group_rows in sorted(grouped.items())
            ]
            feature_identifier_columns = [
                {"key": "parent_key", "label": "親工程条件", "unit": None, "group": "識別"},
                {"key": "composition_key", "label": "成分キー", "unit": None, "group": "識別"},
                {"key": "replicate_count", "label": "個々値数", "unit": "件", "group": "識別"},
            ]
        columns = [
            *feature_identifier_columns,
            *[
                {
                    "key": f"feature.{feature['name']}",
                    "label": feature["name"],
                    "unit": feature["unit"],
                    "group": "特徴量",
                }
                for feature in canonical["feature_pipeline"]["features"]
            ],
            {"key": f"output.{selected_target}", "label": f"{output.label}（実測）", "unit": output.unit, "group": "実測"},
        ]
        page_rows = [
            {
                "observation_id": row["observation_id"],
                "parent_key": row["parent_key"],
                "values": {
                    "parent_key": row["parent_key"],
                    **({"observation_id": row["observation_id"]} if training_unit == "individual_observation" else {}),
                    **({"composition_key": row.get("composition_key")} if training_unit == "parent_condition_mean" else {}),
                    **({"replicate_count": row["replicate_count"]} if training_unit == "parent_condition_mean" else {}),
                    **{f"feature.{key}": value for key, value in row["features"].items()},
                    f"output.{selected_target}": row["outputs"][selected_target],
                },
            }
            for row in model_rows[offset:offset + limit]
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


@router.get(
    "/api/task-definitions",
    response_model=list[TaskCatalogItem],
    operation_id="listTaskDefinitions",
)
def task_definitions(registry: RegistryDependency) -> list[dict[str, Any]]:
    catalog = []
    for task_id in registry.task_ids:
        contract = registry.contract_for(task_id)
        canonical = contract.canonical_candidate
        catalog.append({
            "definition": registry.resolved_definition_for(task_id),
            "starter_candidate": {
                "name": "基準候補",
                "inputs": {
                    "composition": canonical.composition,
                    "process": canonical.process,
                    "categorical": canonical.categorical,
                    "heat_pattern": canonical.heat_pattern,
                },
                "provenance": {"source_kind": "direct", "source_ref": None},
            },
        })
    return catalog


@router.get(
    "/api/projects/{project_id}/task-definition",
    response_model=ResolvedTaskDefinition,
    responses=PROJECT_API_ERRORS,
    operation_id="getProjectTaskDefinition",
)
def task_definition(
    project_id: str,
    store: StoreDependency,
    registry: RegistryDependency,
) -> ResolvedTaskDefinition:
    project = project_or_404(store, project_id)
    return registry.resolved_definition_for(project.task_id)
