from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from statistics import fmean, pstdev

from decision_workbench.contracts.candidate_project_contracts import (
    CandidateInput,
    HeatPoint,
)
from decision_workbench.contracts.prediction_catalog_contracts import CandidateOriginEvidence
from decision_workbench.contracts.data_exploration_contracts import LineageCandidateOption
from decision_workbench.contracts.task_contracts import LineageReference, TaskDefinition
from decision_workbench.data.importer import WorkbookData
from decision_workbench.modeling.hot_rolling_feature_pipeline import PROCESS_NAMES


def lineage_candidate_options(data: WorkbookData, entity_key: str) -> list[LineageCandidateOption]:
    routes = tuple(
        route for route in data.relation_routes
        if entity_key in route.members.values()
    )
    process_roles = (
        ("annealing",)
        if entity_key in data.anneal_features
        else ("hot_rolling",)
        if entity_key in data.hot_rolling_features
        else ("annealing", "hot_rolling")
    )
    options: list[LineageCandidateOption] = []
    seen: set[tuple[str, str, str]] = set()
    for route in routes:
        for process_role in process_roles:
            features = (
                data.anneal_features
                if process_role == "annealing"
                else data.hot_rolling_features
            )
            process_key = route.members.get(process_role)
            if not process_key or process_key not in features:
                continue
            melt_key = route.members.get("melt")
            if not melt_key:
                process_melt_keys = {
                    candidate_route.members["melt"]
                    for candidate_route in data.relation_routes
                    if candidate_route.members.get(process_role) == process_key
                    and candidate_route.members.get("melt") in data.composition
                }
                melt_key = next(iter(process_melt_keys)) if len(process_melt_keys) == 1 else None
            if not melt_key or melt_key not in data.composition:
                continue
            identity = (process_role, process_key, melt_key)
            if identity in seen:
                continue
            seen.add(identity)
            feature = features[process_key]
            if process_role == "annealing" and len(feature.get("heat_pattern", [])) < 2:
                continue
            options.append(LineageCandidateOption(
                process_key=process_key,
                process_role=process_role,
                process_label="焼鈍条件" if process_role == "annealing" else "熱延条件",
                melt_key=melt_key,
            ))
    return sorted(
        options,
        key=lambda option: (option.process_role, option.process_key, option.melt_key),
    )


def candidate_from_lineage(
    data: WorkbookData,
    entity_key: str,
    *,
    process_key: str | None = None,
    melt_key: str | None = None,
) -> CandidateInput:
    options = lineage_candidate_options(data, entity_key)
    if process_key is not None or melt_key is not None:
        selected = next((
            option for option in options
            if option.process_key == process_key and option.melt_key == melt_key
        ), None)
        if selected is None:
            raise ValueError("選択した工程条件と成分の組み合わせを候補化できません")
    elif len(options) == 1:
        selected = options[0]
    elif options:
        raise ValueError("候補化できる上流条件が複数あります。工程条件と成分を選択してください")
    else:
        raise ValueError("候補化できる工程条件と成分の組み合わせが見つかりません")
    process_role = selected.process_role
    process_key = selected.process_key
    melt_key = selected.melt_key
    feature = data.anneal_features.get(process_key) if process_role == "annealing" else data.hot_rolling_features.get(process_key)
    if feature is None:
        raise ValueError("候補化できる工程条件が見つかりません")
    heat_pattern = None
    if process_role == "annealing":
        heat_pattern = [HeatPoint.model_validate(point) for point in deepcopy(feature["heat_pattern"])]
        if len(heat_pattern) < 2:
            raise ValueError("候補化に必要な焼鈍履歴がありません")
        process_values = (
            {"ls_mpm": float(feature["ls_mpm"])}
            if isinstance(feature.get("ls_mpm"), (int, float))
            else {}
        )
        entity_type = "annealing"
    else:
        process_values = {name: float(feature[name]) for name in PROCESS_NAMES}
        entity_type = "hot_rolling"
    direct_context_routes = [
        route
        for route in data.relation_routes
        if entity_key in route.members.values()
        and route.members.get(process_role) == process_key
        and route.members.get("melt") in {None, melt_key}
    ]
    relation_context_ids = {route.id for route in direct_context_routes}
    if not any(route.members.get("melt") == melt_key for route in direct_context_routes):
        bridge_routes = [
            route
            for route in data.relation_routes
            if route.members.get(process_role) == process_key
            and route.members.get("melt") == melt_key
        ]
        if bridge_routes:
            minimum_members = min(len(route.members) for route in bridge_routes)
            relation_context_ids.update(
                route.id
                for route in bridge_routes
                if len(route.members) == minimum_members
            )
    return CandidateInput(
        name=f"実績 {process_key} / {melt_key}",
        inputs={
            "composition": deepcopy(data.composition[melt_key]),
            "process": process_values,
            "categorical": {},
            "heat_pattern": heat_pattern,
            "heat_time_basis": (
                "elapsed_time"
                if process_role == "annealing" and "ls_mpm" not in process_values
                else "line_speed"
            ),
        },
        provenance={
            "source_kind": "lineage",
            "source_ref": {
                "entity_type": entity_type,
                "entity_key": process_key,
                "composition_entity_key": melt_key,
                "relation_context_ids": sorted(relation_context_ids),
                "data_source_digest": data.source_sha256,
            },
        },
    )


def lineage_candidate_origin_evidence(
    data: WorkbookData,
    *,
    candidate_id: str,
    task_id: str,
    reference: LineageReference,
    task_definition: TaskDefinition,
) -> CandidateOriginEvidence:
    """Aggregate only observations that created this lineage candidate."""
    if reference.data_source_digest and reference.data_source_digest != data.source_sha256:
        raise ValueError("候補化した時点の参照データと現在のデータが一致しません")
    route_ids = set(reference.relation_context_ids)
    if reference.composition_entity_key is None and not route_ids:
        raise ValueError("作成元実測を特定するための成分またはrelation経路がありません")

    observations = [
        observation
        for observation in data.observations
        if observation["task_id"] == task_id
        and observation["parent_key"] == reference.entity_key
        and (
            reference.composition_entity_key is None
            or observation.get("composition_key") == reference.composition_entity_key
        )
        and (
            not route_ids
            or bool(route_ids.intersection(observation.get("relation_context_ids", ())))
        )
    ]
    declared_measurements = {
        measurement_key
        for output in task_definition.outputs
        for measurement_key in (*output.measurement_keys, output.key, output.label)
    }
    values_by_measurement: defaultdict[str, list[float]] = defaultdict(list)
    for observation in observations:
        for measurement_key, value in observation.get("outputs", {}).items():
            if measurement_key in declared_measurements and isinstance(value, (int, float)):
                values_by_measurement[measurement_key].append(float(value))

    return CandidateOriginEvidence(
        candidate_id=candidate_id,
        task_id=task_id,
        process_key=reference.entity_key,
        composition_key=reference.composition_entity_key,
        relation_context_ids=sorted(route_ids),
        observation_ids=sorted(observation["id"] for observation in observations),
        repeat_summary={
            key: {
                "mean": fmean(values),
                "std": pstdev(values),
                "n": len(values),
            }
            for key, values in sorted(values_by_measurement.items())
        },
    )
