from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.feature_recipe_contracts import FeatureRecipe
from decision_workbench.data.profile_family_registry import lifecycle_profile_for_data
from decision_workbench.execution.inference_work_graph import semantic_digest
from decision_workbench.modeling.missingness import (
    pattern_digest,
    pattern_support_policy_document,
)
from decision_workbench.modeling.model_lifecycle import (
    QualityReport,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    runtime_capability_digest,
    staged_package_destination,
    task_input_contract_digest,
)
from decision_workbench.modeling.model_package_verify import verify_model_package
from decision_workbench.modeling.packages.contracts import (
    SourceLifecycleProvenance,
)
from decision_workbench.modeling.training.estimators import estimator_implementation
from decision_workbench.modeling.training.capacity import (
    CAPACITY_EVIDENCE_SCHEMA_VERSION,
    CAPACITY_POLICY_ID,
    CAPACITY_POLICY_VERSION,
    capacity_context_from_training_set,
)
from decision_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
    feature_vector,
)
from decision_workbench.modeling.training.feature_recipe import (
    apply_feature_recipe_to_canonical_dataset,
    canonical_recipe_inputs,
    save_feature_recipe_artifacts,
    transform_feature_recipe,
    validate_recipe_canonical_inputs,
)
from decision_workbench.modeling.training.recipe import ConcreteEstimatorRecipe
from decision_workbench.modeling.training.readiness import (
    resolve_estimator_contract_readiness,
)
from decision_workbench.modeling.training.validation_plan import ValidationPlan

CandidateBuilder = Callable[[dict[str, Any], Any], CandidateInput | None]


def _source_missing_pattern(
    observation: dict[str, Any],
    profile: Any,
) -> tuple[tuple[str, str], ...]:
    policies = (
        observation.get("run_context", {})
        .get("curation", {})
        .get("predictor_policies", {})
    )
    pattern: list[tuple[str, str]] = []
    for item in profile.inputs:
        source_state = (policies.get(item.column) or {}).get("source_state")
        if source_state == "unknown_category":
            rule = (
                None
                if profile.curation_recipe is None
                else profile.curation_recipe.columns.get(item.column)
            )
            _, key = item.path.split(".", 1)
            normalized = (observation.get("categorical") or {}).get(key)
            if (
                rule is not None
                and rule.parser == "reported_flag"
                and normalized in item.choices
            ):
                # reported_flag owns source spelling normalization.  A raw
                # alias such as "Yes (1)" is not an unknown runtime category.
                continue
        if source_state not in {
            "missing",
            "unknown_category",
            "structural_not_applicable",
            "redacted",
        }:
            continue
        kind = (
            source_state
            if source_state in {
                "unknown_category",
                "structural_not_applicable",
                "redacted",
            }
            else "not_measured"
        )
        pattern.append((item.path, kind))
    return tuple(sorted(pattern))


def _missing_pattern_evidence(
    canonical: dict[str, Any],
    data: Any,
    training_sets: dict[str, Any],
    trained_by_target: dict[str, Any],
    output_by_key: dict[str, Any],
) -> list[dict[str, Any]]:
    observations = {
        str(row["id"]): row
        for row in data.observations
    }
    patterns_by_context: dict[str, tuple[tuple[str, str], ...]] = {}
    for row in canonical["rows"]:
        observation_id = str(row["observation_id"])
        observation = observations.get(observation_id)
        if observation is None:
            continue
        context = str(row.get("condition_context_id") or observation_id)
        pattern = _source_missing_pattern(observation, data.profile)
        existing = patterns_by_context.get(context, ())
        patterns_by_context[context] = tuple(sorted(set((*existing, *pattern))))

    all_patterns = {
        patterns_by_context.get(context, ())
        for training_set in training_sets.values()
        for context in training_set.replicate_contexts
    }
    complete_metrics: dict[str, tuple[float, float]] = {}
    for target, training_set in training_sets.items():
        predictions = trained_by_target[target].evaluation_predictions
        if predictions is None:
            continue
        complete = np.asarray(
            [
                patterns_by_context.get(context, ()) == ()
                for context in training_set.replicate_contexts
            ],
            dtype=bool,
        ) & training_set.quality_rows
        finite = complete & np.isfinite(predictions)
        if not np.any(finite):
            continue
        complete_metrics[target] = (
            float(np.mean(predictions[finite])),
            _target_point_error(
                training_set.y[finite],
                predictions[finite],
                output_by_key[target].target_kind,
            )[1],
        )
    evidence: list[dict[str, Any]] = []
    for pattern in sorted(all_patterns):
        metrics_by_target: dict[str, dict[str, float | int]] = {}
        training_counts: list[int] = []
        for target, training_set in training_sets.items():
            rows = np.asarray(
                [
                    patterns_by_context.get(context, ()) == pattern
                    for context in training_set.replicate_contexts
                ],
                dtype=bool,
            )
            training_counts.append(int(rows.sum()))
            evaluated = rows & training_set.quality_rows
            target_evidence: dict[str, float | int] = {
                "evaluation_count": int(evaluated.sum()),
            }
            predictions = trained_by_target[target].evaluation_predictions
            if predictions is not None and np.any(evaluated):
                finite = evaluated & np.isfinite(predictions)
                target_evidence["prediction_failure_rate"] = float(
                    1.0 - (int(finite.sum()) / int(evaluated.sum()))
                )
                if np.any(finite):
                    metric_name, point_error = _target_point_error(
                        training_set.y[finite],
                        predictions[finite],
                        output_by_key[target].target_kind,
                    )
                    point_mean = float(np.mean(predictions[finite]))
                    target_evidence.update({
                        metric_name: point_error,
                        "point_prediction_mean": point_mean,
                    })
                    if output_by_key[target].target_kind == "binary":
                        target_evidence["calibration_error"] = float(
                            abs(
                                np.mean(training_set.y[finite])
                                - np.mean(predictions[finite])
                            )
                        )
                    if target in complete_metrics:
                        complete_mean, complete_error = complete_metrics[target]
                        target_evidence.update({
                            "point_shift_vs_complete": point_mean - complete_mean,
                            f"{metric_name}_delta_vs_complete": (
                                point_error - complete_error
                            ),
                        })
            else:
                target_evidence["prediction_failure_rate"] = (
                    1.0 if np.any(evaluated) else 0.0
                )
            metrics_by_target[target] = target_evidence
        evidence.append({
            "pattern": [
                {"path": path, "kind": kind}
                for path, kind in pattern
            ],
            "pattern_digest": pattern_digest(pattern),
            "training_count": min(training_counts, default=0),
            "evaluation_count": min(
                (
                    int(item["evaluation_count"])
                    for item in metrics_by_target.values()
                ),
                default=0,
            ),
            "metrics_by_target": metrics_by_target,
        })
    return evidence


def _target_point_error(
    observed: np.ndarray,
    predicted: np.ndarray,
    target_kind: str,
) -> tuple[str, float]:
    if target_kind == "binary":
        return "brier_score", float(np.mean((observed - predicted) ** 2))
    if target_kind == "count":
        safe_prediction = np.maximum(predicted, 1e-12)
        terms = safe_prediction.copy()
        positive = observed > 0
        terms[positive] = (
            observed[positive]
            * np.log(observed[positive] / safe_prediction[positive])
            - (observed[positive] - safe_prediction[positive])
        )
        return "mean_poisson_deviance", float(2 * np.mean(terms))
    if target_kind == "ordinal":
        return "ordinal_mae", float(np.mean(np.abs(observed - predicted)))
    return "rmse", float(np.sqrt(np.mean((observed - predicted) ** 2)))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _artifact(root: Path, path: Path) -> dict[str, object]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": _digest(path),
        "bytes": path.stat().st_size,
    }


def _representative_candidate(
    canonical: dict[str, Any],
    data: Any,
    candidate_builder: CandidateBuilder,
) -> tuple[CandidateInput, dict[str, Any]]:
    observations = {
        str(row["id"]): row
        for row in data.observations
    }
    for row in canonical["rows"]:
        observation = observations.get(str(row["observation_id"]))
        if observation is None:
            continue
        candidate = candidate_builder(observation, data)
        if candidate is not None:
            return (
                candidate.model_copy(update={"name": "standard estimator package smoke"}),
                row,
            )
    raise ValueError("no canonical training row can produce a smoke candidate")


def _build(
    *,
    task_id: str,
    data: Any,
    contract: Any,
    candidate_builder: CandidateBuilder,
    recipe: ConcreteEstimatorRecipe,
    destination: Path,
    package_id: str,
    package_version: str,
    positive_targets: frozenset[str],
    feature_recipe: FeatureRecipe | None,
    source_lifecycle: SourceLifecycleProvenance | None,
) -> None:
    canonical_paths = tuple(
        field.path
        for group in sorted(
            contract.task_definition.input_groups,
            key=lambda item: item.order,
        )
        for field in sorted(group.fields, key=lambda item: item.order)
    )
    if feature_recipe is not None:
        validate_recipe_canonical_inputs(feature_recipe, canonical_paths)
    canonical = canonical_training_dataset(task_id, data, contract)
    feature_state = (
        apply_feature_recipe_to_canonical_dataset(
            canonical,
            data,
            candidate_builder,
            feature_recipe,
        )
        if feature_recipe is not None
        else None
    )
    feature_dataset_id = canonical_training_dataset_digest(canonical)
    feature_names = tuple(
        str(item["name"])
        for item in canonical["feature_pipeline"]["features"]
    )
    profile_inputs = tuple(getattr(data.profile, "inputs", ()))
    has_explicit_missing_policy = any(
        item.numeric_missing.strategy != "reject"
        or item.categorical_missing.strategy != "reject"
        or item.unknown_category.strategy != "reject"
        for item in profile_inputs
    )
    missing_policy = canonical["feature_pipeline"].get("missing_policy")
    if missing_policy is None and has_explicit_missing_policy:
        missing_policy = {
            "imputation_values": data.feature_imputation_values,
            "digest": semantic_digest(data.feature_imputation_values),
        }
    if missing_policy:
        missing_policy = {
            **missing_policy,
            "missing_by_input": {
                item.path: sum(
                    row["run_context"]["curation"]["predictor_policies"][item.column][
                        "source_state"
                    ] == "missing"
                    for row in data.observations
                )
                for item in profile_inputs
            },
            "policy_by_input": {
                item.path: {
                    "numeric_missing": item.numeric_missing.model_dump(mode="json"),
                    "categorical_missing": item.categorical_missing.model_dump(mode="json"),
                    "unknown_category": item.unknown_category.model_dump(mode="json"),
                }
                for item in profile_inputs
            },
            **(
                {
                    "operation_capability": (
                        data.profile.missingness_operation_capability.model_dump(
                            mode="json"
                        )
                    )
                }
                if data.profile.missingness_operation_capability is not None
                else {}
            ),
        }
    artifacts_dir = destination / "model-artifacts"
    feature_dir = destination / "feature-pipeline"
    reference_dir = destination / "reference"
    smoke_dir = destination / "smoke"
    report_dir = destination / "reports"
    for folder in (
        artifacts_dir,
        feature_dir,
        reference_dir,
        smoke_dir,
        report_dir,
    ):
        folder.mkdir(parents=True, exist_ok=True)

    pipeline_path = feature_dir / "pipeline.json"
    feature_recipe_path = feature_dir / "feature-recipe.json"
    feature_state_path = feature_dir / "feature-state.json"
    if feature_recipe is not None:
        assert feature_state is not None
        save_feature_recipe_artifacts(
            feature_recipe,
            feature_state,
            feature_recipe_path,
            feature_state_path,
        )
    _write_json(
        pipeline_path,
        {
            "id": canonical["feature_pipeline"]["id"],
            "version": canonical["feature_pipeline"]["version"],
            "canonical_input_paths": list(canonical_paths),
            "features": [
                {
                    "name": item["name"],
                    "unit": item["unit"],
                    "meaning": item.get("meaning", item["name"]),
                    "group": item["group"],
                }
                for item in canonical["feature_pipeline"]["features"]
            ],
            **({"missing_policy": missing_policy} if missing_policy else {}),
            **(
                {
                    "feature_recipe": {
                        "schema_version": "feature-recipe-artifacts/v1",
                        "recipe": feature_recipe_path.relative_to(
                            destination
                        ).as_posix(),
                        "recipe_digest": feature_state.recipe_digest,
                        "state": feature_state_path.relative_to(
                            destination
                        ).as_posix(),
                        "state_digest": feature_state.state_digest,
                    }
                }
                if feature_recipe is not None and feature_state is not None
                else {}
            ),
        },
    )
    recipe_path = reference_dir / "training-recipe.json"
    recipe_document = {
        "schema_version": "model-training-recipe/v1",
        "task_id": task_id,
        "estimator": recipe.model_dump(mode="json", exclude_none=True),
        "rows": {
            "raw_observation": "source observations before replicate aggregation",
            "unit": "replicate_context_mean",
            "effective_training_row": "one row per replicate context presented to the estimator",
            "replicate_context": (
                "condition_context_id_else_observation_id"
            ),
            "validation_group": "parent_key",
        },
        "feature_dataset_id": feature_dataset_id,
    }
    _write_json(
        recipe_path,
        recipe_document,
    )

    implementation = estimator_implementation(recipe.estimator_id)
    trainer = implementation.trainer
    predictors: list[dict[str, Any]] = []
    qualities = []
    diagnostics: dict[str, Any] = {}
    files = [
        pipeline_path,
        recipe_path,
        *(
            [feature_recipe_path, feature_state_path]
            if feature_recipe is not None
            else []
        ),
    ]
    trained_by_target = {}
    training_sets = {}
    capacity_resolutions: dict[str, dict[str, Any]] = {}
    output_by_key = {
        output.key: output
        for output in contract.task_definition.outputs
    }
    if recipe.validation_plans_by_target is not None:
        unknown_validation_targets = (
            set(recipe.validation_plans_by_target) - set(output_by_key)
        )
        if unknown_validation_targets:
            raise ValueError(
                "validation plans refer to unknown targets: "
                + ", ".join(sorted(unknown_validation_targets))
            )
    for target, output in output_by_key.items():
        target_kind = output.target_kind
        if target_kind == "continuous" and target in positive_targets:
            target_kind = "continuous_positive"
        selected_validation_plan = (
            recipe.validation_plans_by_target.get(target)
            if recipe.validation_plans_by_target is not None
            and target in recipe.validation_plans_by_target
            else recipe.validation_plan
        )
        if selected_validation_plan is None and output.target_kind == "binary":
            selected_validation_plan = ValidationPlan(
                strategy="stratified_grouped_kfold",
                folds=recipe.folds,
                seed=recipe.seed,
                group_key="parent_key",
                class_balance_policy="require_each_training_fold",
            )
        training_set = compile_target_training_set(
            canonical,
            target=target,
            unit=output.unit,
            target_kind=target_kind,
            folds=recipe.folds,
            seed=recipe.seed,
            validation_plan=selected_validation_plan,
            feature_recipe=feature_recipe,
            feature_recipe_state=feature_state,
        )
        training_sets[target] = training_set
        capacity_context = (
            capacity_context_from_training_set(training_set, recipe)
            if recipe.estimator_id == "exact-gp-rbf.v1"
            else None
        )
        readiness = resolve_estimator_contract_readiness(
            estimator_id=recipe.estimator_id,
            output=output,
            validation_plan=training_set.validation_plan,
            feature_recipe=feature_recipe,
            canonical_feature_count=len(training_set.feature_names),
            smooth_term_count=len(training_set.feature_names),
            total_basis_columns=(
                len(training_set.feature_names)
                * int(getattr(recipe, "max_basis_per_feature", 1))
            ),
            maximum_categorical_levels=(
                max(
                    (
                        len(operation.choices)
                        for operation in feature_recipe.operations
                        if operation.kind == "one_hot"
                    ),
                    default=0,
                )
                if feature_recipe is not None
                else None
            ),
            has_categorical_features=any(
                group.key == "categorical" and group.fields
                for group in contract.task_definition.input_groups
            ),
            row_count=len(training_set.y),
            independent_group_count=len(set(training_set.validation_groups)),
            has_missing_features=bool(training_set.imputed_feature_indices),
            missing_policy=(
                "ready"
                if (
                    not training_set.imputed_feature_indices
                    or missing_policy is not None
                )
                else "missing"
            ),
            observed_target_min=float(np.min(training_set.y)),
            observed_targets_are_integers=bool(
                np.all(training_set.y == np.floor(training_set.y))
            ),
            capacity=capacity_context,
        )
        if readiness.capacity is not None:
            capacity_resolutions[target] = readiness.capacity.model_dump(mode="json")
        if readiness.status not in {"ready", "ready_expensive"}:
            raise ValueError(
                f"{recipe.estimator_id} is not ready for {target}: "
                + "; ".join(readiness.reasons)
            )
        artifact_path = artifacts_dir / (
            f"{target}{implementation.artifact_suffix}"
        )
        trained = trainer(training_set, recipe, artifact_path)
        predictor = dict(trained.predictor)
        predictor["artifact"] = artifact_path.relative_to(destination).as_posix()
        predictors.append(predictor)
        qualities.append(trained.quality)
        diagnostics[target] = trained.diagnostics
        trained_by_target[target] = trained
        files.append(artifact_path)

    recipe_document["evaluation"] = {
        target: {
            "cohort_digest": training_sets[target].cohort_digest,
            "raw_observation_count": training_sets[target].raw_observation_count,
            "effective_replicate_context_count": training_sets[
                target
            ].effective_replicate_context_count,
            "independent_validation_group_count": len(
                set(training_sets[target].validation_groups)
            ),
            "fold_digest": training_sets[target].fold_digest,
            "folds": training_sets[target].folds,
            "validation_plan": training_sets[target].validation_plan.model_dump(
                mode="json"
            ),
            "validation_plan_digest": training_sets[
                target
            ].validation_plan_digest,
            **(
                {"capacity_resolution": capacity_resolutions[target]}
                if target in capacity_resolutions
                else {}
            ),
        }
        for target in trained_by_target
    }
    _write_json(recipe_path, recipe_document)

    quality_path = report_dir / "quality-report.json"
    explicit_validation = (
        recipe.validation_plan is not None
        or recipe.validation_plans_by_target is not None
    )
    temporal_validation = any(
        data.validation_plan.strategy in {"temporal_holdout", "grouped_temporal"}
        for data in training_sets.values()
    )
    _write_json(
        quality_path,
        QualityReport(
            schema_version="model-quality-report/v1",
            split=(
                "typed-validation-plan"
                if explicit_validation
                else "grouped-parent-condition-k-fold"
            ),
            folds=(
                None
                if temporal_validation
                else min(
                    int(item.diagnostics["folds"])
                    for item in trained_by_target.values()
                )
            ),
            targets=tuple(qualities),
            validation_plans={
                target: {
                    **training_sets[target].validation_plan.model_dump(mode="json"),
                    "digest": training_sets[target].validation_plan_digest,
                }
                for target in trained_by_target
            },
            validation_diagnostics={
                target: training_sets[target].validation_diagnostics
                for target in trained_by_target
            },
            validation_evidence={
                target: {
                    "cohort_digest": training_sets[target].cohort_digest,
                    "fold_digest": training_sets[target].fold_digest,
                    "validation_plan_digest": training_sets[
                        target
                    ].validation_plan_digest,
                }
                for target in trained_by_target
            },
        ).model_dump(mode="json"),
    )
    diagnostics_path = report_dir / "training-diagnostics.json"
    _write_json(
        diagnostics_path,
        {
            "schema_version": "standard-estimator-training-diagnostics/v1",
            "estimator_id": recipe.estimator_id,
            "targets": diagnostics,
        },
    )
    stats_path = reference_dir / "training_stats.json"
    if missing_policy:
        missing_policy["evaluation_coverage_by_target"] = {
            target: int(
                sum(target in row["outputs"] for row in canonical["rows"])
            ) / len(canonical["rows"])
            for target in output_by_key
        }
        missing_policy["training_rows"] = len(data.observations)
        missing_policy["policy_digest"] = canonical_training_dataset_digest({
            "policy_by_input": missing_policy["policy_by_input"],
            "imputation_values": missing_policy["imputation_values"],
        })
        missing_policy["pattern_support_policy"] = (
            pattern_support_policy_document()
        )
        missing_policy["pattern_evidence"] = _missing_pattern_evidence(
            canonical,
            data,
            training_sets,
            trained_by_target,
            output_by_key,
        )
    _write_json(
        stats_path,
        {
            "records": {
                target: int(
                    sum(target in row["outputs"] for row in canonical["rows"])
                )
                for target in output_by_key
            },
            "training_contexts": {
                target: len(training_sets[target].y)
                for target in trained_by_target
            },
            "raw_observations": {
                target: training_sets[target].raw_observation_count
                for target in trained_by_target
            },
            "effective_replicate_contexts": {
                target: training_sets[target].effective_replicate_context_count
                for target in trained_by_target
            },
            "validation_groups": {
                target: len(set(training_sets[target].validation_groups))
                for target in trained_by_target
            },
            "cohort_digests": {
                target: training_sets[target].cohort_digest
                for target in trained_by_target
            },
            "fold_digests": {
                target: training_sets[target].fold_digest
                for target in trained_by_target
            },
            "validation_plan_digests": {
                target: training_sets[target].validation_plan_digest
                for target in trained_by_target
            },
            "fold_assignments": {
                target: (
                    [
                        {"validation_key": key, "fold": fold}
                        for key, fold in training_sets[target].fold_assignments
                    ]
                    if training_sets[target].is_temporal_validation
                    else dict(training_sets[target].fold_assignments)
                )
                for target in trained_by_target
            },
            "source_sha256": data.source_sha256,
            "composition_defaults": data.medians,
            "feature_dataset_id": feature_dataset_id,
            **(
                {
                    "capacity_policy": {
                        "policy_id": CAPACITY_POLICY_ID,
                        "policy_version": CAPACITY_POLICY_VERSION,
                        "resolutions": capacity_resolutions,
                    }
                }
                if capacity_resolutions
                else {}
            ),
            **({"missing_policy": missing_policy} if missing_policy else {}),
        },
    )
    files.extend((quality_path, diagnostics_path, stats_path))

    smoke_candidate, smoke_row = _representative_candidate(
        canonical,
        data,
        candidate_builder,
    )
    smoke_input = smoke_dir / "input.json"
    _write_json(smoke_input, smoke_candidate.model_dump(mode="json"))
    smoke_feature_values = dict(smoke_row["features"])
    if feature_recipe is not None:
        assert feature_state is not None
        smoke_feature_values = dict(
            zip(
                feature_names,
                transform_feature_recipe(
                    feature_recipe,
                    feature_state,
                    [canonical_recipe_inputs(smoke_candidate, feature_recipe)],
                )[0],
                strict=True,
            )
        )
    elif missing_policy:
        for name, value in missing_policy["imputation_values"].items():
            if name in smoke_feature_values and not np.isfinite(
                float(smoke_feature_values[name])
            ):
                smoke_feature_values[name] = value
    smoke_values = feature_vector(feature_names, smoke_feature_values)
    smoke_expected = smoke_dir / "expected.json"
    _write_json(
        smoke_expected,
        {
            target: round(trained.predict(smoke_values), 8)
            for target, trained in trained_by_target.items()
        },
    )
    files.extend((smoke_input, smoke_expected))

    manifest = {
        "schema_version": "model-package/v1",
        "package_id": package_id,
        "package_version": package_version,
        "task_id": task_id,
        "input_schema_version": "canonical-candidate/v1",
        "input_contract_digest": task_input_contract_digest(
            contract.task_definition
        ),
        "runtime_capability_digest": runtime_capability_digest(
            contract.runtime_capability
        ),
        "feature_pipeline": {
            "id": canonical["feature_pipeline"]["id"],
            "version": canonical["feature_pipeline"]["version"],
            "spec": pipeline_path.relative_to(destination).as_posix(),
            "canonical_input_paths": list(canonical_paths),
            "output_features": list(feature_names),
            "artifacts": [
                stats_path.relative_to(destination).as_posix(),
                recipe_path.relative_to(destination).as_posix(),
                *(
                    [
                        feature_recipe_path.relative_to(destination).as_posix(),
                        feature_state_path.relative_to(destination).as_posix(),
                    ]
                    if feature_recipe is not None
                    else []
                ),
            ],
        },
        "predictors": predictors,
        "provenance": {
            "training_data_id": canonical["source_data_digest"],
            "feature_dataset_id": feature_dataset_id,
            "training_code_revision": (
                f"standard-model-training/v1:{recipe.estimator_id}"
            ),
            "dataset_profile_id": canonical["dataset_profile_digest"],
            **(
                {
                    "capacity": {
                        "schema_version": CAPACITY_EVIDENCE_SCHEMA_VERSION,
                        "policy_id": CAPACITY_POLICY_ID,
                        "policy_version": CAPACITY_POLICY_VERSION,
                        "estimator_id": recipe.estimator_id,
                        "resolutions": capacity_resolutions,
                    }
                }
                if capacity_resolutions
                else {}
            ),
            **(
                {
                    "source_lifecycle": source_lifecycle.model_dump(
                        mode="json"
                    )
                }
                if source_lifecycle is not None
                else {}
            ),
        },
        "artifacts": [_artifact(destination, path) for path in files],
        "smoke_test": {
            "input": smoke_input.relative_to(destination).as_posix(),
            "expected": smoke_expected.relative_to(destination).as_posix(),
        },
        "quality_report": quality_path.relative_to(destination).as_posix(),
    }
    _write_json(destination / "manifest.json", manifest)


def build_standard_model_package(
    *,
    task_id: str,
    source: Path,
    data: Any,
    contract: Any,
    candidate_builder: CandidateBuilder,
    recipe: ConcreteEstimatorRecipe,
    destination: Path,
    package_id: str,
    package_version: str,
    replace: bool,
    positive_targets: frozenset[str] = frozenset(),
    feature_recipe: FeatureRecipe | None = None,
    source_lifecycle: SourceLifecycleProvenance | None = None,
) -> None:
    with staged_package_destination(destination, replace=replace) as staging:
        _build(
            task_id=task_id,
            data=data,
            contract=contract,
            candidate_builder=candidate_builder,
            recipe=recipe,
            destination=staging,
            package_id=package_id,
            package_version=package_version,
            positive_targets=positive_targets,
            feature_recipe=feature_recipe,
            source_lifecycle=source_lifecycle,
        )
        verify_model_package(
            staging,
            task_id=task_id,
            source=source,
            profile=lifecycle_profile_for_data(data),
        )
