from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np

from decision_workbench.contracts.candidate_project_contracts import CandidateInput
from decision_workbench.contracts.feature_recipe_contracts import FeatureRecipe
from decision_workbench.data.profile_family_registry import lifecycle_profile_for_data
from decision_workbench.modeling.model_lifecycle import (
    QualityReport,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    runtime_capability_digest,
    staged_package_destination,
    task_input_contract_digest,
)
from decision_workbench.modeling.model_package_verify import verify_model_package
from decision_workbench.modeling.training.estimators import estimator_implementation
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
from decision_workbench.modeling.training.recipe import (
    ConcreteEstimatorRecipe,
    validate_recipe_capability,
)

CandidateBuilder = Callable[[dict[str, Any], Any], CandidateInput | None]


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
) -> None:
    validate_recipe_capability(recipe, contract.runtime_capability)
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
    missing_policy = canonical["feature_pipeline"].get("missing_policy")
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
                for item in data.profile.inputs
            },
            "policy_by_input": {
                item.path: {
                    "numeric_missing": item.numeric_missing.model_dump(mode="json"),
                    "categorical_missing": item.categorical_missing.model_dump(mode="json"),
                    "unknown_category": item.unknown_category.model_dump(mode="json"),
                }
                for item in data.profile.inputs
            },
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
            "unit": "replicate_context_mean",
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
        training_set = compile_target_training_set(
            canonical,
            target=target,
            unit=output.unit,
            target_kind=target_kind,
            folds=recipe.folds,
            seed=recipe.seed,
            validation_plan=(
                recipe.validation_plans_by_target.get(target)
                if recipe.validation_plans_by_target is not None
                and target in recipe.validation_plans_by_target
                else recipe.validation_plan
            ),
            feature_recipe=feature_recipe,
            feature_recipe_state=feature_state,
        )
        training_sets[target] = training_set
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
            "fold_digest": training_sets[target].fold_digest,
            "folds": training_sets[target].folds,
            "validation_plan": training_sets[target].validation_plan.model_dump(
                mode="json"
            ),
            "validation_plan_digest": training_sets[
                target
            ].validation_plan_digest,
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
        )
        verify_model_package(
            staging,
            task_id=task_id,
            source=source,
            profile=lifecycle_profile_for_data(data),
        )
