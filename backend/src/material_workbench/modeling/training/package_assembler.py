from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from material_workbench.contracts.schemas import CandidateInput
from material_workbench.data.profile_document import lifecycle_profile_for_data
from material_workbench.modeling.model_lifecycle import (
    QualityReport,
    canonical_training_dataset,
    canonical_training_dataset_digest,
    runtime_capability_digest,
    staged_package_destination,
    task_input_contract_digest,
)
from material_workbench.modeling.model_package_verify import verify_model_package
from material_workbench.modeling.training.estimators import estimator_trainer
from material_workbench.modeling.training.feature_dataset import (
    compile_target_training_set,
    feature_vector,
)
from material_workbench.modeling.training.recipe import (
    ExactGPEstimatorRecipe,
    RidgeEstimatorRecipe,
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
    recipe: RidgeEstimatorRecipe | ExactGPEstimatorRecipe,
    destination: Path,
    package_id: str,
    package_version: str,
    positive_targets: frozenset[str],
) -> None:
    validate_recipe_capability(recipe, contract.runtime_capability)
    canonical = canonical_training_dataset(task_id, data, contract)
    feature_dataset_id = canonical_training_dataset_digest(canonical)
    feature_names = tuple(
        str(item["name"])
        for item in canonical["feature_pipeline"]["features"]
    )
    canonical_paths = tuple(
        field.path
        for group in sorted(
            contract.task_definition.input_groups,
            key=lambda item: item.order,
        )
        for field in sorted(group.fields, key=lambda item: item.order)
    )
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
                    "meaning": item["name"],
                    "group": item["group"],
                }
                for item in canonical["feature_pipeline"]["features"]
            ],
        },
    )
    recipe_path = reference_dir / "training-recipe.json"
    _write_json(
        recipe_path,
        {
            "schema_version": "model-training-recipe/v1",
            "task_id": task_id,
            "estimator": recipe.model_dump(mode="json"),
            "rows": {
                "unit": "replicate_context_mean",
                "replicate_context": (
                    "condition_context_id_else_observation_id"
                ),
                "validation_group": "parent_key",
            },
            "feature_dataset_id": feature_dataset_id,
        },
    )

    trainer = estimator_trainer(recipe.estimator_id)
    predictors: list[dict[str, Any]] = []
    qualities = []
    diagnostics: dict[str, Any] = {}
    files = [pipeline_path, recipe_path]
    trained_by_target = {}
    training_sets = {}
    output_by_key = {
        output.key: output
        for output in contract.task_definition.outputs
    }
    for target, output in output_by_key.items():
        training_set = compile_target_training_set(
            canonical,
            target=target,
            unit=output.unit,
            target_kind=(
                "continuous_positive"
                if target in positive_targets
                else "continuous"
            ),
        )
        training_sets[target] = training_set
        artifact_path = artifacts_dir / f"{target}.npz"
        trained = trainer(training_set, recipe, artifact_path)
        predictor = dict(trained.predictor)
        predictor["artifact"] = artifact_path.relative_to(destination).as_posix()
        predictors.append(predictor)
        qualities.append(trained.quality)
        diagnostics[target] = trained.diagnostics
        trained_by_target[target] = trained
        files.append(artifact_path)

    quality_path = report_dir / "quality-report.json"
    _write_json(
        quality_path,
        QualityReport(
            schema_version="model-quality-report/v1",
            split=(
                "leave-one-parent-condition-out"
                if recipe.estimator_id == "exact-gp-rbf.v1"
                else "grouped-parent-condition-k-fold"
            ),
            **(
                {
                    "folds": min(
                        int(item.diagnostics["folds"])
                        for item in trained_by_target.values()
                    )
                }
                if isinstance(recipe, RidgeEstimatorRecipe)
                else {}
            ),
            targets=tuple(qualities),
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
            "source_sha256": data.source_sha256,
            "composition_defaults": data.medians,
            "feature_dataset_id": feature_dataset_id,
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
    smoke_values = feature_vector(feature_names, smoke_row["features"])
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
    recipe: RidgeEstimatorRecipe | ExactGPEstimatorRecipe,
    destination: Path,
    package_id: str,
    package_version: str,
    replace: bool,
    positive_targets: frozenset[str] = frozenset(),
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
        )
        verify_model_package(
            staging,
            task_id=task_id,
            source=source,
            profile=lifecycle_profile_for_data(data),
        )
