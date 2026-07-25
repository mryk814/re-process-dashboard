"""Build leakage-safe stage-only and end-to-end evaluation for the demo Chain."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from material_workbench.contracts.chain_evaluation_contracts import (
    ChainEvaluationFoldEvidence,
    ChainEvaluationMetricValue,
    ChainEvaluationReport,
    ChainEvaluationSplit,
    ChainEvaluationStageIdentity,
    ChainEvaluationTarget,
)
from material_workbench.contracts.chain_contracts import task_contract_surface
from material_workbench.data.stage_b_training import (
    STAGE_B_OUTPUT_AXES,
    build_stage_b_training_data,
    load_stage_b_profile,
)
from material_workbench.execution.inference_work_graph import semantic_digest
from material_workbench.modeling.model_packages import ModelPackageLoader
from material_workbench.modeling.observation_regression import load_observation_data
from material_workbench.modeling.tabular_model_builder import _fit, _predict
from material_workbench.modeling.tabular_regression import (
    build_tabular_features_from_observation,
)
from material_workbench.modeling.transform_catalog import (
    deterministic_transform_contract_digest,
)
from material_workbench.persistence.welding_chain_bootstrap import (
    welding_chain_definition,
    welding_stage_a_surface,
)
from material_workbench.tasks.task_registry import load_task_contracts


CHAIN_ID = "welding-consumable-a-b-c-v1"
STAGE_A_ID = "welding-stage-a-v1"
STAGE_B_ID = "welding-consumable-stage-b-v1"
STAGE_C_ID = "welding-stage-c-properties-v1"
FOLDS = 5


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _assignment(groups: set[str], folds: int = FOLDS) -> dict[str, int]:
    return {group: index % folds for index, group in enumerate(sorted(groups))}


def _metrics(actual: np.ndarray, predicted: np.ndarray) -> ChainEvaluationMetricValue:
    residual = actual - predicted
    return ChainEvaluationMetricValue(
        mae=float(np.mean(np.abs(residual))),
        rmse=float(np.sqrt(np.mean(residual**2))),
    )


def _stage_identity(
    stage_id: str,
    contract_id: str,
    package_root: Path,
    *,
    dataset_profile_digest: str | None,
) -> ChainEvaluationStageIdentity:
    package = ModelPackageLoader().load(package_root)
    if stage_id == "A":
        contract_digest = deterministic_transform_contract_digest(package)
    else:
        contract = load_task_contracts()[contract_id].task_definition
        contract_digest = semantic_digest(contract.model_dump(mode="json"))
    return ChainEvaluationStageIdentity(
        stage_id=stage_id,
        contract_id=contract_id,
        contract_digest=contract_digest,
        package_manifest_digest=f"sha256:{package.manifest_sha256}",
        dataset_profile_digest=dataset_profile_digest,
    )


def _upstream_predictions(
    *,
    b_rows: list[dict[str, Any]],
    b_x: np.ndarray,
    outer_test_groups: set[str],
) -> tuple[
    dict[str, dict[str, float]],
    dict[str, dict[str, float]],
    int,
    int,
]:
    """Return inner-OOF train inputs and outer-held-out test inputs for Stage C."""

    all_groups = np.asarray([str(row["parent_key"]) for row in b_rows])
    outer_test = np.asarray([group in outer_test_groups for group in all_groups])
    outer_train_groups = sorted(set(all_groups[~outer_test]))
    inner_assignment = _assignment(set(outer_train_groups), FOLDS - 1)
    train_predictions = {
        group: {} for group in outer_train_groups
    }
    test_predictions = {
        group: {} for group in sorted(set(all_groups[outer_test]))
    }
    self_fit_violations = 0
    outer_test_training_overlap = 0
    for target in STAGE_B_OUTPUT_AXES:
        target_available = np.asarray([target in row["outputs"] for row in b_rows])
        y = np.asarray([
            float(row["outputs"].get(target, 0.0)) for row in b_rows
        ])
        outer_fit_mask = (~outer_test) & target_available
        outer_test_training_overlap += len(
            set(all_groups[outer_fit_mask]) & outer_test_groups
        )
        outer_model = _fit(b_x[outer_fit_mask], y[outer_fit_mask], 2.0)
        for index in np.flatnonzero(outer_test):
            group = str(all_groups[index])
            test_predictions[group][target] = float(
                _predict(b_x[index : index + 1], outer_model)[0]
            )
        for inner_fold in range(FOLDS - 1):
            inner_test_groups = {
                group
                for group, fold in inner_assignment.items()
                if fold == inner_fold
            }
            inner_test = np.asarray([
                group in inner_test_groups for group in all_groups
            ])
            inner_fit = (~outer_test) & (~inner_test) & target_available
            self_fit_violations += len(
                set(all_groups[inner_fit]) & inner_test_groups
            )
            inner_model = _fit(b_x[inner_fit], y[inner_fit], 2.0)
            for index in np.flatnonzero(inner_test & ~outer_test):
                group = str(all_groups[index])
                train_predictions[group][target] = float(
                    _predict(b_x[index : index + 1], inner_model)[0]
                )
    if any(set(values) != set(STAGE_B_OUTPUT_AXES) for values in train_predictions.values()):
        raise ValueError("inner OOF Stage B predictions are incomplete")
    if any(set(values) != set(STAGE_B_OUTPUT_AXES) for values in test_predictions.values()):
        raise ValueError("outer test Stage B predictions are incomplete")
    return (
        train_predictions,
        test_predictions,
        self_fit_violations,
        outer_test_training_overlap,
    )


def _replace_upstream(
    canonical_inputs: dict[str, Any],
    predicted: dict[str, float],
) -> dict[str, Any]:
    values = dict(canonical_inputs)
    for component in STAGE_B_OUTPUT_AXES:
        values[f"composition.{component}"] = predicted[component]
    return values


def build_chain_evaluation(
    *,
    source: str | Path,
    stage_b_profile: str | Path,
    stage_a_package: str | Path,
    stage_b_package: str | Path,
    stage_c_package: str | Path,
) -> ChainEvaluationReport:
    source_path = Path(source)
    b_profile = load_stage_b_profile(stage_b_profile)
    b_training = build_stage_b_training_data(source_path, b_profile)
    from material_workbench.task_modules import observation_declaration

    c_data = load_observation_data(
        source_path, observation_declaration(STAGE_C_ID)
    )
    c_spec = c_data.spec
    if b_training.data.source_sha256 != c_data.source_sha256:
        raise ValueError("Stage B/C evaluation data must share one source identity")

    b_rows = [
        row for row in b_training.data.observations if row["eligible"]
    ]
    b_group_rows = {str(row["parent_key"]): row for row in b_rows}
    if len(b_group_rows) != len(b_rows):
        raise ValueError("Stage B evaluation requires one row per weld-run group")
    b_x = np.asarray([
        build_tabular_features_from_observation(
            row, b_training.data.medians, b_training.data.profile
        ).values
        for row in b_rows
    ])

    target_labels = {
        output.key: output.label
        for output in load_task_contracts()[STAGE_C_ID].task_definition.outputs
    }
    target_units = {
        output.key: output.unit
        for output in load_task_contracts()[STAGE_C_ID].task_definition.outputs
    }
    assignments: dict[str, dict[str, int]] = {}
    targets: list[ChainEvaluationTarget] = []
    evidence: list[ChainEvaluationFoldEvidence] = []
    upstream_cache: dict[
        tuple[str, ...],
        tuple[
            dict[str, dict[str, float]],
            dict[str, dict[str, float]],
            int,
            int,
        ],
    ] = {}

    for target, names in c_spec.target_features.items():
        rows = [
            row
            for row in c_data.observations
            if row["target_status"].get(target, {}).get("usable")
        ]
        groups = [str(row["parent_key"]) for row in rows]
        group_set = set(groups)
        missing_upstream = sorted(group_set - set(b_group_rows))
        if missing_upstream:
            raise ValueError(
                f"{target}: Stage B row is missing for groups {missing_upstream[:3]}"
            )
        assignment = _assignment(group_set)
        assignments[target] = assignment
        fold_ids = np.asarray([assignment[group] for group in groups])
        actual = np.asarray([float(row["outputs"][target]) for row in rows])
        stage_only_predictions = np.empty(len(rows))
        end_to_end_predictions = np.empty(len(rows))
        for outer_fold in range(FOLDS):
            test_mask = fold_ids == outer_fold
            train_mask = ~test_mask
            outer_test_groups = {
                group for group, fold in assignment.items() if fold == outer_fold
            }
            cache_key = tuple(sorted(outer_test_groups))
            upstream = upstream_cache.get(cache_key)
            if upstream is None:
                upstream = _upstream_predictions(
                    b_rows=b_rows,
                    b_x=b_x,
                    outer_test_groups=outer_test_groups,
                )
                upstream_cache[cache_key] = upstream
            (
                train_upstream,
                test_upstream,
                self_fit_violations,
                outer_test_training_overlap,
            ) = upstream

            measured_x = np.asarray([
                [c_spec.feature_values(row["canonical_inputs"])[name] for name in names]
                for row in rows
            ])
            stage_only_model = _fit(
                measured_x[train_mask], actual[train_mask], 1.0
            )
            stage_only_predictions[test_mask] = _predict(
                measured_x[test_mask], stage_only_model
            )

            end_train_x = np.asarray([
                [
                    c_spec.feature_values(
                        _replace_upstream(
                            row["canonical_inputs"],
                            train_upstream[str(row["parent_key"])],
                        )
                    )[name]
                    for name in names
                ]
                for row in np.asarray(rows, dtype=object)[train_mask]
            ])
            end_test_x = np.asarray([
                [
                    c_spec.feature_values(
                        _replace_upstream(
                            row["canonical_inputs"],
                            test_upstream[str(row["parent_key"])],
                        )
                    )[name]
                    for name in names
                ]
                for row in np.asarray(rows, dtype=object)[test_mask]
            ])
            end_model = _fit(end_train_x, actual[train_mask], 1.0)
            end_to_end_predictions[test_mask] = _predict(end_test_x, end_model)
            train_groups = group_set - outer_test_groups
            evidence.append(ChainEvaluationFoldEvidence(
                target=target,
                outer_fold=outer_fold,
                train_groups=len(train_groups),
                test_groups=len(outer_test_groups),
                test_observations=int(test_mask.sum()),
                train_group_digest=_digest(sorted(train_groups)),
                test_group_digest=_digest(sorted(outer_test_groups)),
                upstream_training_source="inner-grouped-oof",
                upstream_test_source="outer-train-only",
                upstream_training_predictions=len(train_groups),
                upstream_test_predictions=len(outer_test_groups),
                upstream_self_fit_violations=self_fit_violations,
                outer_test_training_overlap=outer_test_training_overlap,
            ))
        family = c_spec.target_family[target]
        targets.append(ChainEvaluationTarget(
            target=target,
            label=target_labels[target],
            unit=target_units[target],
            cohort=f"{family}:target-usable",
            observation_family=family,
            observations=len(rows),
            split_groups=len(group_set),
            stage_only=_metrics(actual, stage_only_predictions),
            end_to_end=_metrics(actual, end_to_end_predictions),
        ))

    stages = (
        _stage_identity(
            "A",
            STAGE_A_ID,
            Path(stage_a_package),
            dataset_profile_digest=None,
        ),
        _stage_identity(
            "B",
            STAGE_B_ID,
            Path(stage_b_package),
            dataset_profile_digest=b_training.profile_digest,
        ),
        _stage_identity(
            "C",
            STAGE_C_ID,
            Path(stage_c_package),
            dataset_profile_digest=c_data.profile_digest,
        ),
    )
    contracts = load_task_contracts()
    chain_definition = welding_chain_definition(
        welding_stage_a_surface(ModelPackageLoader().load(Path(stage_a_package))),
        task_contract_surface(
            contracts[STAGE_B_ID].task_definition,
            contract_digest=stages[1].contract_digest,
        ),
        task_contract_surface(
            contracts[STAGE_C_ID].task_definition,
            contract_digest=stages[2].contract_digest,
        ),
    )
    assignment_digest = _digest(assignments)
    return ChainEvaluationReport(
        evaluation_id="welding-consumable-a-b-c-nested-oof-v1",
        chain_id=CHAIN_ID,
        chain_definition_digest=chain_definition.digest,
        binding_digest=semantic_digest(
            [item.model_dump(mode="json") for item in chain_definition.bindings]
        ),
        unit_conversion_digest=semantic_digest(
            [
                item.conversion.model_dump(mode="json")
                for item in chain_definition.bindings
                if item.conversion is not None
            ]
        ),
        source_data_digest=f"sha256:{c_data.source_sha256}",
        stages=stages,
        split=ChainEvaluationSplit(
            strategy="nested-grouped-outer-k-fold",
            group_key="weld-run key",
            folds=FOLDS,
            assignment_policy="sorted-group-round-robin",
            assignment_digest=assignment_digest,
            assignments=assignments,
        ),
        metric_definitions={
            "mae": "同一outer test観測に対する平均絶対誤差。単位は各特性と同じ。",
            "rmse": "同一outer test観測に対する二乗平均平方根誤差。単位は各特性と同じ。",
        },
        targets=tuple(targets),
        fold_evidence=tuple(evidence),
        notes=(
            "段単体は実測溶着金属成分をCへ入力する。",
            "通し評価はouter train内のinner OOF B予測でCを学習し、outer testではouter-train B予測を使う。",
            "Aは固定科学masterによる決定論的変換であり、Stage Bのcanonical材料成分入力へ反映済み。",
            "段単体と通しは同じouter split・同じoutput別cohortで比較し、一つの精度へ合成しない。",
        ),
    )
