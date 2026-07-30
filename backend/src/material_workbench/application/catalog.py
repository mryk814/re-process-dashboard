from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Literal

from material_workbench.modeling.model_lifecycle import (
    canonical_training_dataset,
    canonical_training_dataset_digest,
    validate_lifecycle_metadata,
)
from material_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
)
from material_workbench.modeling.model_packages import PREDICTOR_RUNTIME_TYPES
from material_workbench.modeling.training_distance import (
    EvidenceContextIdentity,
    evidence_context_id,
    training_context_distances,
)
from material_workbench.modeling.transform_catalog import DeterministicTransformCatalog
from material_workbench.data.importer import training_context_key
from material_workbench.contracts.schemas import (
    ModelPackageStatus,
    ModelTrainingDataPage,
    OutputSpaceEvidenceResponse,
    TaskCatalogItem,
)
from material_workbench.persistence.store import Store
from material_workbench.application.workspace_catalog_bootstrap import task_definition_digest
from material_workbench.contracts.task_contracts import ResolvedTaskDefinition
from material_workbench.tasks.task_registry import TaskRegistry, TaskRegistryError
from material_workbench.contracts.subsystem_availability import (
    SubsystemAvailability,
    SubsystemAvailabilityRegistry,
)
from material_workbench.application.project_runtime import ProjectRuntimeResolver


class CatalogUseCaseError(ValueError):
    """Base error translated to HTTP only at the API transport boundary."""


class CatalogNotFoundError(CatalogUseCaseError):
    pass


class CatalogValidationError(CatalogUseCaseError):
    pass


class CatalogConflictError(CatalogUseCaseError):
    pass


@dataclass(frozen=True)
class CatalogRuntimeState:
    resources_ready: bool
    resources_loading_error: str | None
    workspace_database: Path
    data_library_root: Path
    workspace_kind: str


def _require_project(store: Store, project_id: str) -> Any:
    project = store.get_project(project_id)
    if project is None:
        raise CatalogNotFoundError("プロジェクトが見つかりません")
    return project


def _lifecycle_profile(data: Any) -> Path | Any:
    lifecycle_profile = getattr(data, "lifecycle_profile", None)
    if lifecycle_profile is not None:
        return lifecycle_profile
    profile_path = Path(data.profile_path)
    return profile_path if profile_path.exists() else data.profile


def health(
    state: CatalogRuntimeState,
    store: Store,
    registry: TaskRegistry,
    subsystem_registry: SubsystemAvailabilityRegistry,
) -> dict[str, Any]:
    available = set(registry.available_task_ids)
    optional_subsystems = subsystem_registry.list()
    resources_ready = state.resources_ready
    resources_loading_error = state.resources_loading_error
    default_project = store.get_project("default")
    default_runtime = (
        registry.runtime_for(default_project.task_id)
        if default_project is not None and default_project.task_id in available
        else None
    )
    return {
        "ok": True,
        "ready": resources_ready,
        "resources_loading": not resources_ready and resources_loading_error is None,
        "resources_loading_error": resources_loading_error,
        "degraded": (
            len(available) != len(registry.task_ids)
            or any(item.status == "unavailable" for item in optional_subsystems)
        ),
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
        "optional_subsystems": [
            item.model_dump(mode="json") for item in optional_subsystems
        ],
        "workspace": {
            "database_path": str(state.workspace_database),
            "data_library_path": str(state.data_library_root),
            "kind": state.workspace_kind,
        },
    }


def readiness(
    state: CatalogRuntimeState,
    registry: TaskRegistry,
    subsystem_registry: SubsystemAvailabilityRegistry,
) -> dict[str, Any]:
    unavailable = {
        task_id: registry.availability_for(task_id).model_dump(mode="json")
        for task_id in registry.task_ids
        if registry.availability_for(task_id).status == "unavailable"
    }
    optional_subsystems = subsystem_registry.list()
    return {
        "ready": state.resources_ready,
        "resources_loading_error": state.resources_loading_error,
        "degraded": bool(unavailable) or any(
            item.status == "unavailable" for item in optional_subsystems
        ),
        "available_tasks": list(registry.available_task_ids),
        "unavailable_tasks": unavailable,
        "optional_subsystems": [
            item.model_dump(mode="json") for item in optional_subsystems
        ],
    }


def subsystem_availability(
    subsystem_registry: SubsystemAvailabilityRegistry,
) -> tuple[SubsystemAvailability, ...]:
    return subsystem_registry.list()


def model_package(
    project_id: str,
    store: Store,
    registry: TaskRegistry,
    resolver: ProjectRuntimeResolver,
) -> dict[str, Any]:
    project = _require_project(store, project_id)
    resolved = resolver.resolve(project)
    package = resolved.runtime.model_package
    assert package is not None
    manifest = package.manifest
    quality = validate_lifecycle_metadata(
        package,
        registry.contract_for(project.task_id),
        profile_path=_lifecycle_profile(resolved.runtime.data),
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


def model_training_data(
    project_id: str,
    store: Store,
    registry: TaskRegistry,
    resolver: ProjectRuntimeResolver,
    stage: Literal["curation", "selected", "features"] = "selected",
    target: str | None = None,
    offset: int = 0,
    limit: int = 25,
) -> dict[str, Any]:
    project = _require_project(store, project_id)
    resolved = resolver.resolve(project)
    package = resolved.runtime.model_package
    assert package is not None
    contract = registry.contract_for(project.task_id)
    data = resolved.runtime.data
    validate_lifecycle_metadata(
        package,
        contract,
        profile_path=_lifecycle_profile(data),
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
    selected_rows = [row for row in canonical["rows"] if selected_target in row["outputs"]]
    predictor = next(item for item in package.manifest.predictors if item.target == selected_target)
    training_unit = str(
        predictor.config.get("training_unit", "individual_observation")
    )
    supported_training_units = {
        "individual_observation",
        "parent_condition_mean",
        "replicate_context_mean",
        "source_row",
        "independent source row",
        "source_row_grouped_by_parent",
        "wear_measurement_row",
    }
    if training_unit not in supported_training_units:
        raise CatalogValidationError(
            f"unsupported model training unit: {training_unit}"
        )
    observations = {str(row["id"]): row for row in data.observations}
    output = next(item for item in contract.task_definition.outputs if item.key == selected_target)
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
            if not state:
                continue
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
        predictor_feature_names = set(predictor.feature_names)
        target_feature_specs = [
            feature
            for feature in canonical["feature_pipeline"]["features"]
            if feature["name"] in predictor_feature_names
        ]
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
                    "observation_ids": [
                        str(row["observation_id"]) for row in group_rows
                    ],
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
                {"key": "observation_ids", "label": "実測ID", "unit": None, "group": "識別"},
                {"key": "replicate_count", "label": "個々値数", "unit": "件", "group": "識別"},
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
            feature_identifier_columns = [
                {
                    "key": "replicate_context",
                    "label": "反復コンテキスト",
                    "unit": None,
                    "group": "識別",
                },
                {
                    "key": "validation_group",
                    "label": "検証グループ",
                    "unit": None,
                    "group": "識別",
                },
                {
                    "key": "observation_ids",
                    "label": "実測ID",
                    "unit": None,
                    "group": "識別",
                },
                {
                    "key": "replicate_count",
                    "label": "個々値数",
                    "unit": "件",
                    "group": "識別",
                },
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
                for feature in target_feature_specs
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
                    **(
                        {
                            "replicate_context": row["replicate_context"],
                            "validation_group": row["validation_group"],
                        }
                        if training_unit == "replicate_context_mean"
                        else {}
                    ),
                    **(
                        {"observation_ids": ", ".join(row["observation_ids"])}
                        if training_unit
                        in {"parent_condition_mean", "replicate_context_mean"}
                        else {}
                    ),
                    **(
                        {"replicate_count": row["replicate_count"]}
                        if training_unit
                        in {"parent_condition_mean", "replicate_context_mean"}
                        else {}
                    ),
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


def output_space_evidence(
    project_id: str,
    store: Store,
    registry: TaskRegistry,
    resolver: ProjectRuntimeResolver,
    x_target: str,
    y_target: str,
    candidate_id: str,
    expected_revision: int,
    distance_filter: Literal["supported", "caution", "all"] = "supported",
    limit: int = 200,
) -> dict[str, Any]:
    if x_target == y_target:
        raise CatalogValidationError("output space axes must be different")
    project = _require_project(store, project_id)
    candidate = store.get_candidate(candidate_id, project_id)
    if candidate is None:
        raise CatalogNotFoundError("candidate not found")
    if candidate.revision != expected_revision:
        raise CatalogConflictError("candidate revision changed")
    resolved = resolver.resolve(project)
    package = resolved.runtime.model_package
    assert package is not None
    contract = registry.contract_for(project.task_id)
    definition = registry.resolved_definition_for(project.task_id)
    prediction_space = next(
        (
            surface
            for surface in definition.application.workbench_surfaces
            if surface.kind == "prediction_space"
        ),
        None,
    )
    if (
        prediction_space is None
        or x_target not in prediction_space.target_keys
        or y_target not in prediction_space.target_keys
    ):
        raise CatalogValidationError(
            "output space axes must be declared by the task surface"
        )
    evidence_context = prediction_space.evidence_context
    available_targets = {item.target for item in package.manifest.predictors}
    if x_target not in available_targets or y_target not in available_targets:
        raise CatalogValidationError(
            "output space axes must be predicted by the model package"
        )
    data = resolved.runtime.data
    validate_lifecycle_metadata(
        package,
        contract,
        profile_path=_lifecycle_profile(data),
    )
    canonical = canonical_training_dataset(
        project.task_id,
        data,
        contract,
        pipeline_version=package.manifest.feature_pipeline.version,
    )
    points = _output_space_evidence_points(
        canonical["rows"],
        x_target=x_target,
        y_target=y_target,
        evidence_context=evidence_context,
    )
    try:
        distance_evidence = training_context_distances(
            resolved.runtime,
            candidate,
            target_keys=(x_target, y_target),
            allowed_context_ids={point["context_id"] for point in points},
            evidence_context=evidence_context,
        )
    except ValueError as exc:
        raise CatalogValidationError(str(exc)) from exc
    distance_by_context = dict(
        zip(
            distance_evidence.context_ids,
            distance_evidence.distances,
            strict=True,
        )
    )
    enriched = []
    for point in points:
        distance = float(distance_by_context[point["context_id"]])
        status = (
            "supported"
            if distance <= distance_evidence.supported_threshold
            else "caution"
            if distance <= distance_evidence.caution_threshold
            else "extrapolated"
        )
        enriched.append(
            {
                **point,
                "distance": distance,
                "distance_status": status,
            }
        )
    eligible = [
        point
        for point in enriched
        if distance_filter == "all"
        or point["distance_status"] == "supported"
        or (
            distance_filter == "caution"
            and point["distance_status"] in {"supported", "caution"}
        )
    ]
    eligible.sort(key=lambda point: (point["distance"], point["context_id"]))
    visible = eligible[:limit]
    return {
        "x_target": x_target,
        "y_target": y_target,
        "evidence_context": evidence_context,
        "source_data_digest": canonical["source_data_digest"],
        "candidate_id": candidate.id,
        "candidate_revision": candidate.revision,
        "distance_method": distance_evidence.method,
        "distance_version": distance_evidence.version,
        "cohort_digest": distance_evidence.cohort_digest,
        "supported_threshold": distance_evidence.supported_threshold,
        "caution_threshold": distance_evidence.caution_threshold,
        "filter": distance_filter,
        "eligible_contexts": len(eligible),
        "sampling_policy": "task_distance",
        "total_contexts": len(points),
        "returned_contexts": len(visible),
        "truncated": len(visible) < len(eligible),
        "points": visible,
    }


def _output_space_evidence_points(
    rows: list[dict[str, Any]],
    *,
    x_target: str,
    y_target: str,
    evidence_context: EvidenceContextIdentity = "training_context",
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if x_target not in row["outputs"] and y_target not in row["outputs"]:
            continue
        context_id = evidence_context_id(row, evidence_context)
        grouped.setdefault(context_id, []).append(row)
    points = []
    for context_id, rows in sorted(grouped.items()):
        x_observations = [
            (str(row["observation_id"]), float(row["outputs"][x_target]))
            for row in rows
            if x_target in row["outputs"]
        ]
        y_observations = [
            (str(row["observation_id"]), float(row["outputs"][y_target]))
            for row in rows
            if y_target in row["outputs"]
        ]
        if not x_observations or not y_observations:
            continue
        parent_keys = {str(row["parent_key"]) for row in rows}
        if len(parent_keys) != 1:
            raise CatalogValidationError(
                "output-space context spans multiple validation groups: "
                f"{context_id}"
            )
        x_ids = {observation_id for observation_id, _ in x_observations}
        y_ids = {observation_id for observation_id, _ in y_observations}
        relationship = (
            "same_observations"
            if x_ids == y_ids
            else "overlapping_observations"
            if x_ids & y_ids
            else "distinct_observations"
        )
        process_keys = {str(row["parent_key"]) for row in rows}
        composition_keys = {
            str(row["composition_key"])
            for row in rows
            if row.get("composition_key")
        }
        points.append({
            "context_id": context_id,
            "parent_key": next(iter(parent_keys)),
            "process_key": (
                next(iter(process_keys)) if len(process_keys) == 1 else None
            ),
            "composition_key": (
                next(iter(composition_keys))
                if len(composition_keys) == 1
                else None
            ),
            "relation_context_ids": sorted(
                {
                    str(relation_id)
                    for row in rows
                    for relation_id in row.get("relation_context_ids", [])
                }
            ),
            "pairing_relationship": relationship,
            "x": {
                "mean": fmean(value for _, value in x_observations),
                "std": pstdev(value for _, value in x_observations),
                "min": min(value for _, value in x_observations),
                "max": max(value for _, value in x_observations),
                "count": len(x_observations),
                "observation_ids": sorted(x_ids),
            },
            "y": {
                "mean": fmean(value for _, value in y_observations),
                "std": pstdev(value for _, value in y_observations),
                "min": min(value for _, value in y_observations),
                "max": max(value for _, value in y_observations),
                "count": len(y_observations),
                "observation_ids": sorted(y_ids),
            },
        })
    return points


def _sample_output_space_evidence(
    points: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    if len(points) <= limit:
        return points
    x_values = [float(point["x"]["mean"]) for point in points]
    y_values = [float(point["y"]["mean"]) for point in points]
    x_min, x_max = min(x_values), max(x_values)
    y_min, y_max = min(y_values), max(y_values)
    x_span = x_max - x_min or 1.0
    y_span = y_max - y_min or 1.0
    normalized = [
        ((x_value - x_min) / x_span, (y_value - y_min) / y_span)
        for x_value, y_value in zip(x_values, y_values, strict=True)
    ]
    selected: list[int] = []
    for index in (
        min(range(len(points)), key=lambda item: (x_values[item], points[item]["context_id"])),
        max(range(len(points)), key=lambda item: (x_values[item], points[item]["context_id"])),
        min(range(len(points)), key=lambda item: (y_values[item], points[item]["context_id"])),
        max(range(len(points)), key=lambda item: (y_values[item], points[item]["context_id"])),
    ):
        if index not in selected:
            selected.append(index)
    while len(selected) < limit:
        best_index = -1
        best_distance = -1.0
        for index, (point_x, point_y) in enumerate(normalized):
            if index in selected:
                continue
            distance = min(
                (point_x - normalized[selected_index][0]) ** 2
                + (point_y - normalized[selected_index][1]) ** 2
                for selected_index in selected
            )
            if distance > best_distance:
                best_index = index
                best_distance = distance
        selected.append(best_index)
    return [points[index] for index in sorted(selected)]


def task_definitions(
    registry: TaskRegistry,
    transform_catalog: DeterministicTransformCatalog | None,
) -> list[dict[str, Any]]:
    catalog = []
    for task_id in registry.task_ids:
        contract = registry.contract_for(task_id)
        canonical = contract.canonical_candidate
        definition = registry.resolved_definition_for(task_id)
        starter_candidate: dict[str, Any] = {
            "name": "基準候補",
            "inputs": {
                "composition": canonical.composition,
                "process": canonical.process,
                "categorical": canonical.categorical,
                "heat_pattern": canonical.heat_pattern,
            },
            "provenance": {"source_kind": "direct", "source_ref": None},
        }
        transform_id = definition.application.sparse_blend_transform_id
        if transform_id is not None and transform_catalog is not None:
            starter_candidate["blend"] = (
                transform_catalog.initial_blend(
                    transform_id
                )
            )
        catalog.append({
            "definition": definition,
            "starter_candidate": starter_candidate,
        })
    return catalog


def task_definition(
    project_id: str,
    store: Store,
    registry: TaskRegistry,
) -> ResolvedTaskDefinition:
    project = _require_project(store, project_id)
    identity = project.scientific_identity
    if identity.identity_kind == "single_task":
        return registry.resolved_definition_for(identity.task_id)
    revision = store.get_chain_revision(identity.chain_revision_id)
    if (
        revision is None
        or revision.revision_digest != identity.chain_revision_digest
    ):
        raise CatalogConflictError(
            "プロジェクトに固定されたChain Revisionを読み込めません",
        )
    task_stages = [
        stage for stage in revision.stages if stage.stage_kind == "task"
    ]
    if not task_stages:
        raise CatalogConflictError("Chainに予測Taskがありません")
    terminal_stage = task_stages[-1]
    try:
        resolved = registry.resolved_definition_for(terminal_stage.contract_id)
        current_contract_digest = task_definition_digest(
            registry,
            terminal_stage.contract_id,
        )
    except TaskRegistryError as exc:
        raise CatalogConflictError(
            "Chain終端Taskの固定contractを読み込めません",
        ) from exc
    if (
        current_contract_digest
        != terminal_stage.contract_digest
    ):
        raise CatalogConflictError(
            "Chain終端Taskのcontract digestが固定Revisionと一致しません",
        )
    return resolved


@dataclass(frozen=True)
class CatalogUseCases:
    state: CatalogRuntimeState
    store: Store
    registry: TaskRegistry
    resolver: ProjectRuntimeResolver
    subsystem_registry: SubsystemAvailabilityRegistry
    transform_catalog: DeterministicTransformCatalog | None

    def health(self) -> dict[str, Any]:
        return health(self.state, self.store, self.registry, self.subsystem_registry)

    def readiness(self) -> dict[str, Any]:
        return readiness(self.state, self.registry, self.subsystem_registry)

    def subsystem_availability(self) -> tuple[SubsystemAvailability, ...]:
        return subsystem_availability(self.subsystem_registry)

    def model_package(self, project_id: str) -> dict[str, Any]:
        return model_package(project_id, self.store, self.registry, self.resolver)

    def model_training_data(
        self,
        project_id: str,
        *,
        stage: Literal["curation", "selected", "features"],
        target: str | None,
        offset: int,
        limit: int,
    ) -> dict[str, Any]:
        return model_training_data(
            project_id,
            self.store,
            self.registry,
            self.resolver,
            stage,
            target,
            offset,
            limit,
        )

    def output_space_evidence(
        self,
        project_id: str,
        *,
        x_target: str,
        y_target: str,
        candidate_id: str,
        expected_revision: int,
        distance_filter: Literal["supported", "caution", "all"],
        limit: int,
    ) -> dict[str, Any]:
        return output_space_evidence(
            project_id,
            self.store,
            self.registry,
            self.resolver,
            x_target,
            y_target,
            candidate_id,
            expected_revision,
            distance_filter,
            limit,
        )

    def task_definitions(self) -> list[dict[str, Any]]:
        return task_definitions(self.registry, self.transform_catalog)

    def task_definition(self, project_id: str) -> ResolvedTaskDefinition:
        return task_definition(project_id, self.store, self.registry)
